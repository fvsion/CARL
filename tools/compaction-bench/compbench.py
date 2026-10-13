#!/usr/bin/env python3
"""The compaction bench (Phase 23.5.2.1): long coding sessions of the real OpenCode and Pi on CARL's server, every
request the model got saved; then, at points where the context is 40-70% full, each compaction method makes the
request shorter and the same model, given the shorter request, is scored against what it really did with all of it.

    compbench.py prepare  --home H [--model M] [--port P] [--models-dir D]       a harness HOME, the coder off
    compbench.py collect  --home H --out DIR [--clients opencode,pi] [--scripts pylib,pycli] [--model M] [--port P]
    compbench.py checkpoints DIR [--window 98304] [--at 0.4,0.55,0.7]
    compbench.py score    DIR --methods none,mask:4:0.3,... [--model M] [--port P] [--home H]
    compbench.py report   DIR [--md]

collect: one session per client and script (a series of the agent bench's tasks on one fixture project, each run to
its end, in one session, so the context grows as in real work). The clients talk to a capture proxy
(tools/req-capture-proxy.py) in front of CARL's server, which saves each chat request's body: the requests of a
session are its transcript as the model saw it (CARL's own rewrites included).

checkpoints: for each session, the requests nearest to each fraction of the window (tokens from the server's
tokenizer at score time; chars / 3.6 here), each with the truth: the assistant message that followed it (the next
request of the session holds it).

score: for each checkpoint and method, two requests to the same model (temperature 0, the session's tools kept so the
prompt's start stays the same): the next step (its first tool call against the truth's: the tool, the file or the
command) and a probe (a JSON answer: the files changed so far, the last error, the next step), scored against what
code reads from the full transcript (files changed: the write and edit calls; the last error: the last failed tool
result). Also the prompt tokens and the read time.

Methods: none; mask:K:S (each tool result cleared to one line, except the newest K tool results and those in the last S
of the context, and the latest error: the two-way guard of the user's decision, 2026-10-12); more come with
23.5.2.2-.3 (CARL's prune and summary).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
REPO = os.path.dirname(TOOLS)
sys.path.insert(0, os.path.join(TOOLS, "cache-matrix"))
sys.path.insert(0, os.path.join(TOOLS, "agent-bench"))
sys.path.insert(0, TOOLS)

JsonDict = Dict[str, Any]
# Measured against the A3B's tokenizer (2026-10-13, thinking on: the rendered template, /tokenize): a message's text,
# thinking and call arguments 3.34-3.41 chars a token, the tools 4.0. The first value, 3.6 without the thinking, gave
# 78.9K for a request of 98.4K.
CHARS_PER_TOKEN = 3.35
TOOLS_CHARS_PER_TOKEN = 4.0
WINDOW = 98304
AT = (0.4, 0.55, 0.7)
TASK_LIMIT = 1800.0                 # one task of a script, at most (the A3B: 2-8 min a medium task)
PROXY_GAP = 10                      # the server runs at the clients' port + this; the proxy at the clients' port
# Follow-ups after the tasks, as a user asks at the end of a piece of work: they read and run much (the first run,
# 2026-10-12, ended at 25-45K tokens with the tasks alone: the 55% and 70% points were never reached)
FOLLOW_UPS = [
    "Run the whole test suite and show me the full output. Fix anything that fails.",
    "Read every file you changed in this session again, in full, and check it for mistakes. Fix what you find.",
    "Add tests for every function you added in this session that has no test yet, then run the whole suite.",
    "Write CHANGES.md: every change you made in this session, file by file, with the reason for each.",
    "Show me the full contents of each module in this project once more and tell me which parts are untested.",
]
# A script: the agent bench's tasks on one fixture project, one after the other in one session (questions first, as a
# user does, then changes, a stuck bug and a large task: tool output of every kind), then the follow-ups. An item that
# is not a prompt id is the text itself.
# "brief:ID,ID,...": a coder-style turn (user, 2026-10-13: sessions where one turn runs a long tool loop): those tasks
# in one message, worked through without stopping.
SCRIPTS: Dict[str, Tuple[str, List[str]]] = {
    "pylib": ("pylib", ["question-review", "small-test", "stuck-zerodiv", "medium-change", "medium-module",
                        "large-module", "stuck-test", "small-newtest", "large-cli", *FOLLOW_UPS]),
    "pycli": ("pycli", ["question-explain", "small-rename", "small-flag", "medium-command", "stuck-keyerror",
                        "question-error", "large-feature", "small-gitignore", *FOLLOW_UPS]),
    "webapp": ("webapp", ["question-where", "small-typo", "stuck-console", "large-refactor", *FOLLOW_UPS]),
    "pyrules": ("pyrules", ["rules-func", "rules-rename", "rules-flag", "rules-test", *FOLLOW_UPS]),
    "coder-pylib": ("pylib", ["brief:medium-change,medium-module,stuck-zerodiv,large-module",
                              "brief:stuck-test,small-newtest,large-cli"]),
    "coder-pycli": ("pycli", ["brief:small-rename,small-flag,medium-command,stuck-keyerror",
                              "brief:large-feature,small-gitignore"]),
}
BRIEF = ("Work through every item below in order, without stopping to ask. After each item, run the whole test suite "
         "and fix anything that fails before you go on. When all are done, reply with a short report.\n\n")
# After a script, while the session is under --target: these, in turn (the sessions must reach the 96K window's
# 35-75% band; user, 2026-10-13: "we need sessions that get up to the 96k")
GROW = [
    "Run the whole test suite verbosely and show me the full output. Fix anything that fails.",
    "Read every test file in this project in full and list the behaviours that are not tested yet. Add tests for them.",
    "Read every source file once more in full and look for edge cases that are not handled. Fix them, with tests.",
    *FOLLOW_UPS,
]
TARGET = 74000           # a session stops once its context is this big (75% of 96K is ~73.7K)
# A coder-style session gets a follow-up brief only under 60% of the window: CARL's rule for sending the coder back to
# its session (carl-delegation RESUME_MAX, 1.20.9); above it the session grows only within its turn.
RESUME_SHARE = 0.6
# The probe (2026-10-13, agreed: "every error hit, most recent first, each with how it was fixed": the shape of the
# summary's error section; the earlier "last error" question was read as "now" in 41 of 96 answers)
PROBE = ("Stop for a moment and answer from what you know so far, without calling any tool. Reply with only a JSON "
         "object: {\"files_changed\": [the paths of every file you created or changed in this session], "
         "\"errors\": [every failing test or error message you saw in this session, most recent first, even if you "
         "fixed it since, each as {\"error\": \"the test's name and the message, as exactly as you can\", \"fix\": "
         "\"how it was fixed, or 'not fixed'\"}; [] only if there was none], "
         "\"next_step\": \"what you will do next, in one sentence\"}. Paths relative to the project's root.")
ERROR_RE = re.compile(r"(Traceback \(most recent call last\)|\w*Error\b|\w*Exception\b|\berror:|\bFAILED\b|"
                      r"\b\d+ failed\b|[Ee]xit code:? [1-9])")
NAMED_ERROR_RE = re.compile(r"\w+(Error|Exception)\b|\berror:")
DIAG_RE = re.compile(r"ERROR \[\d+:\d+\]")        # a diagnostic a tool reports (OpenCode's edit: the LSP's errors)
PATH_RE = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.(?:py|js|ts|json|md|toml|txt|cfg|ini|html|css|sh))\b")


def say(msg: str) -> None:
    print(time.strftime("%H:%M:%S ") + msg, flush=True)


# ------------------------------------------------------------------------------------------ the transcript, read by code
def msg_text(m: Mapping[str, Any]) -> str:
    c = m.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(str(p.get("text", "")) for p in c if isinstance(p, dict))
    return ""


def tool_calls(m: Mapping[str, Any]) -> List[JsonDict]:
    """An assistant message's tool calls as {name, args} (args parsed from their JSON text when they can be)."""
    out = []
    for tc in m.get("tool_calls") or []:
        fn = (tc or {}).get("function") or {}
        args: Any = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {"_raw": args}
        out.append({"name": str(fn.get("name") or ""), "args": args if isinstance(args, dict) else {}})
    return out


