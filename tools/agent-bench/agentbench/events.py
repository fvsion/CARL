"""The JSON event streams of OpenCode and Pi, and the decision of the main agent. Pure: no I/O.

OpenCode 1.18 (`opencode run --format json`), one JSON object per line:
    step_start  {"type":"step_start","timestamp":MS,"sessionID":..,"part":{"type":"step-start",..}}
    reasoning   {"type":"reasoning",..,"part":{"type":"reasoning","text":..}}        (only with --thinking)
    text        {"type":"text",..,"part":{"type":"text","text":..}}
    tool_use    {"type":"tool_use",..,"part":{"type":"tool","tool":"task","callID":..,
                 "state":{"status":"completed"|"error","input":{..},"output":..,"time":{"start":MS,"end":MS}}}}
    step_finish {"type":"step_finish",..,"part":{"reason":"tool-calls"|"stop","tokens":{"reasoning":N,..}}}
    error       {"type":"error",..,"error":{"name":"APIError","data":{"message":..}}}
A tool_use event comes when the tool has run. The log (--print-logs) has a line when a tool starts:
    ... message=evaluated permission=task pattern=coder action.permission=* action.action=allow ...

Pi 1.0 (`pi -p --mode json`, and the same events in `--mode rpc`):
    message_start / message_update / message_end {"message":{"role":"assistant","content":[
        {"type":"thinking","thinking":..}, {"type":"text","text":..},
        {"type":"toolCall","id":..,"name":"subagent","arguments":{"agent":"coder",..}}],
        "usage":{"reasoning":N,..},"stopReason":"stop"|"toolUse"|"error","errorMessage":..,"thinkingLevel":..}}
    tool_execution_start {"toolName":..,"args":{..}}, tool_execution_end {"toolName":..,"result":{..}}
    auto_retry_start / auto_retry_end {"success":false,"finalError":..}
    agent_end, agent_settled (the end of the turn)
A background subagent's result comes back as a message_end with "role":"custom" and "<subagent ...>" text.

Two measures: strict (classify: the first tool call decides) and practical (practical: the first decisive
action decides, the call to the coder or a write of the main agent; looks before it do not count).
"""
from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

JsonDict = Dict[str, Any]

DELEGATED = "delegated"
SELF = "self"
ANSWER = "answer"
STALLED = "stalled"     # the turn ended with text that announces an action, and no action (user, 2026-10-05)
ERROR = "error"
DECISIONS = (DELEGATED, SELF, ANSWER, STALLED, ERROR)                 # strict: the first tool call decides

# The practical decision: the first decisive action, after any number of looks (within the limits).
SELF_WRITE = "self-write"
UNDECIDED = "undecided"
PENDING = "pending"                                           # still running (never in a result)
PRACTICAL_DECISIONS = (DELEGATED, SELF_WRITE, ANSWER, STALLED, UNDECIDED, ERROR)
MEASURE = "practical-v2"                                     # the results of this measure (the resume key)
PREVIOUS_MEASURE = "practical-v1"                            # 8 tool calls / 240 s (the first baseline, 2026-10-05)
LEGACY_MEASURE = "first-tool"                                # result lines with no "measure" field
GATE_MARK = "[CARL] Blocked"                                # a tool result of the V5 gate: the write did not happen
BRIEF_MARK = "[CARL] Brief refused"                         # a coder call's result: CARL's brief check refused it
RESULT_KEEP = 2000                                           # the characters of a tool result that a ToolCall keeps
WRITE_RESULT_WAIT = 30.0                                     # Pi: seconds to wait for a write's result (blocked?)
MAX_TOOLS = 15                                               # tool calls before the decision is "undecided"
MAX_SECONDS = 300.0                                          # seconds from the first model step, likewise
LOOK = "look"
WRITE = "write"
WRITE_TOOLS = {"opencode": frozenset(("write", "edit", "patch", "multiedit", "apply_patch")),
               "pi": frozenset(("write", "edit"))}

CODER_NAMES = ("coder", "carl-coder")
CLIENTS = ("opencode", "pi")

