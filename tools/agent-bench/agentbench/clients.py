"""The client adapters: OpenCode and Pi in the harness HOME. A decision run stops the client at its first tool
call; a full run lets the task go to its end (OpenCode through `opencode serve`, Pi through `pi --mode rpc`, so
that a background coder can finish and its result can come back)."""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple, Union

from . import events as ev, fixtures
from .proc import Child, run

PROVIDER = "llamacpp"
OPENCODE_EXTRA_ENV = {
    "OPENCODE_DISABLE_AUTOUPDATE": "1",      # the same client version for every run
    "OPENCODE_DISABLE_MODELS_FETCH": "1",    # no models.dev download at start (the provider is CARL's)
}
_LEVEL = re.compile(r"^[a-z]{2,10}$")
Parser = Union[ev.OpenCodeParser, ev.PiParser]


# ------------------------------------------------------------------------------------------------ environment
def read_env_file(path: str) -> Dict[str, str]:
    """The `export KEY=value` lines of a shell file (CARL's ~/.config/carl/opencode.env). Nothing is evaluated."""
    out: Dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            words = shlex.split(line, comments=True)
        except ValueError:
            continue
        if words and words[0] == "export":
            words = words[1:]
        for w in words:
            k, sep, v = w.partition("=")
            if sep and re.fullmatch(r"[A-Z_][A-Z0-9_]*", k):
                out[k] = v
    return out