def call_target(call: Mapping[str, Any]) -> str:
    """What a tool call acts on: its file path, else the first word of its command, else its pattern or URL."""
    a = call.get("args") or {}
    for k in ("filePath", "file_path", "path", "file"):
        if isinstance(a.get(k), str) and a[k]:
            return norm_path(a[k])
    cmd = a.get("command") or a.get("cmd")
    if isinstance(cmd, str) and cmd.strip():
        return cmd.strip().split()[0]
    for k in ("pattern", "query", "url"):
        if isinstance(a.get(k), str):
            return a[k]
    return ""


PROJECT_RE = re.compile(r"^.*?/projects/[^/]+/[^/]+/[^/]+/")      # collect's copy: projects/SESSION/FIXTURE-xxxx/ROOT/


def norm_path(p: str) -> str:
    """A path as the probe compares it: from the project's root down (the absolute folder of collect's copy dropped,
    else an agent-bench fixture's)."""
    p = p.strip().strip("`'\"")
    m = PROJECT_RE.match(p if p.startswith("/") else "/" + p)
    if m:
        return (p if p.startswith("/") else "/" + p)[m.end():]
    for marker in ("/pylib/", "/pycli/", "/webapp/", "/pyrules/", "/empty/"):
        if marker in p:
            return p.split(marker, 1)[1]
    return p.lstrip("./")


WRITE_TOOLS = {"write", "edit", "multiedit", "apply_patch", "patch", "str_replace", "create"}


def files_changed(messages: Sequence[Mapping[str, Any]]) -> List[str]:
    out: List[str] = []
    for m in messages:
        if m.get("role") != "assistant":
            continue
        for c in tool_calls(m):
            if c["name"].lower() in WRITE_TOOLS:
                t = call_target(c)
                if t and t not in out:
                    out.append(t)
    return out


COMMAND_TOOLS = {"bash", "shell", "run", "exec", "command", "terminal"}


def command_results(messages: Sequence[Mapping[str, Any]]) -> set:
    """The indexes of the results of commands (a file read that shows `raise ValueError` is not an error)."""
    names = _call_names(messages)
    return {i for i, n in names.items() if n.split(" ")[0].lower() in COMMAND_TOOLS}


def error_result(messages: Sequence[Mapping[str, Any]]) -> Optional[int]:
    """The index of the last tool result that shows an error: a command's that says one, or a write tool's with a
    diagnostic (OpenCode's edit reports the LSP's errors; a file read that shows `raise ValueError` is not one)."""
    commands = command_results(messages)
    names = _call_names(messages)
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m.get("role") != "tool":
            continue
        text = msg_text(m)
        if (i in commands and ERROR_RE.search(text)) or (
                names.get(i, "").split(" ")[0].lower() in WRITE_TOOLS and DIAG_RE.search(text)):
            return i
    return None


def last_error(messages: Sequence[Mapping[str, Any]]) -> str:
    """The last line that names an error (else the last that says one), of the last result that shows one."""
    i = error_result(messages)
    if i is None:
        return ""
    text = msg_text(messages[i])
    diags = [ln.strip() for ln in text.splitlines() if DIAG_RE.search(ln)]
    if diags:
        return diags[-1]
    lines = [ln.strip() for ln in text.splitlines() if ERROR_RE.search(ln)]
    named = [ln for ln in lines if NAMED_ERROR_RE.search(ln)]      # "ZeroDivisionError: ..." over "1 failed"
    return (named or lines or [""])[-1]


KEY_LINE_RE = re.compile(r"^\s*(FAIL:|ERROR:|_{3,})")
MESSAGE_RE = re.compile(r"\b\w+(?:Error|Exception): (.{4,})|ERROR \[\d+:\d+\] (.{4,})")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower().replace('"', "'")).strip()


def error_keys(messages: Sequence[Mapping[str, Any]]) -> List[str]:
    """What names the last error, in any form (2026-10-13: "AssertionError: 'a' != 'ccc'" is the same failure as
    "tests/test_longest.py:8: AssertionError"): the failing tests' names, file:line, and the start of each error
    message (the exception's or the diagnostic's), from the error lines of the last result that shows one."""
    i = error_result(messages)
    if i is None:
        return []
    keys: List[str] = []
    for ln in msg_text(messages[i]).splitlines():
        if not (ERROR_RE.search(ln) or KEY_LINE_RE.search(ln) or DIAG_RE.search(ln)):
            continue
        keys += re.findall(r"\btest_\w+", ln) + re.findall(r"[\w/.-]+\.py:\d+", ln)
        for a, b in MESSAGE_RE.findall(ln):
            msg = _norm(a or b)[:25]
            if len(msg) >= 6:
                keys.append(msg)
    return list(dict.fromkeys(k if k.startswith("test_") or ".py:" in k else _norm(k) for k in keys))


def all_paths(messages: Sequence[Mapping[str, Any]]) -> set:
    """Every path-like string anywhere in the transcript (an answer's path not among them is made up)."""
    seen = set()
    for m in messages:
        for p in PATH_RE.findall(msg_text(m) + " " + json.dumps(m.get("tool_calls") or [])):
            seen.add(norm_path(p))
    return seen


# --------------------------------------------------------------------------------------------------------- the methods
def est_tokens(m: Mapping[str, Any]) -> int:
    """A message's tokens: its text, its thinking and its calls."""
    return int(len(msg_text(m) + str(m.get("reasoning_content") or "") + json.dumps(m.get("tool_calls") or []))
               / CHARS_PER_TOKEN)


def mask(messages: Sequence[Mapping[str, Any]], keep_calls: int, keep_share: float) -> List[JsonDict]:
    """Tool results cleared to one line, except the newest keep_calls of them, those in the last keep_share of the
    context, and the latest error (the two-way guard: a flat number and a share). Everything else as it was."""
    out = [dict(m) for m in messages]
    total = sum(est_tokens(m) for m in out) or 1
    tail, acc = len(out), 0
    for i in range(len(out) - 1, -1, -1):          # where the last keep_share of the context starts
        acc += est_tokens(out[i])
        if acc > keep_share * total:
            break
        tail = i
    tools = [i for i, m in enumerate(out) if m.get("role") == "tool"]
    keep = set(tools[-keep_calls:]) if keep_calls > 0 else set()
    err = error_result(out)
    names = _call_names(out)
    for i in tools:
        if i in keep or i >= tail or i == err:
            continue
        out[i]["content"] = f"[cleared: the output of {names.get(i, 'a tool')}; call it again to see it]"
    return out


def _call_names(messages: Sequence[Mapping[str, Any]]) -> Dict[int, str]:
    """Each tool result's index -> its call in words ("read notes/store.py"), from the assistant message before it."""
    out: Dict[int, str] = {}
    by_id: Dict[str, str] = {}
    for i, m in enumerate(messages):
        if m.get("role") == "assistant":
            for tc, c in zip(m.get("tool_calls") or [], tool_calls(m)):
                by_id[str((tc or {}).get("id") or "")] = f"{c['name']} {call_target(c)}".strip()
        elif m.get("role") == "tool":
            out[i] = by_id.get(str(m.get("tool_call_id") or ""), "a tool")
    return out


def _calls(messages: Sequence[Mapping[str, Any]]) -> Dict[int, JsonDict]:
    """Each tool result's index -> its call ({name, args}), from the assistant message before it."""
    out: Dict[int, JsonDict] = {}
    by_id: Dict[str, JsonDict] = {}
    for i, m in enumerate(messages):
        if m.get("role") == "assistant":
            for tc, c in zip(m.get("tool_calls") or [], tool_calls(m)):
                by_id[str((tc or {}).get("id") or "")] = c
        elif m.get("role") == "tool":
            out[i] = by_id.get(str(m.get("tool_call_id") or ""), {"name": "", "args": {}})
    return out