_PERMISSION = re.compile(r"message=evaluated permission=(\S+) pattern=(\S+)")
_LOG_ERROR = re.compile(r"level=ERROR\b")
# a refusal: the result starts with the mark, after a client's own short label ("Error: ", OpenCode)
_REFUSAL = re.compile(r"^\s*(?:[A-Za-z][A-Za-z ]{0,40}:\s*)?" + re.escape(BRIEF_MARK))
_TEST_CMD = re.compile(r"(\bpytest\b|\bunittest\b|\bnpm (run )?test\b|\bnode --test\b|\bmake test\b|\bpython3? -m (pytest|unittest)\b)")


# ------------------------------------------------------------------------------------------------ trimming
def trim(value: Any, max_str: int = 300, max_items: int = 20, depth: int = 6) -> Any:
    """A copy of a JSON value that is small enough for a result line: long strings cut, long lists cut."""
    if isinstance(value, str):
        return value if len(value) <= max_str else value[:max_str] + f"...[{len(value) - max_str} more]"
    if depth <= 0:
        return "..."
    if isinstance(value, dict):
        out: JsonDict = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= max_items:
                out["..."] = f"{len(value) - max_items} more keys"
                break
            out[str(k)] = trim(v, max_str, max_items, depth - 1)
        return out
    if isinstance(value, list):
        items = [trim(v, max_str, max_items, depth - 1) for v in value[:max_items]]
        if len(value) > max_items:
            items.append(f"...[{len(value) - max_items} more]")
        return items
    return value


def _dict(value: Any) -> JsonDict:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


# ------------------------------------------------------------------------------------------------ the decision
def is_delegation(client: str, tool: str, args: Mapping[str, Any], coders: Sequence[str] = CODER_NAMES) -> bool:
    """A call that hands work to the coder: OpenCode's task tool with subagent_type coder, Pi's subagent tool with
    agent coder (single, or one of the parallel tasks or chain steps)."""
    names = {c.lower() for c in coders}
    if client == "opencode":
        return tool == "task" and str(args.get("subagent_type", "")).strip().lower() in names
    if client == "pi":
        if tool != "subagent":
            return False
        agents: List[Any] = [args.get("agent")]
        for key in ("tasks", "chain"):
            agents += [_dict(t).get("agent") for t in _list(args.get(key))]
        return any(isinstance(a, str) and a.strip().lower() in names for a in agents)
    raise ValueError(f"unknown client: {client}")


def is_refusal(result: Optional[str]) -> bool:
    """Is this tool result CARL's refusal of a coder brief (it starts with BRIEF_MARK)?"""
    return bool(result) and bool(_REFUSAL.match(str(result)))


def _result_text(value: Any) -> Optional[str]:
    """A tool result as text (cut to RESULT_KEEP); None when there is none."""
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text[:RESULT_KEEP]


def is_test_command(tool: str, args: Mapping[str, Any]) -> bool:
    """A shell call that runs tests (pytest, unittest, npm test, node --test, make test)."""
    if tool != "bash":
        return False
    return bool(_TEST_CMD.search(str(args.get("command", ""))))