def client_env(home: str, base: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """The environment of OpenCode and Pi: the harness HOME, its ~/.local/bin first, the switches that CARL's
    installer wrote to ~/.config/carl/opencode.env (OpenCode reads them from the environment only)."""
    base = os.environ if base is None else base
    env = {k: base[k] for k in ("LANG", "LC_ALL", "TMPDIR", "USER", "LOGNAME", "SHELL") if k in base}
    env.update(HOME=home, TERM="dumb", NO_COLOR="1", PATH=":".join([
        os.path.join(home, ".local", "bin"), os.path.join(home, ".local", "lib", "nodejs", "bin"),
        "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]))
    env.update(read_env_file(os.path.join(home, ".config", "carl", "opencode.env")))
    env.update(OPENCODE_EXTRA_ENV)
    return env


def client_version(client: str, env: Mapping[str, str], cwd: str) -> str:
    code, out = run([client, "--version"], cwd, env, 60)
    words = out.strip().split()
    return words[-1] if code == 0 and words else "unknown"


def clear_stale_locks(home: str, hostname: Optional[str] = None) -> List[str]:
    """Remove OpenCode's lock folders (~/.local/state/opencode/locks/*.lock) of processes that no longer run on
    this computer. A stopped decision run leaves its lock, and the next OpenCode start waits about 60 s for it.
    Only in the harness HOME; a lock of a running process or of another computer stays."""
    folder = os.path.join(home, ".local", "state", "opencode", "locks")
    host = hostname or socket.gethostname()
    removed: List[str] = []
    try:
        names = os.listdir(folder)
    except OSError:
        return removed
    for name in names:
        lock = os.path.join(folder, name)
        if not (name.endswith(".lock") and os.path.isdir(lock) and not os.path.islink(lock)):
            continue
        try:
            with open(os.path.join(lock, "meta.json"), encoding="utf-8") as f:
                meta = json.load(f)
            pid, owner = int(meta["pid"]), str(meta.get("hostname", ""))
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if owner != host or pid <= 1 or _alive(pid):
            continue
        shutil.rmtree(lock, ignore_errors=True)
        removed.append(name)
    return removed


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# ------------------------------------------------------------------------------------------------ thinking
def check_level(thinking: str) -> str:
    if not _LEVEL.match(thinking):
        raise ValueError(f"bad thinking level {thinking!r}")
    return thinking


def opencode_variant(thinking: str) -> Optional[str]:
    """OpenCode's --variant: none for off; nothing for default (the model's own: high in CARL's config)."""
    if thinking == "default":
        return None
    return "none" if thinking == "off" else check_level(thinking)


def opencode_default_level(home: str, model: str, provider: str = PROVIDER) -> str:
    """The reasoningEffort that CARL wrote for the model in opencode.json (what "default" uses)."""
    try:
        with open(os.path.join(home, ".config", "opencode", "opencode.json"), encoding="utf-8") as f:
            doc = json.load(f)
        effort = doc["provider"][provider]["models"][model]["options"]["reasoningEffort"]
        return str(effort)
    except (OSError, ValueError, KeyError, TypeError):
        return "default"


def pi_level(thinking: str, home: str) -> str:
    """Pi's thinking level: off, the level given, or for default the defaultThinkingLevel of settings.json."""
    if thinking == "off":
        return "off"
    if thinking != "default":
        return check_level(thinking)
    try:
        with open(os.path.join(home, ".pi", "agent", "settings.json"), encoding="utf-8") as f:
            level = json.load(f).get("defaultThinkingLevel")
        if isinstance(level, str) and _LEVEL.match(level):
            return level
    except (OSError, ValueError, AttributeError):
        pass
    return "low"


# ------------------------------------------------------------------------------------------------ commands
@dataclass(frozen=True)
class RunSpec:
    client: str
    model: str
    thinking: str
    prompt: str
    cwd: str
    home: str
    limit: float
    provider: str = PROVIDER


def decision_argv(spec: RunSpec) -> List[str]:
    """The command of a decision run (headless, JSON events)."""
    if spec.client == "opencode":
        argv = ["opencode", "run", "--format", "json", "--thinking", "--print-logs", "--log-level", "INFO",
                "-m", f"{spec.provider}/{spec.model}"]
        variant = opencode_variant(spec.thinking)
        if variant:
            argv += ["--variant", variant]
        return argv + [spec.prompt]
    if spec.client == "pi":
        return ["pi", "-p", "--mode", "json", "--model",
                f"{spec.provider}/{spec.model}:{pi_level(spec.thinking, spec.home)}", "--", spec.prompt]
    raise ValueError(f"unknown client {spec.client!r}")


def make_parser(client: str) -> Parser:
    if client == "opencode":
        return ev.OpenCodeParser()
    if client == "pi":
        return ev.PiParser()
    raise ValueError(f"unknown client {client!r}")


# ------------------------------------------------------------------------------------------------ decision runs
@dataclass
class Outcome:
    obs: ev.Observation
    seconds: float
    returncode: Optional[int]
    stderr: str
    thinking_level: str
    timed_out: bool = False
    full: Dict[str, Any] = field(default_factory=dict)
    practical: ev.Practical = field(default_factory=lambda: ev.Practical(ev.UNDECIDED))

    @property
    def decision(self) -> str:
        """The strict decision: the first tool call."""
        return ev.classify(self.obs)


@dataclass(frozen=True)
class Limits:
    """When a decision run stops without a decisive action: after max_tools tool calls, or max_seconds from the
    first model step (the practical decision is then "undecided")."""
    max_tools: int = ev.MAX_TOOLS
    max_seconds: float = ev.MAX_SECONDS
    log_grace: float = 10.0          # OpenCode: a tool start in the log counts when its event is this late
    step_grace: float = 3.0          # OpenCode: after the decisive tool, wait this long for its step's end


def finished(parser: Parser, prac: ev.Practical, now: float, limits: Limits) -> bool:
    """Can the client stop? When the practical decision is known. OpenCode: a decisive tool's step must end too
    (its other tools and its thinking tokens come with it), or step_grace seconds pass. After a call to the coder:
    not while CARL's brief check refused its newest call (events.brief_pending: the briefs of a run, Phase 23.4.3)."""
    if prac.decision == ev.PENDING:
        return False
    if prac.decision == ev.DELEGATED and ev.brief_pending(parser.obs, now, limits.max_tools, limits.max_seconds):
        return False                                # a refused brief: the main agent sends it again
    if isinstance(parser, ev.OpenCodeParser) and prac.tool is not None and prac.tool.source == "event":
        end = parser.obs.last_step_end
        return (end is not None and end >= prac.tool.at) or now - prac.tool.at >= limits.step_grace
    return True


def run_decision(spec: RunSpec, env: Mapping[str, str], limits: Limits = Limits(),
                 child_factory: Callable[..., Child] = Child,
                 record: Optional[List[Tuple[str, str]]] = None) -> Outcome:
    """Start the client and read its events past the looks (read, grep, ls ...) until the first decisive action
    (the coder, or a write of the main agent), the end of the turn, an error or a limit; then stop it. The first
    tool call (the strict decision) is recorded on the way. record: every line read, as (stream, text)."""
    parser = make_parser(spec.client)
    if spec.client == "opencode":
        clear_stale_locks(spec.home)
    child = child_factory(decision_argv(spec), spec.cwd, env)
    timed_out = closed = False
    while True:
        item = child.next_line(0.2)
        now = child.elapsed()
        if item is not None:
            stream, text, at = item
            if record is not None and stream in ("out", "err"):
                record.append((stream, text))
            if stream == "out":
                doc = ev.parse_line(text)
                if doc is not None:
                    parser.feed(doc, at)
            elif stream == "err" and isinstance(parser, ev.OpenCodeParser):
                parser.feed_log(text, at)
        now_p = ev.practical(parser.obs, spec.cwd, now, limits.max_tools, limits.max_seconds, limits.log_grace)
        if finished(parser, now_p, now, limits):
            break
        if not child.streams_open():
            closed = True
            break
        if now > spec.limit:
            timed_out = True
            break
    seconds = child.elapsed()
    rc = child.stop()
    if isinstance(parser, ev.OpenCodeParser):
        parser.promote_log_tool()
    parser.end()
    o = parser.obs
    stderr = child.err_text()
    if o.first_tool is None and not o.error and not o.text.strip():
        if o.model_start is not None and not closed:
            # the model worked (thinking) to a limit with no tool call and no text: undecided, not an error
            o.limit = (now_p.reason or f"{spec.limit:.0f} s") if not timed_out else f"{spec.limit:.0f} s"
        elif timed_out:
            o.error = f"time limit ({spec.limit:.0f} s): no tool call and no end of the turn"
        else:
            o.error = f"the client ended (code {rc}) with no tool call and no text"
    if o.first_tool is None and o.error and isinstance(parser, ev.OpenCodeParser):
        errs = parser.log_errors()
        if errs and errs[-1] not in o.error:
            o.error += " | log: " + errs[-1]
    level = thinking_level(spec, o)
    prac = ev.practical(o, spec.cwd, None, limits.max_tools, limits.max_seconds, limits.log_grace)
    if timed_out and prac.decision == ev.UNDECIDED and o.model_start is None:
        prac = ev.Practical(ev.ERROR, None, prac.looks)
    return Outcome(o, seconds, rc, stderr, level, timed_out, practical=prac)


def thinking_level(spec: RunSpec, obs: ev.Observation) -> str:
    if spec.client == "pi":
        return obs.thinking_level or pi_level(spec.thinking, spec.home)
    variant = opencode_variant(spec.thinking)
    return variant or opencode_default_level(spec.home, spec.model, spec.provider)


# ------------------------------------------------------------------------------------------------ full runs: Pi
_BG_ID = re.compile(r"\bbg-\d+\b")
_CODER_TITLE = re.compile(r"\(@(carl-)?coder subagent\)")      # OpenCode's title of a coder session


def run_full_pi(spec: RunSpec, env: Mapping[str, str], settle_wait: float = 60.0) -> Outcome:
    """Pi in RPC mode: send the task, wait until the turn settles and every background coder has come back
    (its result arrives as a message and starts a turn), or the time limit."""
    parser = ev.PiParser()
    argv = ["pi", "--mode", "rpc", "--model", f"{spec.provider}/{spec.model}:{pi_level(spec.thinking, spec.home)}"]
    child = Child(argv, spec.cwd, env, stdin=True)
    child.send(json.dumps({"id": "bench-1", "type": "prompt", "message": spec.prompt}))
    started: Set[str] = set()
    back: Set[str] = set()
    first_result_at: Optional[float] = None
    at_result: Optional[List[str]] = None
    last_settle = -1.0
    timed_out = False
    while True:
        item = child.next_line(0.5)
        now = child.elapsed()
        if item is not None and item[0] == "out":
            doc = ev.parse_line(item[1])
            if doc is not None:
                before = parser.obs.coder_results
                parser.feed(doc, item[2])
                if parser.obs.coder_results > before and first_result_at is None:
                    first_result_at = item[2]
                    at_result = fixtures.changed_files(spec.cwd)     # what the coder left (it runs in its own process)
                kind = doc.get("type")
                if kind == "tool_execution_end" and doc.get("toolName") == "subagent":
                    started |= set(_BG_ID.findall(json.dumps(doc.get("result", ""))))
                elif kind == "message_end" and (doc.get("message") or {}).get("role") == "custom":
                    back |= set(_BG_ID.findall(json.dumps(doc.get("message", ""))[:400]))
                elif kind == "agent_settled":
                    last_settle = now
        o = parser.obs
        if o.settled > 0 and not (started - back) and not o.custom_after_settle and now - last_settle >= 1.0:
            break
        if o.custom_after_settle and not (started - back) and last_settle >= 0 and now - last_settle > settle_wait:
            break                                   # the result came back and started no turn
        if not child.streams_open() or not child.running():
            break
        if now > spec.limit:
            timed_out = True
            break
    seconds = child.elapsed()
    rc = child.stop()
    parser.end()
    o = parser.obs
    if timed_out and not o.error:
        o.error = f"time limit ({spec.limit:.0f} s)" if o.first_tool is None else ""
    facts = ev.full_facts("pi", o.tools, o.coder_results, first_result_at)
    full = {"coder_calls": facts.coder_calls, "coder_started": sorted(started), "coder_results": facts.coder_results,
            "tests_run": facts.tests_run, "tools": facts.tools[:60], "timed_out": timed_out,
            # Pi's coder runs in its own process: no file changed by its result = it did nothing (the main agent
            # delegates first, so the files then are the coder's)
            "files_at_coder_result": at_result,
            "coder_stalled": bool(facts.coder_calls and facts.coder_results and at_result is not None and not at_result)}
    prac = ev.practical(o, spec.cwd, None, max_seconds=None)
    return Outcome(o, seconds, rc, child.err_text(), thinking_level(spec, o), timed_out, full, prac)


# ------------------------------------------------------------------------------------------------ full runs: OpenCode
def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class OpenCodeApi:
    """A small client of `opencode serve` (127.0.0.1 only)."""

    def __init__(self, port: int, timeout: float = 60.0) -> None:
        self.base = f"http://127.0.0.1:{port}"
        self.timeout = timeout

    def call(self, method: str, path: str, body: Optional[Mapping[str, Any]] = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, method=method, data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            raw = r.read()
        return json.loads(raw) if raw else None

    def ready(self) -> bool:
        try:
            self.call("GET", "/session/status")
            return True
        except (urllib.error.URLError, OSError, ValueError):
            return False


def run_full_opencode(spec: RunSpec, env: Mapping[str, str], poll: float = 2.0, stable_polls: int = 3) -> Outcome:
    """OpenCode as a local server: send the task, poll the session until the main session and its subagent
    sessions are idle, every background task has come back and the main agent has answered after it."""
    port = free_port()
    clear_stale_locks(spec.home)
    child = Child(["opencode", "serve", "--port", str(port), "--hostname", "127.0.0.1", "--print-logs",
                   "--log-level", "INFO"], spec.cwd, env)
    api = OpenCodeApi(port)
    obs = ev.Observation("opencode")
    full: Dict[str, Any] = {}
    timed_out = False
    try:
        while not api.ready():
            if not child.running() or child.elapsed() > 180:
                obs.error = "opencode serve did not start: " + child.err_text(5)
                return Outcome(obs, child.elapsed(), child.stop(), child.err_text(), "", False, full)
            time.sleep(0.5)
        sid = str(api.call("POST", "/session", {"title": "agent-bench"})["id"])
        body: Dict[str, Any] = {"parts": [{"type": "text", "text": spec.prompt}],
                                "model": {"providerID": spec.provider, "modelID": spec.model}, "agent": "build"}
        variant = opencode_variant(spec.thinking)
        if variant:
            body["variant"] = variant
        t_start = child.elapsed()
        api.call("POST", f"/session/{sid}/prompt_async", body)
        msgs: List[Any] = []
        stable = 0
        while True:
            time.sleep(poll)
            while child.next_line(0) is not None:
                pass                                 # drain the log
            status = api.call("GET", "/session/status") or {}
            msgs = api.call("GET", f"/session/{sid}/message") or []
            kids = api.call("GET", f"/session/{sid}/children") or []
            busy = {k for k, v in status.items() if isinstance(v, dict) and v.get("type") != "idle"}
            tools, results, _ = ev.opencode_messages_facts(msgs)
            bg = sum(1 for t in tools if t.name == "task" and ev.is_delegation("opencode", t.name, t.args)
                     and bool(((t.raw.get("state") or {}).get("metadata") or {}).get("background")))
            last_role = ((msgs[-1].get("info") or {}).get("role") if msgs else None)
            finished = (sid not in busy and not any(k.get("id") in busy for k in kids if isinstance(k, dict))
                        and results >= bg and last_role == "assistant")
            stable = stable + 1 if finished else 0
            if stable >= stable_polls:
                break
            if child.elapsed() - t_start > spec.limit:
                timed_out = True
                break
            if not child.running():
                obs.error = "opencode serve stopped: " + child.err_text(5)
                break
        tools, results, first_result = ev.opencode_messages_facts(msgs)
        obs.tools = tools
        if tools:
            obs.first_tool = tools[0]
            obs.step_tools = [tools[0].name]
            obs.first_event = ev.trim(tools[0].raw)
        for m in msgs:
            info = m.get("info") or {}
            for p in m.get("parts") or []:
                if p.get("type") == "text" and info.get("role") == "assistant":
                    obs.text += str(p.get("text", ""))
                if p.get("type") == "step-finish":
                    obs.thinking_tokens += ev._int((p.get("tokens") or {}).get("reasoning"))
            if info.get("error") and not obs.error:
                obs.error = json.dumps(info.get("error"))[:500]
        facts = ev.full_facts("opencode", tools, results, None if first_result is None else float(first_result))
        coder_tools: List[ev.ToolCall] = []
        for k in api.call("GET", f"/session/{sid}/children") or []:     # the coder's own session(s)
            if isinstance(k, dict) and _CODER_TITLE.search(str(k.get("title", ""))):
                coder_tools += ev.opencode_messages_facts(api.call("GET", f"/session/{k.get('id')}/message") or [])[0]
        work = ev.coder_work("opencode", coder_tools, spec.cwd)
        full = {"coder_calls": facts.coder_calls, "coder_results": facts.coder_results, "tests_run": facts.tests_run,
                "tools": facts.tools[:60], "timed_out": timed_out, "messages": len(msgs),
                "coder_writes": work.writes, "coder_test_runs": work.test_runs,
                "coder_stalled": work.stalled(facts.coder_calls, facts.coder_results)}
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as e:
        obs.error = f"opencode serve API: {e}"
    seconds = child.elapsed()
    rc = child.stop()
    obs.ended = True
    obs.turn_ended = not timed_out
    prac = ev.practical(obs, spec.cwd, None, max_seconds=None)     # "at" is a message index here
    return Outcome(obs, seconds, rc, child.err_text(), thinking_level(spec, obs), timed_out, full, prac)


def run_full(spec: RunSpec, env: Mapping[str, str]) -> Outcome:
    if spec.client == "opencode":
        return run_full_opencode(spec, env)
    if spec.client == "pi":
        return run_full_pi(spec, env)
    raise ValueError(f"unknown client {spec.client!r}")


def versions(clients: Sequence[str], env: Mapping[str, str], cwd: str) -> Dict[str, str]:
    return {c: client_version(c, env, cwd) for c in clients}