# ------------------------------------------------------------------------- the compaction event (23.5.2, #1 and #2)
# Agreed with the user 2026-10-13 (design_and_analysis/compaction-decisions.md): one event, at idle or at the hard
# trigger; the current turn's user message verbatim; a raw working tail of 20% of the window (at least the newest K
# results; never a call without its result); the newest view of each file the current turn changes, in a budget of
# 10% of the window; the event works until the context is under the drain line (40% of the window), removing the old
# part's results first, then the current turn's middle, then the changing files beyond their budget.
DRAIN = 0.4
FILES_SHARE = 0.1
MIN_PRUNE = 80                 # ph=facts: a result under this is never pruned (the placeholder would not be smaller)
LIST_WHOLE = 300               # ph=facts: a search or listing under this stays whole
READ_TOOLS = {"read", "view", "cat", "read_file"}
LIST_TOOLS = {"glob", "grep", "find", "ls", "list", "search", "rg"}
TEST_CMD_RE = re.compile(r"\b(pytest|unittest|tox|nox|npm (run )?test|yarn test|pnpm test|jest|vitest|go test|"
                         r"cargo test|make (test|check)|ruff|mypy|flake8|eslint|tsc)\b")
# a command that changes the project or the system: a fixed word list, not inference (the user: safe defaults)
SIDE_RE = re.compile(r"(^|[;&|])\s*(rm|mv|cp|mkdir|rmdir|touch|chmod|chown|ln|tee|sed\s+-i|pip3?\s+install|"
                     r"npm\s+(install|i|uninstall)|brew\s+install|git\s+(commit|push|checkout|switch|reset|add|rm|mv|"
                     r"merge|rebase|stash|tag|clean|restore|apply|cherry-pick))\b|[^<>=2&]>{1,2}\s*[\w./~-]")
EXIT_RE = re.compile(r"(?:exited with code|exit code:?)\s*(\d+)", re.I)
TOTAL_LINES_RE = re.compile(r"total (\d+) lines")


def _cmd(call: Mapping[str, Any]) -> str:
    a = call.get("args") or {}
    c = str(a.get("command") or a.get("cmd") or "").strip().replace("\n", " ")
    c = re.sub(r"^cd [^&;]+(&&|;)\s*", "", c)                 # "cd PROJECT && pytest": the command is pytest
    return c if len(c) <= 60 else c[:57] + "..."


def _lines(text: str) -> int:
    m = TOTAL_LINES_RE.search(text[-200:])
    return int(m.group(1)) if m else len(text.rstrip("\n").splitlines())


def test_outcome(text: str) -> str:
    """A test run's outcome in STE100: the failed tests by name and the counts (pytest, unittest)."""
    failed = list(dict.fromkeys(re.findall(r"FAILED \S*::(\w+)", text)
                                + re.findall(r"^(?:FAIL|ERROR): (\w+)", text, re.M)))
    nf = re.search(r"(\d+) failed", text)
    np_ = re.search(r"(\d+) passed", text)
    ran = re.search(r"Ran (\d+) tests?", text)
    out = []
    n_failed = int(nf.group(1)) if nf else len(failed)
    if n_failed:
        names = ", ".join(failed[:5]) + (", ..." if len(failed) > 5 else "")
        out.append(f"{n_failed} {'test' if n_failed == 1 else 'tests'} failed" + (f": {names}." if names else "."))
    if np_:
        out.append(f"{np_.group(1)} tests passed.")
    elif ran and not n_failed:
        out.append(f"{ran.group(1)} tests passed.")
    return " ".join(out)


def placeholder(call: Mapping[str, Any], text: str, changed_after: bool, in_task: bool) -> str:
    """A pruned result's placeholder, in STE100 (user, 2026-10-13: "say pruned instead of cleared"; "Write these in
    STE100 simplified technical english for fidelity"): built by code from the call and its result, by kind."""
    name = str(call.get("name") or "tool").lower()
    target = call_target(call)
    if name in READ_TOOLS:
        head = f"[pruned] read {target}: {_lines(text)} lines."
        if in_task:
            return head + " You changed this file in this task. Read the file again before you change it."
        if changed_after:
            return head + " The file changed after this read. This data is old. Read the file again before you use it."
        return head + " Read the file again to see its contents."
    if name in COMMAND_TOOLS:
        cmd = _cmd(call)
        ex = EXIT_RE.findall(text)
        head = f'[pruned] {name} "{cmd}":' + (f" exit {ex[-1]}." if ex else "")
        if TEST_CMD_RE.search(cmd):
            outcome = test_outcome(text)
            if outcome:
                return f"{head} {outcome} Do the tests again to see their status."
        if SIDE_RE.search(" " + cmd):
            return f"{head} This command changed the project. Do not do this command again."
        if ERROR_RE.search(text):
            err = last_error([{"role": "assistant", "tool_calls": [{"id": "x", "function": {"name": name,
                              "arguments": json.dumps(call.get("args") or {})}}]},
                              {"role": "tool", "tool_call_id": "x", "content": text}])
            if err:
                return f"{head} Error: {err[:120]}"
        last = next((ln.strip() for ln in reversed(text.splitlines()) if ln.strip()), "")
        return f'{head} Last line: "{last[:100]}".' if last else f"{head} No output."
    if name in WRITE_TOOLS:
        n_diag = len(DIAG_RE.findall(text))
        if re.search(r"success|applied|wrote", text, re.I):
            return (f"[pruned] {name} {target}: The change is done."
                    + (f" The tool reported {n_diag} {'error' if n_diag == 1 else 'errors'}." if n_diag else ""))
        first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        return f'[pruned] {name} {target}: The change failed: "{first[:100]}".'
    if name in LIST_TOOLS:
        items = [norm_path(ln.strip()) for ln in text.splitlines() if ln.strip()]
        return (f"[pruned] {name} \"{target}\": {len(items)} {'result' if len(items) == 1 else 'results'}. "
                f"The first {min(10, len(items))}: {', '.join(items[:10])}.")
    return f"[pruned] {name} {target}: {_lines(text)} lines.".replace("  ", " ")


ARG_STRINGS = ("oldString", "newString", "content", "oldText", "newText", "old_string", "new_string", "patch")


def stub_args(args: Any) -> Any:
    """#4 (agreed 2026-10-13): an edit's or a write's strings (OpenCode: oldString, newString, content; Pi: path,
    content, edits[].oldText/newText) as "[pruned: N lines]"; every other argument, and every name, as it was."""
    if isinstance(args, dict):
        return {k: (f"[pruned: {len(v.splitlines()) or 1} lines]" if k in ARG_STRINGS and isinstance(v, str)
                    else stub_args(v) if isinstance(v, (dict, list)) else v) for k, v in args.items()}
    if isinstance(args, list):
        return [stub_args(x) for x in args]
    return args


def _prune_args(m: JsonDict, j: int) -> None:
    """The message's j-th tool call with its strings pruned (stub_args); the message's tool calls copied first."""
    tcs = [dict(tc, function=dict(tc.get("function") or {})) for tc in m.get("tool_calls") or []]
    fn = tcs[j]["function"]
    try:
        args = json.loads(fn.get("arguments") or "{}")
    except ValueError:
        return
    fn["arguments"] = json.dumps(stub_args(args))
    m["tool_calls"] = tcs


THINK_SENTENCES = 50          # +think=last: the last whole sentences up to ~50 tokens (user, 2026-10-13) ...
THINK_OTHER = 100             # ... else (a list or another format) the last ~100 tokens
SENTENCE_END_RE = re.compile(r"[.!?][\"')\]`*]*$")