# ------------------------------------------------------------------------------------------------ bash writes
_HEREDOC = re.compile(r"(?<!<)<<(?!<)-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
_SEPARATORS = frozenset((";", "|", "||", "&&", "&", "|&", "(", ")", ";;"))
_REDIRECTS = frozenset((">", ">>", "&>", "&>>", ">|"))
_WRAPPERS = frozenset(("sudo", "env", "command", "time", "nohup", "nice", "builtin", "exec"))
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_INPLACE = re.compile(r"^-[A-Za-z]*i")                      # sed -i, sed -Ei, perl -pi


def strip_heredocs(command: str) -> str:
    """The command without the bodies of its here-documents (their text is data, not shell)."""
    lines = command.split("\n")
    out: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        for m in _HEREDOC.finditer(line):
            delim = m.group(2)
            while i < len(lines) and lines[i].strip() != delim:
                i += 1
            i += 1
    return "\n".join(out)


def _unquote(tok: str) -> str:
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "'\"":
        return tok[1:-1]
    return tok


def shell_segments(command: str) -> List[List[str]]:
    """The simple commands of a shell line, as tokens (quotes kept on the tokens). [] when it cannot be read."""
    text = strip_heredocs(command).replace("\n", " ; ")
    lex = shlex.shlex(text, posix=False, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        tokens = list(lex)
    except ValueError:
        return []
    segs: List[List[str]] = [[]]
    for tok in tokens:
        if tok in _SEPARATORS:
            segs.append([])
        else:
            segs[-1].append(tok)
    return [s for s in segs if s]


def in_root(path: str, root: Optional[str]) -> bool:
    """Is the path in the project folder? Relative paths are; ~, $VAR and command substitutions are not known,
    so they do not count."""
    p = _unquote(path)
    if not p or p.startswith(("~", "&", "/dev/")) or "$" in p or "`" in p:
        return False
    if root is None:
        return not os.path.isabs(p)
    base = os.path.realpath(root)
    full = os.path.realpath(p if os.path.isabs(p) else os.path.join(base, p))
    return full == base or full.startswith(base.rstrip(os.sep) + os.sep)


def bash_writes(command: str, root: Optional[str] = None) -> bool:
    """Does this shell command clearly write files in the project? Conservative: only these count:
    a redirection > >> &> >| into a file of the project; tee FILE; sed -i / perl -i; mkdir, touch; cp, mv,
    install, ln, rsync into the project; patch; git apply. Not counted: writes from inside a program
    (python -c "open(...)"), paths in variables, a bare heredoc to stdout."""
    for seg in shell_segments(command):
        args: List[str] = []
        i = 0
        while i < len(seg):
            tok = seg[i]
            if tok in _REDIRECTS:
                if i + 1 < len(seg) and in_root(seg[i + 1], root):
                    return True
                i += 2
                continue
            if tok in ("<", "<<", "<<<", "<<-", ">&", "<&"):
                i += 2
                continue
            args.append(tok)
            i += 1
        while args and (_ASSIGN.match(args[0]) or os.path.basename(args[0]) in _WRAPPERS):
            args = args[1:]
        if not args:
            continue
        cmd = os.path.basename(_unquote(args[0]))
        rest = args[1:]
        plain = [a for a in rest if not a.startswith("-")]
        if cmd == "tee" and any(in_root(a, root) for a in plain):
            return True
        if cmd in ("sed", "perl") and any(_INPLACE.match(a) or a.startswith("--in-place") for a in rest):
            return True
        if cmd in ("mkdir", "touch") and any(in_root(a, root) for a in plain):
            return True
        if cmd in ("cp", "mv", "install", "ln", "rsync") and len(plain) >= 2 and in_root(plain[-1], root):
            return True
        if cmd == "patch":
            return True
        if cmd == "git" and plain[:1] == ["apply"] and not any(a in ("--check", "--stat", "--numstat")
                                                               for a in rest):
            return True
    return False


def tool_class(client: str, tool: str, args: Mapping[str, Any], root: Optional[str] = None) -> str:
    """delegated (the call to the coder), write (the main agent changes files itself) or look (any other tool:
    read, glob, grep, list, ls / cat / find / rg / tests through bash, tool_search, webfetch, todo tools, another
    subagent)."""
    if is_delegation(client, tool, args):
        return DELEGATED
    if tool in WRITE_TOOLS.get(client, frozenset()):
        return WRITE
    if tool == "bash" and bash_writes(str(args.get("command", "")), root):
        return WRITE
    return LOOK


@dataclass
class ToolCall:
    """One tool call of the main agent. at: seconds since the client started (harness clock)."""
    name: str
    args: JsonDict
    at: float
    raw: JsonDict = field(default_factory=dict)
    source: str = "event"            # "event" (the JSON stream) or "log" (OpenCode's log line only)
    call_id: str = ""
    blocked: Optional[bool] = None   # the gate stopped it (GATE_MARK in its result); None: no result yet
    result: Optional[str] = None     # its result (the output or the error, cut to RESULT_KEEP); None: no result yet


@dataclass
class Observation:
    """What a client's events say about the first turn."""
    client: str
    first_tool: Optional[ToolCall] = None
    step_tools: List[str] = field(default_factory=list)     # every tool of the first tool step, in order
    tools: List[ToolCall] = field(default_factory=list)     # every tool call of the main agent, in order
    text: str = ""
    error: str = ""
    limit: str = ""                                         # stopped at a limit while the model still worked (no tool, no text)
    model_start: Optional[float] = None                     # the first model step (harness seconds)
    thinking_tokens: int = 0
    thinking_chars: int = 0
    thinking_level: str = ""
    ended: bool = False
    step_done: bool = False                                 # the step with the first tool has finished
    log_tool: Optional[ToolCall] = None                     # OpenCode: a tool start seen in the log only
    first_event: JsonDict = field(default_factory=dict)
    events: int = 0
    turn_ended: bool = False                                # the turn ended by itself (no stop by the harness)
    last_step_end: Optional[float] = None                   # OpenCode: the time of the newest step_finish
    thinking_tokens_all: int = 0                            # every model step until the harness stopped
    log_decisive: List[ToolCall] = field(default_factory=list)   # OpenCode: decisive tool starts in the log
    coder_results: int = 0                                  # coder results that came back (full runs)
    settled: int = 0                                        # Pi: agent_settled events (full runs)
    custom_after_settle: bool = False                       # Pi: a result came back after the last settle

    def decision(self) -> str:
        return classify(self)

    def tool_seconds(self) -> Optional[float]:
        """Seconds from the first model step to the first tool call (None when there is no tool call)."""
        if self.first_tool is None:
            return None
        start = self.model_start if self.model_start is not None else 0.0
        return max(0.0, self.first_tool.at - start)


# The last sentence of a turn that only promises work: "I will start with…", "Let me look at…", "I'll now create…"
_ANNOUNCE = re.compile(r"\b(I will|I'll|I am going to|I'm going to|let me|I'm starting|I will now|I'll now|"
                       r"next,? I|I can start|starting with|first,? I)\b", re.I)


def stalled(text: str) -> bool:
    """The text ends by announcing an action (its last sentence), so a turn that ends here did not do it."""
    t = text.strip().rstrip(":").strip()
    if not t or t.endswith("?"):
        return False
    last = re.split(r"(?<=[.!])\s+", t)[-1]
    return bool(_ANNOUNCE.search(last))


def classify(obs: Observation) -> str:
    """delegated, self, answer, stalled, undecided or error: the first tool call decides; with no tool call, an error,
    a text, or the limit the model reached while it still worked."""
    if obs.first_tool is not None:
        return DELEGATED if is_delegation(obs.client, obs.first_tool.name, obs.first_tool.args) else SELF
    if obs.error:
        return ERROR
    if obs.text.strip():
        return STALLED if stalled(obs.text) else ANSWER
    if obs.limit:
        return UNDECIDED                                    # it thought to the limit and did nothing
    return ERROR


@dataclass
class Practical:
    """The practical decision: the first decisive action. looks: the tool calls before it. seconds: from the
    first model step to it. reason: why it is undecided."""
    decision: str
    tool: Optional[ToolCall] = None
    looks: int = 0
    seconds: Optional[float] = None
    reason: str = ""


def practical(obs: Observation, root: Optional[str] = None, now: Optional[float] = None,
              max_tools: int = MAX_TOOLS, max_seconds: Optional[float] = MAX_SECONDS,
              log_grace: float = 10.0) -> Practical:
    """The first decisive action of the main agent: delegated, self-write; else answer (the turn ended with no
    decisive action), undecided (max_tools tool calls, or max_seconds from the first model step) or error.
    now: the harness clock while the client runs (then the result can be PENDING); None: the final result."""
    start = obs.model_start if obs.model_start is not None else 0.0
    looks = 0
    for call in obs.tools:
        if max_seconds is not None and call.at - start > max_seconds:
            return Practical(UNDECIDED, None, looks, None, f"{max_seconds:.0f} s")
        cls = tool_class(obs.client, call.name, call.args, root)
        if cls not in (LOOK, DELEGATED) and call.blocked is None and now is not None \
                and now - call.at < WRITE_RESULT_WAIT:
            return Practical(PENDING, None, looks)       # Pi: a write's result says whether the gate stopped it
        if cls != LOOK and not call.blocked:
            return Practical(DELEGATED if cls == DELEGATED else SELF_WRITE, call, looks, max(0.0, call.at - start))
        looks += 1                                       # a look, or a write the gate stopped: no decision
        if looks >= max_tools:
            return Practical(UNDECIDED, None, looks, None, f"{max_tools} tool calls")
    last_event = max((c.at for c in obs.tools), default=None)
    for call in obs.log_decisive:                       # OpenCode: a tool started; its event has not come yet
        if last_event is not None and last_event >= call.at:
            continue                                     # its event came (and was not decisive: the gate)
        if now is None or now - call.at >= log_grace:
            cls = tool_class(obs.client, call.name, call.args, root)
            if cls != LOOK:
                return Practical(DELEGATED if cls == DELEGATED else SELF_WRITE, call, looks,
                                 max(0.0, call.at - start))
    if obs.error:
        return Practical(ERROR, None, looks)
    if obs.turn_ended:
        return Practical(STALLED if stalled(obs.text) else ANSWER, None, looks)
    if now is not None:
        if max_seconds is not None and obs.model_start is not None and now - start > max_seconds:
            return Practical(UNDECIDED, None, looks, None, f"{max_seconds:.0f} s")
        return Practical(PENDING, None, looks)
    return Practical(UNDECIDED, None, looks, None, "stopped before a decision")


def brief_pending(obs: Observation, now: float, max_tools: int = MAX_TOOLS,
                  max_seconds: Optional[float] = MAX_SECONDS, wait: float = WRITE_RESULT_WAIT) -> bool:
    """A decision run after the decision to delegate: does it go on for the coder's brief? Yes while the newest call
    to the coder was refused by CARL's brief check (the main agent then sends the brief again) or its result has not
    come yet (Pi: at most `wait` s). No when the turn ended, after an error, after max_tools tool calls from the first
    coder call, or max_seconds from the first model step. The decision itself stays the first coder call."""
    coder = [i for i, c in enumerate(obs.tools) if is_delegation(obs.client, c.name, c.args)]
    if not coder or obs.error or obs.turn_ended:
        return False
    if len(obs.tools) - coder[0] > max_tools:
        return False
    start = obs.model_start if obs.model_start is not None else 0.0
    if max_seconds is not None and now - start > max_seconds:
        return False
    last = obs.tools[coder[-1]]
    if last.result is None:
        return now - last.at < wait
    return is_refusal(last.result)


def expected_ok(expect: str, decision: str) -> Optional[bool]:
    """Is the decision right? expect: delegate (large, stuck) or keep (small, question). None for an error.
    Works for the strict and the practical decisions: for a large or stuck request only "delegated" is right;
    for a small request or a question everything but "delegated" is right (undecided too: no hand-off)."""
    if decision == ERROR:
        return None
    if decision == STALLED:                              # it said it would act and did not: never right
        return False
    if expect == "delegate":
        return decision == DELEGATED
    if expect == "keep":
        return decision in (SELF, ANSWER, SELF_WRITE, UNDECIDED)
    raise ValueError(f"unknown expectation: {expect}")


# ------------------------------------------------------------------------------------------------ parsers
class OpenCodeParser:
    """Feeds `opencode run --format json` events (stdout) and its log lines (stderr) into an Observation."""

    client = "opencode"

    def __init__(self) -> None:
        self.obs = Observation(self.client)
        self._log_errors: List[str] = []

    def feed(self, ev: Mapping[str, Any], at: float) -> None:
        o = self.obs
        o.events += 1
        kind = ev.get("type")
        part = _dict(ev.get("part"))
        if kind == "step_start":
            if o.model_start is None:
                o.model_start = at
        elif kind == "reasoning":
            o.thinking_chars += len(str(part.get("text", "")))
        elif kind == "text":
            o.text += str(part.get("text", ""))
            if not o.first_event and o.first_tool is None:
                o.first_event = dict(ev)
        elif kind == "tool_use":
            state = _dict(part.get("state"))
            blocked = state.get("status") == "error" and GATE_MARK in json.dumps(state.get("error", ""))
            call = ToolCall(str(part.get("tool", "")), dict(_dict(state.get("input"))), at, dict(ev),
                            call_id=str(part.get("callID", "")), blocked=blocked,
                            result=_result_text(state.get("output") if state.get("status") == "completed"
                                                else state.get("error")))
            o.tools.append(call)
            if o.first_tool is None:
                o.first_tool = call
                o.first_event = dict(ev)
                o.step_tools = [call.name]
            elif not o.step_done:
                o.step_tools.append(call.name)
        elif kind == "step_finish":
            tokens = _dict(part.get("tokens"))
            if not o.step_done:
                o.thinking_tokens += _int(tokens.get("reasoning"))
            o.thinking_tokens_all += _int(tokens.get("reasoning"))
            o.last_step_end = at
            if part.get("reason") == "stop":
                o.turn_ended = True                          # the turn ended: text, no more tool calls
                if o.first_tool is None:
                    o.ended = True
            if o.first_tool is not None:
                o.step_done = True
        elif kind == "error":
            err = _dict(ev.get("error"))
            data = _dict(err.get("data"))
            o.error = str(data.get("message") or err.get("name") or "error")
            if not o.first_event:
                o.first_event = dict(ev)

    def feed_log(self, line: str, at: float) -> None:
        """A log line (stderr with --print-logs): a tool that starts, or an error."""
        m = _PERMISSION.search(line)
        if m:
            perm, pattern = m.group(1), m.group(2)
            args: JsonDict = {"subagent_type": pattern} if perm == "task" else {"pattern": pattern}
            call = ToolCall(perm, args, at, {"log": line.strip()[:500]}, source="log")
            if self.obs.first_tool is None and self.obs.log_tool is None:
                self.obs.log_tool = call
            # a start of a decisive tool (the coder; an edit: the permission of write, edit and patch)
            if (perm == "task" and is_delegation("opencode", "task", args)) or perm == "edit":
                self.obs.log_decisive.append(ToolCall("task" if perm == "task" else "edit", args, at,
                                                      call.raw, source="log"))
        if _LOG_ERROR.search(line):
            self._log_errors.append(line.strip()[:500])

    def end(self) -> None:
        self.obs.ended = True

    def promote_log_tool(self) -> None:
        """Use the log's tool start as the first tool call (its tool_use event did not come in time)."""
        o = self.obs
        if o.first_tool is None and o.log_tool is not None:
            o.first_tool = o.log_tool
            o.step_tools = [o.log_tool.name]
            o.first_event = dict(o.log_tool.raw)

    def log_errors(self) -> List[str]:
        return list(self._log_errors)


class PiParser:
    """Feeds Pi's JSON events (`pi -p --mode json`, or `--mode rpc`) into an Observation."""

    client = "pi"

    def __init__(self) -> None:
        self.obs = Observation(self.client)
        self._last_error = ""
        self._retry_failed = ""

    def feed(self, ev: Mapping[str, Any], at: float) -> None:
        o = self.obs
        o.events += 1
        kind = ev.get("type")
        msg = _dict(ev.get("message"))
        role = msg.get("role")
        if kind == "message_start" and role == "assistant" and o.model_start is None:
            o.model_start = at
        elif kind == "message_end" and role == "assistant":
            self._assistant_end(msg, ev, at)
        elif kind == "message_end" and role == "custom":
            text = _content_text(msg.get("content"))
            if "<subagent" in text and re.search(r'agent="(coder|carl-coder)"', text):
                o.coder_results += 1
                o.custom_after_settle = True
        if kind == "tool_execution_end":                 # a call's result: did the gate stop it?
            cid = str(ev.get("toolCallId", ""))
            text = _content_text(_dict(ev.get("result")).get("content"))
            for c in o.tools:
                if c.call_id and c.call_id == cid:
                    c.blocked = GATE_MARK in text
                    c.result = text[:RESULT_KEEP]
        if kind == "tool_execution_end" and ev.get("toolName") == "subagent":
            args = _dict(_dict(ev.get("result")).get("details"))
            results = _list(args.get("results"))
            if results and any(is_delegation("pi", "subagent", _dict(r)) for r in results):
                o.coder_results += 1                         # a foreground coder run: its result is here
        elif kind == "auto_retry_end":
            self._retry_failed = "" if ev.get("success") else str(ev.get("finalError") or "retries failed")
        elif kind == "agent_settled":
            o.settled += 1
            o.turn_ended = True
            o.custom_after_settle = False

    def _assistant_end(self, msg: JsonDict, ev: Mapping[str, Any], at: float) -> None:
        o = self.obs
        usage = _dict(msg.get("usage"))
        level = msg.get("thinkingLevel")
        if isinstance(level, str) and level:
            o.thinking_level = level
        if msg.get("stopReason") == "error":
            self._last_error = str(msg.get("errorMessage") or "error")
            return
        self._last_error = ""
        before_tool = o.first_tool is None
        o.thinking_tokens_all += _int(usage.get("reasoning"))
        if before_tool:
            o.thinking_tokens += _int(usage.get("reasoning"))
        calls: List[ToolCall] = []
        for c in _list(msg.get("content")):
            c = _dict(c)
            if c.get("type") == "thinking" and before_tool:
                o.thinking_chars += len(str(c.get("thinking", "")))
            elif c.get("type") == "text":
                o.text += str(c.get("text", ""))
            elif c.get("type") == "toolCall":
                calls.append(ToolCall(str(c.get("name", "")), dict(_dict(c.get("arguments"))), at, dict(ev),
                                      call_id=str(c.get("id", ""))))
        o.tools += calls
        if calls and before_tool:
            o.first_tool = calls[0]
            o.step_tools = [c.name for c in calls]
            o.first_event = dict(ev)
            o.step_done = True
        elif not o.first_event and before_tool and o.text:
            o.first_event = dict(ev)

    def end(self) -> None:
        o = self.obs
        o.ended = True
        if o.first_tool is None and not o.text.strip():
            o.error = self._retry_failed or self._last_error or o.error
        elif self._retry_failed and o.first_tool is None:
            o.error = self._retry_failed


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(str(_dict(p).get("text", "")) for p in _list(content))


def parse_line(line: str) -> Optional[JsonDict]:
    """One line of a JSON event stream, or None when it is not a JSON object."""
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        value = json.loads(line)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


# ------------------------------------------------------------------------------------------------ full runs
@dataclass
class FullFacts:
    """The facts of a full run (a task to the end)."""
    delegated: bool = False            # the main agent called the coder (at any time)
    coder_calls: int = 0
    coder_results: int = 0             # results of the coder that came back to the main agent
    tests_run: bool = False            # the main agent ran the tests after the first coder result (or at all)
    tools: List[str] = field(default_factory=list)


def full_facts(client: str, tools: Sequence[ToolCall], coder_results: int,
               first_result_at: Optional[float] = None) -> FullFacts:
    """Summarize the main agent's tool calls of a full run."""
    facts = FullFacts(tools=[t.name for t in tools], coder_results=coder_results)
    for t in tools:
        if is_delegation(client, t.name, t.args):
            facts.delegated = True
            facts.coder_calls += 1
    after = [t for t in tools if first_result_at is None or t.at >= first_result_at]
    facts.tests_run = any(is_test_command(t.name, t.args) for t in after)
    return facts


@dataclass
class CoderWork:
    """What the coder itself did in a full run: its writes (write, edit, patch, a shell write) and its test runs.
    stalled: it was called and came back, but did neither (it "delegated" or only talked: the Gemma 4 E4B in
    OpenCode, 2026-10-06, before the fix that keeps the delegation rule from subagents)."""
    writes: int = 0
    test_runs: int = 0

    def stalled(self, coder_calls: int, coder_results: int) -> bool:
        return coder_calls > 0 and coder_results > 0 and self.writes == 0 and self.test_runs == 0


def coder_work(client: str, tools: Sequence[ToolCall], root: Optional[str] = None) -> CoderWork:
    """Count the coder's writes and test runs from its own tool calls."""
    w = CoderWork()
    for t in tools:
        if tool_class(client, t.name, t.args, root) == WRITE:
            w.writes += 1
        if is_test_command(t.name, t.args):
            w.test_runs += 1
    return w


def opencode_messages_facts(messages: Sequence[Mapping[str, Any]]) -> Tuple[List[ToolCall], int, Optional[int]]:
    """The main session's tool calls, the coder results and the index of the first result, from the messages
    of `opencode serve` (GET /session/ID/message). The tool calls' "at" is their message index."""
    tools: List[ToolCall] = []
    results = 0
    first_result: Optional[int] = None
    for i, m in enumerate(messages):
        info = _dict(m.get("info"))
        for p in _list(m.get("parts")):
            p = _dict(p)
            if p.get("type") == "tool":
                state = _dict(p.get("state"))
                call = ToolCall(str(p.get("tool", "")), dict(_dict(state.get("input"))), float(i), trim(p),
                                result=_result_text(state.get("output") if state.get("status") == "completed"
                                                    else state.get("error")))
                tools.append(call)
                meta = _dict(state.get("metadata"))
                if (call.name == "task" and is_delegation("opencode", "task", call.args)
                        and state.get("status") == "completed" and not meta.get("background")):
                    results += 1                              # a foreground coder run: its result is here
                    first_result = i if first_result is None else first_result
            elif p.get("type") == "text" and info.get("role") == "user":
                text = str(p.get("text", ""))
                if text.lstrip().startswith("<task id=") and 'state="completed"' in text[:200]:
                    results += 1
                    first_result = i if first_result is None else first_result
    return tools, results, first_result