def think_last(text: str) -> str:
    """A pruned thinking block's remnant (+think=last; user, 2026-10-13: "the last 50 tokens when it is a whole
    sentence the last 100 when it's a list of other format"): "[pruned: N tokens]" and the block's end, which often
    holds its decision."""
    t = text.strip()
    n = int(len(t) / CHARS_PER_TOKEN)
    if SENTENCE_END_RE.search(t):
        room = int(THINK_SENTENCES * CHARS_PER_TOKEN)
        sents = re.split(r"(?<=[.!?])\s+", t)
        keep = [sents.pop()]
        while sents and len(" ".join([sents[-1], *keep])) <= room:
            keep.insert(0, sents.pop())
        end = " ".join(keep)
        if len(end) > room:                          # one long sentence: its end, from a word
            end = end[-room:].split(" ", 1)[-1]
    else:
        room = int(THINK_OTHER * CHARS_PER_TOKEN)
        end = t[-room:]
        if len(t) > room:
            end = end.split("\n", 1)[1] if "\n" in end[:-1] else end.split(" ", 1)[-1]   # from a line, else a word
    return f"[pruned: {n} tokens] {end.strip()}"


def thinking(m: Mapping[str, Any]) -> str:
    """An assistant message's thinking: reasoning_content, else a <think> block in its text."""
    r = m.get("reasoning_content")
    if isinstance(r, str) and r.strip():
        return r
    c = m.get("content")
    if isinstance(c, str) and "</think>" in c:
        return c.split("</think>", 1)[0].split("<think>")[-1]
    return ""


def _set_thinking(m: JsonDict, new: str) -> None:
    if isinstance(m.get("reasoning_content"), str) and m["reasoning_content"].strip():
        m["reasoning_content"] = new
    else:
        c = str(m.get("content") or "")
        m["content"] = (f"<think>\n{new}\n</think>" if new else "") + c.split("</think>", 1)[1]


def event(messages: Sequence[Mapping[str, Any]], extra: int, window: int, keep_calls: int, tail_share: float,
          facts: bool, args: bool = False, think: str = "", think_mid: bool = False) -> List[JsonDict]:
    """The compaction event's prune (stage 1) on a request: its shape and the drain line (above). extra: the tokens
    outside the messages (the tools). facts: the STE100 placeholders and their thresholds (#2), else the mask's line.
    args: old edit and write arguments pruned too (#4), with the results of their place in the order. think: earlier
    thinking pruned (empty: removed; last: think_last()), the old part's first (before its results; Qwen's template
    drops it by default), the current turn's middle only with think_mid (else kept, the baseline), never the tail; no
    size floor (user, 2026-10-13: "the 80 limit shouldn't apply here")."""
    out = [dict(m) for m in messages]
    sizes = [est_tokens(m) for m in out]
    total = sum(sizes) + extra
    if total <= DRAIN * window:
        return out                                   # under the line: the event has nothing to do
    users = [i for i, m in enumerate(out) if m.get("role") == "user"]
    cur = users[-1] if users else 0                  # the current turn's user message
    start, acc = len(out), 0                         # the working tail: its last tail_share of the window
    for i in range(len(out) - 1, -1, -1):
        if acc >= tail_share * window:
            break
        acc += sizes[i]
        start = i
    results = [i for i, m in enumerate(out) if m.get("role") == "tool"]
    if keep_calls > 0 and len(results) >= keep_calls:
        start = min(start, results[-keep_calls])
    while start > 0 and out[start].get("role") == "tool":      # never a result without its call
        start -= 1
    calls = _calls(out)
    err = error_result(out)
    # the newest view of each file the current turn changes, outside the tail: newest-edited first, in the budget
    changing: List[str] = []
    for i in range(len(out) - 1, cur, -1):
        if out[i].get("role") == "assistant":
            for c in reversed(tool_calls(out[i])):
                t = call_target(c)
                if c["name"].lower() in WRITE_TOOLS and t and t not in changing:
                    changing.append(t)
    kept_files, beyond, room = set(), [], FILES_SHARE * window
    for path in changing:
        view = next((i for i in reversed(results) if i < start and calls[i]["name"].lower() in READ_TOOLS
                     and call_target(calls[i]) == path), None)
        if view is None:
            continue
        if sizes[view] <= room:
            kept_files.add(view)
            room -= sizes[view]
        else:
            beyond.append(view)
    protected = kept_files | {err} if err is not None else set(kept_files)
    old: List[Tuple[int, int]] = [(i, -1) for i in results if i < cur and i not in protected and i not in beyond]
    mid: List[Tuple[int, int]] = [(i, -1) for i in results if cur < i < start and i not in protected and i not in beyond]
    if args:
        # the edits after a kept view of a changing file are that file's current state with it (rule 4): they stay
        views = {call_target(calls[v]): v for v in kept_files}
        for i, m in enumerate(out[:start]):
            if m.get("role") != "assistant":
                continue
            for j, c in enumerate(tool_calls(m)):
                if c["name"].lower() not in WRITE_TOOLS or (call_target(c) in views and i > views[call_target(c)]):
                    continue
                size = int(len(json.dumps(c["args"])) / CHARS_PER_TOKEN)
                if size >= MIN_PRUNE:
                    (old if i < cur else mid).append((i, j))
        old.sort()
        mid.sort()
    first: List[Tuple[int, int]] = []
    if think:
        for i, m in enumerate(out[:start]):
            if m.get("role") == "assistant" and thinking(m).strip():
                if i < cur:
                    first.append((i, -2))
                elif think_mid:
                    mid.append((i, -2))
        mid.sort()
    names = _call_names(out)
    for i, j in first + old + mid + [(b, -1) for b in beyond]:
        if total <= DRAIN * window:
            break
        if j == -2:                                  # a thinking block
            new_think = "" if think == "empty" else think_last(thinking(out[i]))
            if len(new_think) >= len(thinking(out[i])):
                continue
            _set_thinking(out[i], new_think)
            new_size = est_tokens(out[i])
            total -= sizes[i] - new_size
            sizes[i] = new_size
            continue
        if j >= 0:                                   # an edit's or a write's arguments (#4)
            _prune_args(out[i], j)
            new_size = est_tokens(out[i])
            total -= sizes[i] - new_size
            sizes[i] = new_size
            continue
        text = msg_text(out[i])
        call = calls[i]
        kind = str(call.get("name") or "").lower()
        if facts and (sizes[i] < MIN_PRUNE or (kind in LIST_TOOLS and sizes[i] < LIST_WHOLE)):
            continue
        if facts:
            path = call_target(call)
            later = any(j > i and calls[j]["name"].lower() in WRITE_TOOLS and call_target(calls[j]) == path
                        for j in results)
            new = placeholder(call, text, later, i in beyond)
        else:
            new = f"[cleared: the output of {names.get(i, 'a tool')}; call it again to see it]"
        out[i]["content"] = new
        total -= sizes[i] - est_tokens(out[i])
    return out


Method = Callable[..., List[JsonDict]]


def method_fn(spec: str, window: int = WINDOW) -> Method:
    """A method by name: none; mask:K:S (the first guard); mask:K:S+turn (the event's shape, S = the working tail's
    share of the window, K its floor; the drain line); +ph=facts (the STE100 placeholders); +args (old edit and write
    arguments); +think=empty or +think=last (earlier thinking), +thinkmid (the current turn's middle's too). The
    function takes the messages and the tokens outside them (the tools)."""
    base, *mods = spec.split("+")
    parts = base.split(":")
    if parts[0] == "none" and not mods:
        return lambda ms, extra=0: [dict(m) for m in ms]
    known = {"turn", "ph=facts", "args", "think=empty", "think=last", "thinkmid"}
    if parts[0] == "mask" and len(parts) == 3 and set(mods) <= known and (not set(mods) - {"turn"} or "turn" in mods) \
            and not {"think=empty", "think=last"} <= set(mods) and ("thinkmid" not in mods or any(
                x.startswith("think=") for x in mods)):
        k, s = int(parts[1]), float(parts[2])
        if "turn" in mods:
            facts, pargs, mid = "ph=facts" in mods, "args" in mods, "thinkmid" in mods
            think = next((x.split("=", 1)[1] for x in mods if x.startswith("think=")), "")
            return lambda ms, extra=0: event(ms, extra, window, k, s, facts, pargs, think, mid)
        return lambda ms, extra=0: mask(ms, k, s)
    raise SystemExit(f"error: no such method {spec!r} (none, mask:K:S, mask:K:S+turn, mask:K:S+turn+ph=facts)")


def imitated(reply: Mapping[str, Any]) -> bool:
    """The next step's call copies a stub (#4's risk: an edit whose old text is "[pruned: 3 lines]" fails)."""
    return any("[pruned" in json.dumps(c["args"]) or "[cleared" in json.dumps(c["args"]) for c in tool_calls(reply))


def repeated(short: Sequence[Mapping[str, Any]], reply: Mapping[str, Any]) -> bool:
    """The next step calls again what the method pruned (the same read, the same command): a repeated action."""
    calls = _calls(short)
    gone = {(c["name"].lower(), call_target(c) if c["name"].lower() not in COMMAND_TOOLS else _cmd(c))
            for i, c in calls.items() if msg_text(short[i]).startswith(("[pruned]", "[cleared"))}
    for c in tool_calls(reply):
        n = c["name"].lower()
        if (n, call_target(c) if n not in COMMAND_TOOLS else _cmd(c)) in gone:
            return True
    return False


# ----------------------------------------------------------------------------------------------- sessions and points
def load_bodies(folder: str) -> List[JsonDict]:
    names = sorted((n for n in os.listdir(folder) if re.fullmatch(r"chat-\d+\.json", n)),
                   key=lambda n: int(n[5:-5]))
    out = []
    for n in names:
        with open(os.path.join(folder, n), encoding="utf-8") as f:
            out.append(json.load(f))
    return out


def chain(bodies: Sequence[JsonDict]) -> List[JsonDict]:
    """The main session's requests in order: those with tools whose messages continue the one before (a title or a
    summary request, with no tools or another start, is left out)."""
    out: List[JsonDict] = []
    for b in bodies:
        ms = b.get("messages") or []
        if not b.get("tools") or not ms:
            continue
        if out:
            prev = out[-1]["messages"]
            if len(ms) < len(prev) or json.dumps(ms[:2]) != json.dumps(prev[:2]):
                continue
        out.append(b)
    return out


@dataclass
class Checkpoint:
    session: str
    index: int                    # the request in the session's chain
    fraction: float
    tokens: int
    truth: JsonDict               # the assistant message the model wrote next with the whole context


def truth_after(reqs: Sequence[JsonDict], i: int) -> Optional[JsonDict]:
    """The assistant message that followed request i (the next request of the session holds it)."""
    n = len(reqs[i]["messages"])
    return next((m for m in reqs[i + 1]["messages"][n:] if m.get("role") == "assistant"), None)


def points_in_range(session: str, reqs: Sequence[JsonDict], window: int, lo: float, hi: float,
                    every: int = 1) -> List[Checkpoint]:
    """Every request whose context fills lo..hi of the window (every Nth), each with its truth: many points from one
    session (the options research, 2026-10-13: 11 points tell only ~25-point differences apart)."""
    out: List[Checkpoint] = []
    for i in range(len(reqs) - 1):
        size = sum(est_tokens(m) for m in reqs[i]["messages"])
        if not lo * window <= size <= hi * window:
            continue
        truth = truth_after(reqs, i)
        if truth is not None:
            out.append(Checkpoint(session, i, round(size / window, 3), size, truth))
    return out[::max(every, 1)]


def checkpoints(session: str, reqs: Sequence[JsonDict], window: int, at: Sequence[float]) -> List[Checkpoint]:
    sizes = [sum(est_tokens(m) for m in r["messages"]) for r in reqs]
    out: List[Checkpoint] = []
    for f in at:
        want = f * window
        cands = [i for i in range(len(reqs) - 1) if sizes[i] <= want]
        if not cands:
            continue
        i = max(cands, key=lambda j: sizes[j])
        nxt = reqs[i + 1]["messages"]
        n = len(reqs[i]["messages"])
        truth = next((m for m in nxt[n:] if m.get("role") == "assistant"), None)
        if truth is None or any(c.index == i for c in out):
            continue
        out.append(Checkpoint(session, i, f, sizes[i], truth))
    return out


# ----------------------------------------------------------------------------------------------------------- scoring
def score_next(got: Mapping[str, Any], truth: Mapping[str, Any]) -> JsonDict:
    """The next step against the truth: the same tool and the same target (file, command, pattern). Parallel calls
    count as a set: a match when the two sets share a call (the options research: one point's truth was several reads
    at once, and only the first was compared)."""
    g, t = tool_calls(got), tool_calls(truth)
    if not t:
        return {"tool": not g, "target": not g, "got": "text" if not g else g[0]["name"], "want": "text"}
    if not g:
        return {"tool": False, "target": False, "got": "text", "want": t[0]["name"]}
    gt = {(c["name"], call_target(c)) for c in g}
    tt = {(c["name"], call_target(c)) for c in t}
    return {"tool": bool({n for n, _ in gt} & {n for n, _ in tt}), "target": bool(gt & tt),
            "got": "; ".join(sorted(f"{n} {x}" for n, x in gt))[:200],
            "want": "; ".join(sorted(f"{n} {x}" for n, x in tt))[:200]}


def parse_probe(text: str) -> Optional[JsonDict]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        doc = json.loads(m.group(0))
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


ERROR_KEYS_RE = re.compile(r"\btest_\w+|[\w/.-]+\.py:\d+")


def error_recalled(said: str, truth: str, keys: Sequence[str] = ()) -> bool:
    """The last error named: any of its keys (error_keys: a failing test, file:line, the start of its message) in
    what was said, else the failing test or file and line from the truth's line (a paraphrase of the rest counts);
    no error and an empty answer agree."""
    if not truth:
        return not said.strip()
    if keys:
        low = _norm(said)
        return any((k if k.startswith("test_") or ".py:" in k else _norm(k)) in (said if ".py:" in k else low)
                   for k in keys)
    keys = ERROR_KEYS_RE.findall(truth)
    if keys:
        return any(k in said for k in keys)
    return bool(said) and (truth[:60] in said or said[:60] in truth)


def errors_said(answer: Mapping[str, Any]) -> str:
    """The errors an answer lists (each {error, fix} or a string; an older answer's last_error), as one text."""
    errs = answer.get("errors")
    if isinstance(errs, list):
        return "\n".join(str(e.get("error", "")) if isinstance(e, dict) else str(e) for e in errs)
    return str(answer.get("last_error") or "")


def score_probe(answer: Optional[Mapping[str, Any]], full: Sequence[Mapping[str, Any]]) -> JsonDict:
    """The probe's answer against code's reading of the whole transcript up to the point."""
    want = files_changed(full)
    err = last_error(full)
    keys = error_keys(full)
    known = all_paths(full)
    if answer is None:
        return {"parsed": False, "file_recall": 0.0, "file_precision": 0.0, "invented": 0, "error": False}
    got = [norm_path(str(p)) for p in (answer.get("files_changed") or []) if isinstance(p, str)]
    hit = [p for p in got if p in want]
    invented = [p for p in got if p not in known]
    said = errors_said(answer)
    return {"parsed": True,
            "file_recall": len(set(hit)) / len(want) if want else (1.0 if not got else 0.0),
            "file_precision": len(set(hit)) / len(set(got)) if got else (1.0 if not want else 0.0),
            "invented": len(set(invented)),
            "error": error_recalled(said, err, keys)}


# ------------------------------------------------------------------------------------------------------- the server
def post(base: str, key: str, path: str, body: Mapping[str, Any], timeout: float = 3600) -> JsonDict:
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def ask(base: str, key: str, req: Mapping[str, Any], messages: Sequence[Mapping[str, Any]], max_tokens: int) -> JsonDict:
    """The same request (model, tools, template fields) with these messages, temperature 0, not streamed."""
    body = {k: v for k, v in req.items() if k not in ("messages", "stream", "stream_options", "id_slot")}
    body.update(messages=list(messages), stream=False, temperature=0, max_tokens=max_tokens, cache_prompt=True,
                id_slot=0)
    t0 = time.time()
    r = post(base, key, "/v1/chat/completions", body)
    choice = (r.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    t = r.get("timings") or {}
    return {"message": msg, "finish": choice.get("finish_reason"), "seconds": round(time.time() - t0, 2),
            "prompt_n": t.get("prompt_n"),
            "prompt_ms": t.get("prompt_ms"), "cache_n": t.get("cache_n")}


# ---------------------------------------------------------------------------------------------------------- commands
def cmd_prepare(a: argparse.Namespace) -> int:
    """A harness HOME with OpenCode and Pi set up for CARL's server, the coder off (one main session does the work, so
    its context grows), the tested client versions."""
    import cachematrix as cm
    from agentbench import home as hm
    from agentbench.server import CarlServer
    home = hm.check_home_path(a.home)
    if os.path.isdir(home) and os.listdir(home) and not hm.is_harness_home(home):
        raise SystemExit(f"error: {home} is not empty and is not a harness HOME")
    os.makedirs(home, exist_ok=True)
    hm.write_marker(home, {"created_by": "tools/compaction-bench/compbench.py prepare", "repo": REPO})
    cm.check_port(a.port)
    work = a.home.rstrip("/") + "-work"
    conf = os.path.join(home, ".config", "carl")
    os.makedirs(conf, exist_ok=True)
    setup = cm.MatrixSetup(repo=REPO, model=a.model, work=os.path.join(work, "server"), port=a.port,
                           models_dir=os.path.abspath(a.models_dir), slots=1, conf=conf)
    srv = CarlServer(setup)
    srv.start()
    try:
        say(f"ready after {srv.wait_ready():.0f} s; the client package and ./setup in {home} (some minutes)")
        src = hm.PackageSource(REPO, setup.client_dir, conf, os.path.join(work, "dist"), setup.models_dir)
        hm.unpack_and_setup(hm.make_package(src), home, install=True, coder="off")
        found = hm.pin_versions(home, a.opencode_version, a.pi_version)
        hm.write_marker(home, {"versions": found})
    finally:
        srv.stop()
    say(f"ready: {home} ({', '.join(f'{k} {v}' for k, v in found.items())})")
    return 0


def _drivers(client: str, home: str, cwd: str, env: Mapping[str, str], model: str,  # type: ignore[no-untyped-def]
             thinking: str = "default"):
    """The client's driver (cache-matrix's). thinking: default (the model's own, as CARL sets it up: on) or off. Pi
    at that level (default: CARL's, from its settings.json); OpenCode from the request's variant."""
    import cachematrix as cm
    from agentbench import clients as cl
    cm.TURN_LIMIT = TASK_LIMIT
    if client != "opencode":
        class Pi(cm.PiDriver):                    # the matrix's driver starts Pi with thinking off (2026-10-13: the
            def start(self) -> None:              # thinking-on collection's Pi sessions ran off)
                level = cl.pi_level(thinking, self.home)
                self.child = cm.Child(["pi", "--mode", "rpc", "--model", f"{cl.PROVIDER}/{self.model}:{level}"],
                                      self.cwd, self.env, stdin=True)
                self.current = ""
        return Pi(home, cwd, env, model, False)

    class OpenCode(cm.OpenCodeDriver):            # the matrix's driver sends thinking off; here it is a choice
        def say(self, sid: str, text: str) -> str:
            body: JsonDict = {"parts": [{"type": "text", "text": text}],
                              "model": {"providerID": cl.PROVIDER, "modelID": self.model}, "agent": "build"}
            variant = cl.opencode_variant(thinking)
            if variant:
                body["variant"] = variant
            msg = self._call("POST", f"/session/{sid}/message", body) or {}
            err = (msg.get("info") or {}).get("error")
            if err:
                return "error: " + json.dumps(err)[:300]
            return "".join(str(p.get("text", "")) for p in msg.get("parts") or [] if p.get("type") == "text")[:200]
    return OpenCode(home, cwd, env, model, False, "")


def body_tokens(body: Mapping[str, Any]) -> int:
    """A request's size: its messages and its tools (est_tokens; the tools at TOOLS_CHARS_PER_TOKEN). 98.5K for a
    request the server counted 98.4K."""
    return sum(est_tokens(m) for m in body.get("messages") or []) + int(len(json.dumps(body.get("tools") or []))
                                                                         / TOOLS_CHARS_PER_TOKEN)


def session_tokens(folder: str) -> int:
    """The size of a session's newest chat request (its context now)."""
    bodies = chain(load_bodies(folder))
    return body_tokens(bodies[-1]) if bodies else 0


def client_compaction_off(home: str) -> List[str]:
    """The harness HOME's clients without their own compaction and prune, so a session grows as it is (OpenCode
    prunes old tool results above 40K, Pi compacts at the window less 16K; research/clients.md). The files changed."""
    changed = []
    for path, key, value in ((os.path.join(home, ".config", "opencode", "opencode.json"), "compaction",
                              {"auto": False, "prune": False}),
                             (os.path.join(home, ".pi", "agent", "settings.json"), "compaction", {"enabled": False})):
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and doc.get(key) != value:
            doc[key] = value
            with open(path, "w", encoding="utf-8") as f:
                json.dump(doc, f, indent=2)
            changed.append(path)
    return changed


def item_text(item: str, prompts: Mapping[str, Any]) -> str:
    """A script item's message: a prompt's text, a coder-style brief (brief:ID,ID: those tasks in one message), or the
    text itself."""
    if item.startswith("brief:"):
        rest = item[len("brief:"):]
        ids = rest.split(",") if all(i in prompts for i in rest.split(",")) else [rest]     # else a text of its own
        return BRIEF + "\n\n".join(f"{n}. {prompts[i].text if i in prompts else i}" for n, i in enumerate(ids, 1))
    return prompts[item].text if item in prompts else item


def cmd_collect(a: argparse.Namespace) -> int:
    import cachematrix as cm
    from agentbench import clients as cl, fixtures as fx, home as hm, prompts as pr
    from agentbench.server import CarlServer
    home = hm.require_harness_home(a.home)
    os.makedirs(a.out, exist_ok=True)
    prompts = {p.id: p for p in pr.load_prompts()}
    conf = os.path.join(home, ".config", "carl")
    work = a.home.rstrip("/") + "-work"
    setup = cm.MatrixSetup(repo=REPO, model=a.model, work=os.path.join(work, "server"), port=a.port + PROXY_GAP,
                           models_dir=os.path.abspath(a.models_dir), slots=1, conf=conf)
    for path in client_compaction_off(home):
        say(f"the client's own compaction off: {path}")
    srv = CarlServer(setup)
    srv.start()
    srv.wait_ready()
    try:
        env = cl.client_env(home)
        for client in cm.csv(a.clients):
            for script in cm.csv(a.scripts):
                name = f"{client}-{script}"
                folder = os.path.join(a.out, name)
                if os.path.exists(os.path.join(folder, "done.json")):
                    say(f"{name}: done already")
                    continue
                os.makedirs(folder, exist_ok=True)
                proxy = subprocess.Popen([sys.executable, os.path.join(TOOLS, "req-capture-proxy.py"),
                                          f"127.0.0.1:{a.port}", f"127.0.0.1:{a.port + PROXY_GAP}",
                                          os.path.join(folder, "requests.jsonl"), "--bodies", folder],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                fixture, ids = SCRIPTS[script]
                cwd = fx.make_fresh(fixture, os.path.join(work, "projects", name))
                drv = _drivers(client, home, cwd, env, a.model, a.thinking)
                tasks = []
                try:
                    time.sleep(1)
                    drv.start()
                    sid = drv.new()
                    grow = [g for _ in range(a.max_grow // len(GROW) + 1) for g in GROW][:a.max_grow]
                    for pid in [*ids, *grow]:
                        size = session_tokens(folder)
                        if size >= a.target:
                            say(f"{name}: {size} tokens, at the target")
                            break
                        coder = script.startswith("coder-")
                        if coder and pid in grow and size >= RESUME_SHARE * WINDOW:
                            say(f"{name}: {size} tokens: no follow-up brief above 60% of the window (CARL's rule)")
                            break
                        if coder and pid in grow:
                            pid = "brief:" + pid
                        if pid in grow and pid == grow[0] and len(tasks) == len(ids):
                            say(f"{name}: the script done at {size} tokens; follow-ups until {a.target}")
                        t0 = time.time()
                        try:
                            reply = drv.say(sid, item_text(pid, prompts))
                        except Exception as e:  # noqa: BLE001  (one task's failure is data; the session goes on)
                            reply = f"error: {e}"
                        tasks.append({"task": pid[:60], "seconds": round(time.time() - t0, 1), "reply": reply[:200]})
                        say(f"{name}: {pid[:40]} in {time.time() - t0:.0f} s ({session_tokens(folder)} tokens)")
                finally:
                    drv.stop()
                    proxy.terminate()
                    proxy.wait(10)
                with open(os.path.join(folder, "done.json"), "w", encoding="utf-8") as f:
                    json.dump({"client": client, "script": script, "fixture": fixture, "tasks": tasks,
                               "model": a.model, "cwd": cwd, "thinking": a.thinking,
                               "tokens": session_tokens(folder)}, f, indent=1)
    finally:
        srv.stop()
    return 0


def sessions(out: str) -> Dict[str, List[JsonDict]]:
    found = {}
    for name in sorted(os.listdir(out)):
        folder = os.path.join(out, name)
        if os.path.isfile(os.path.join(folder, "done.json")):
            found[name] = chain(load_bodies(folder))
    return found


def cmd_checkpoints(a: argparse.Namespace) -> int:
    rows = []
    for name, reqs in sessions(a.out).items():
        if a.range:
            lo, hi = (float(x) for x in a.range.split(":"))
            found = points_in_range(name, reqs, a.window, lo, hi, a.every)
        else:
            found = checkpoints(name, reqs, a.window, [float(x) for x in a.at.split(",")])
        for c in found:
            rows.append({"session": c.session, "index": c.index, "fraction": c.fraction, "tokens": c.tokens,
                         "truth": c.truth})
            say(f"{name}: request {c.index} at {c.fraction:.0%} (~{c.tokens} tokens)")
    with open(os.path.join(a.out, "checkpoints.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=1)
    return 0


def cmd_score(a: argparse.Namespace) -> int:
    from agentbench.server import CarlServer, ensure_key
    import cachematrix as cm
    with open(os.path.join(a.out, "checkpoints.json"), encoding="utf-8") as f:
        points = json.load(f)
    reqs = sessions(a.out)
    results = os.path.join(a.out, "scores.jsonl")
    done = set()
    if os.path.exists(results):
        with open(results, encoding="utf-8") as f:
            done = {(r["session"], r["index"], r["method"]) for r in map(json.loads, f)}
    conf = os.path.join(a.home, ".config", "carl")
    setup = cm.MatrixSetup(repo=REPO, model=a.model, work=os.path.join(a.home.rstrip("/") + "-work", "server"),
                           port=a.port, models_dir=os.path.abspath(a.models_dir), slots=1, conf=conf)
    srv = CarlServer(setup)
    srv.start()
    srv.wait_ready()
    base, key = f"http://127.0.0.1:{a.port}", ensure_key(setup.key_file)
    try:
        refs: Dict[Tuple[str, int], JsonDict] = {}
        if os.path.exists(results):
            with open(results, encoding="utf-8") as f:
                for r in map(json.loads, f):
                    if r["method"] == "none" and isinstance(r.get("reply"), dict):
                        refs[(r["session"], r["index"])] = r["reply"]
        asked: Dict[Tuple[str, int, str], Tuple[JsonDict, JsonDict]] = {}
        methods = cm.csv(a.methods)
        if "none" in methods:                        # none first: its reply is the reference of the others
            methods = ["none"] + [m for m in methods if m != "none"]
        for p in points:
            req = reqs[p["session"]][p["index"]]
            full = req["messages"]
            for spec in methods:
                if (p["session"], p["index"], spec) in done:
                    continue
                short = method_fn(spec, a.window)(full, body_tokens({"tools": req.get("tools")}))
                # the same request as another method's at this point (under the drain line, or nothing of that kind to
                # prune): the same answers at temperature 0, so asked once
                same = hashlib.sha256(json.dumps(short, sort_keys=True).encode()).hexdigest()
                if (p["session"], p["index"], same) in asked:
                    nxt, probe = asked[(p["session"], p["index"], same)]
                else:
                    nxt = ask(base, key, req, short, a.answer_tokens)
                    probe = ask(base, key, req, short + [{"role": "user", "content": PROBE}], a.answer_tokens)
                    asked[(p["session"], p["index"], same)] = (nxt, probe)
                reply = {"role": "assistant", "content": msg_text(nxt["message"])[:300],
                         "tool_calls": nxt["message"].get("tool_calls") or []}
                if spec == "none":
                    refs[(p["session"], p["index"])] = reply
                ref = refs.get((p["session"], p["index"]))
                row = {"session": p["session"], "index": p["index"], "fraction": p["fraction"], "method": spec,
                       "tokens_full": p["tokens"], "prompt_n": nxt["prompt_n"], "cache_n": nxt["cache_n"],
                       "read_ms": nxt["prompt_ms"],
                       "next": score_next(nxt["message"], p["truth"]),
                       "same_as_none": score_next(nxt["message"], ref) if ref is not None else None,
                       "repeat": repeated(short, nxt["message"]),
                       "imitated": imitated(nxt["message"]),
                       "context_after": sum(est_tokens(m) for m in short) + body_tokens({"tools": req.get("tools")}),
                       "reply": reply, "finish": nxt.get("finish"),
                       "probe": score_probe(parse_probe(msg_text(probe["message"])), full),
                       "probe_answer": msg_text(probe["message"])[:2000],
                       "next_got": tool_calls(nxt["message"])[:1] or msg_text(nxt["message"])[:300],
                       "seconds": nxt["seconds"] + probe["seconds"]}
                with open(results, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row) + "\n")
                say(f"{p['session']} @{p['fraction']:.0%} {spec}: next {row['next']['tool']}/{row['next']['target']}, "
                    f"files {row['probe']['file_recall']:.2f}, {row['seconds']:.0f} s")
    finally:
        srv.stop()
    return 0


def mcnemar_p(b: int, c: int) -> float:
    """The exact two-sided McNemar p: b and c are the points where only one of the two methods was right."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    from math import comb
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n)


def paired(rows: Sequence[Mapping[str, Any]], method: str, seed: int = 7, draws: int = 2000) -> JsonDict:
    """A method against none on the same points: the McNemar p of the next target, and the difference in its rate
    with a 95% interval from resampling whole sessions (points of one session are not independent)."""
    import random
    key = (lambda r: (r["session"], r["index"]))
    base = {key(r): bool(r["next"]["target"]) for r in rows if r["method"] == "none"}
    mine = {key(r): bool(r["next"]["target"]) for r in rows if r["method"] == method}
    both = [k for k in mine if k in base]
    if not both:
        return {"n": 0, "diff": 0.0, "lo": 0.0, "hi": 0.0, "p": 1.0}
    b = sum(1 for k in both if base[k] and not mine[k])
    c = sum(1 for k in both if mine[k] and not base[k])
    by_session: Dict[str, List[Tuple[bool, bool]]] = {}
    for k in both:
        by_session.setdefault(k[0], []).append((mine[k], base[k]))
    names = sorted(by_session)
    rng = random.Random(seed)
    diffs = []
    for _ in range(draws):
        pts = [x for s in (rng.choice(names) for _ in names) for x in by_session[s]]
        diffs.append(sum(m - n for m, n in pts) / len(pts))
    diffs.sort()
    return {"n": len(both), "diff": (c - b) / len(both), "lo": diffs[int(0.025 * draws)],
            "hi": diffs[int(0.975 * draws) - 1], "p": mcnemar_p(b, c)}


def report(rows: Iterable[Mapping[str, Any]]) -> List[List[str]]:
    rows = list(rows)
    by: Dict[str, List[Mapping[str, Any]]] = {}
    for r in rows:
        by.setdefault(str(r["method"]), []).append(r)
    head = ["method", "points", "next tool", "next target", "against none (95%)", "p", "same as none",
            "files recall", "files precision", "invented", "errors recalled", "repeats", "imitated", "prompt tokens",
            "event re-read s"]
    out = [head]
    mean = (lambda xs: sum(xs) / len(xs) if xs else 0.0)
    speed = read_speed(rows)
    for name, rs in by.items():
        pr = paired(rows, name) if name != "none" else None
        same = [r["same_as_none"]["target"] for r in rs if isinstance(r.get("same_as_none"), dict)]
        out.append([name, str(len(rs)),
                    f"{mean([r['next']['tool'] for r in rs]):.0%}", f"{mean([r['next']['target'] for r in rs]):.0%}",
                    "" if pr is None else f"{pr['diff']:+.0%} ({pr['lo']:+.0%} to {pr['hi']:+.0%})",
                    "" if pr is None else f"{pr['p']:.2f}",
                    f"{mean(same):.0%}" if same and name != "none" else "",
                    f"{mean([r['probe']['file_recall'] for r in rs]):.2f}",
                    f"{mean([r['probe']['file_precision'] for r in rs]):.2f}",
                    f"{mean([r['probe']['invented'] for r in rs]):.1f}",
                    f"{mean([r['probe']['error'] for r in rs]):.0%}",
                    f"{mean([bool(r.get('repeat')) for r in rs]):.0%}",
                    f"{mean([bool(r.get('imitated')) for r in rs]):.0%}",
                    f"{mean([(r.get('prompt_n') or 0) + (r.get('cache_n') or 0) for r in rs]):,.0f}",
                    "" if name == "none" or not speed else
                    f"{mean([(r.get('prompt_n') or 0) + (r.get('cache_n') or 0) for r in rs]) / speed:.0f}"])
    return out


def read_speed(rows: Sequence[Mapping[str, Any]]) -> float:
    """The server's read speed (tokens/s): the median of the requests it read mostly anew. A compaction event is read
    again once, whole, at idle (user, 2026-10-13: one re-read per event, not per turn), so the report gives that
    time, not the bench's per-point reads (each point's pruned request differs from the cached one)."""
    fast = sorted(r["prompt_n"] / (r["read_ms"] / 1000) for r in rows
                  if (r.get("prompt_n") or 0) > 4000 and (r.get("read_ms") or 0) > 0
                  and (r.get("cache_n") or 0) < 0.2 * r["prompt_n"])
    return fast[len(fast) // 2] if fast else 0.0


def sample(rows: Sequence[Mapping[str, Any]], reqs: Mapping[str, List[JsonDict]], a: str, b: str, n: int = 20,
           window: int = WINDOW) -> List[str]:
    """Points where methods a and b chose a different next step, side by side, for a person to read (user,
    2026-10-13: the thinking prune's rationale is not scored well by code): the true next step, each method's, and the
    thinking blocks b changed (the end of the original, and what b kept)."""
    by = {(r["session"], r["index"], r["method"]): r for r in rows}
    points = sorted({(r["session"], r["index"]) for r in rows if r["method"] == a})
    out = [f"# {a} against {b}", ""]
    shown = 0
    for sess, idx in points:
        ra, rb = by.get((sess, idx, a)), by.get((sess, idx, b))
        if not ra or not rb or ra["next"]["target"] == rb["next"]["target"]:
            continue
        req = reqs[sess][idx]
        full = req["messages"]
        short = method_fn(b, window)(full, body_tokens({"tools": req.get("tools")}))
        out += [f"## {sess} request {idx} ({ra.get('fraction', 0):.0%})", "",
                f"- the real next step: {ra['next'].get('want', '')}",
                f"- {a}: {json.dumps(ra.get('next_got'))[:200]} ({'right' if ra['next']['target'] else 'wrong'})",
                f"- {b}: {json.dumps(rb.get('next_got'))[:200]} ({'right' if rb['next']['target'] else 'wrong'})", ""]
        changed = [(i, thinking(full[i]), thinking(short[i])) for i in range(len(full))
                   if full[i].get("role") == "assistant" and thinking(full[i]) != thinking(short[i])]
        for i, before, after in changed[-3:]:
            out += [f"thinking at message {i} ({int(len(before) / CHARS_PER_TOKEN)} tokens), its end:", "",
                    "> " + before.strip()[-400:].replace("\n", "\n> "), "", "kept: " + (after or "(nothing)"), ""]
        shown += 1
        if shown >= n:
            break
    return out


def cmd_sample(a: argparse.Namespace) -> int:
    with open(os.path.join(a.out, "scores.jsonl"), encoding="utf-8") as f:
        rows = [json.loads(x) for x in f]
    text = "\n".join(sample(rows, sessions(a.out), a.a, a.b, a.n, a.window)) + "\n"
    if a.md_out:
        with open(a.md_out, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        print(text)
    return 0


def cmd_report(a: argparse.Namespace) -> int:
    with open(os.path.join(a.out, "scores.jsonl"), encoding="utf-8") as f:
        table = report(map(json.loads, f))
    for i, row in enumerate(table):
        print(("| " + " | ".join(row) + " |") if a.md else "  ".join(row))
        if a.md and i == 0:
            print("|" + "---|" * len(row))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--home", required=True, help="the harness HOME (never yours)")
    common.add_argument("--model", default="qwen3.6-35b-a3b-iq3")
    common.add_argument("--port", type=int, default=8097)
    common.add_argument("--models-dir", default=os.path.expanduser("~/models/gguf"))
    p = sub.add_parser("prepare", parents=[common])
    p.add_argument("--opencode-version", default="1.19.0")
    p.add_argument("--pi-version", default="1.1.0")
    p.set_defaults(fn=cmd_prepare)
    c = sub.add_parser("collect", parents=[common])
    c.add_argument("--out", required=True)
    c.add_argument("--clients", default="opencode,pi")
    c.add_argument("--scripts", default=",".join(SCRIPTS))
    c.add_argument("--thinking", default="default", choices=("default", "off"),
                   help="default: the model's own, as CARL sets it up (on); off (the first runs, 2026-10-12)")
    c.add_argument("--target", type=int, default=TARGET,
                   help="a session stops once its context is this big; under it after the script, follow-ups")
    c.add_argument("--max-grow", type=int, default=24, help="the follow-ups after the script, at most")
    c.set_defaults(fn=cmd_collect)
    k = sub.add_parser("checkpoints")
    k.add_argument("out")
    k.add_argument("--window", type=int, default=WINDOW)
    k.add_argument("--at", default=",".join(str(x) for x in AT))
    k.add_argument("--range", default="", help="LO:HI: every request whose context fills this much of the window")
    k.add_argument("--every", type=int, default=1, help="with --range: every Nth of those requests")
    k.set_defaults(fn=cmd_checkpoints)
    s = sub.add_parser("score", parents=[common])
    s.add_argument("out")
    s.add_argument("--methods", default="none,mask:8:0.2")
    s.add_argument("--window", type=int, default=WINDOW, help="the window the event's sizes are shares of")
    s.add_argument("--answer-tokens", type=int, default=4096,
                   help="the next step's and the probe's limit (thinking needs room; 512 cut a write's path)")
    s.set_defaults(fn=cmd_score)
    sm = sub.add_parser("sample", help="points where two methods chose differently, side by side, to read")
    sm.add_argument("out")
    sm.add_argument("--a", required=True)
    sm.add_argument("--b", required=True)
    sm.add_argument("--n", type=int, default=20)
    sm.add_argument("--window", type=int, default=WINDOW)
    sm.add_argument("--md-out", default="")
    sm.set_defaults(fn=cmd_sample)
    r = sub.add_parser("report")
    r.add_argument("out")
    r.add_argument("--md", action="store_true")
    r.set_defaults(fn=cmd_report)
    a = ap.parse_args(argv)
    return int(a.fn(a))


if __name__ == "__main__":
    sys.exit(main())
