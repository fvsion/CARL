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
CHARS_PER_TOKEN = 3.6
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
SCRIPTS: Dict[str, Tuple[str, List[str]]] = {
    "pylib": ("pylib", ["question-review", "small-test", "stuck-zerodiv", "medium-change", "medium-module",
                        "large-module", "stuck-test", "small-newtest", "large-cli", *FOLLOW_UPS]),
    "pycli": ("pycli", ["question-explain", "small-rename", "small-flag", "medium-command", "stuck-keyerror",
                        "question-error", "large-feature", "small-gitignore", *FOLLOW_UPS]),
}
PROBE = ("Stop for a moment and answer from what you know so far, without calling any tool. Reply with only a JSON "
         "object: {\"files_changed\": [the paths of every file you created or changed in this session], "
         "\"last_error\": \"the exact text of the last failing test or error message you saw in this session, even if "
         "you fixed it since; empty only if there was none\", \"next_step\": \"what you will do next, in one sentence\"}. "
         "Paths relative to the project's root.")
ERROR_RE = re.compile(r"(Traceback \(most recent call last\)|\w*Error\b|\w*Exception\b|\berror:|\bFAILED\b|"
                      r"\b\d+ failed\b|[Ee]xit code:? [1-9])")
NAMED_ERROR_RE = re.compile(r"\w+(Error|Exception)\b|\berror:")
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


def last_error(messages: Sequence[Mapping[str, Any]]) -> str:
    """The last line that names an error (else the last that says one), of the last command result that has one."""
    commands = command_results(messages)
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m.get("role") != "tool" or i not in commands:
            continue
        text = msg_text(m)
        if ERROR_RE.search(text):
            lines = [ln.strip() for ln in text.splitlines() if ERROR_RE.search(ln)]
            named = [ln for ln in lines if NAMED_ERROR_RE.search(ln)]      # "ZeroDivisionError: ..." over "1 failed"
            return (named or lines or [""])[-1]
    return ""


def all_paths(messages: Sequence[Mapping[str, Any]]) -> set:
    """Every path-like string anywhere in the transcript (an answer's path not among them is made up)."""
    seen = set()
    for m in messages:
        for p in PATH_RE.findall(msg_text(m) + " " + json.dumps(m.get("tool_calls") or [])):
            seen.add(norm_path(p))
    return seen


# --------------------------------------------------------------------------------------------------------- the methods
def est_tokens(m: Mapping[str, Any]) -> int:
    return int(len(msg_text(m) + json.dumps(m.get("tool_calls") or [])) / CHARS_PER_TOKEN)


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
    commands = command_results(out)
    err = next((i for i in reversed(tools) if i in commands and ERROR_RE.search(msg_text(out[i]))), None)
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


def method_fn(spec: str) -> Callable[[Sequence[Mapping[str, Any]]], List[JsonDict]]:
    """A method by name: none, mask:K:S."""
    parts = spec.split(":")
    if parts[0] == "none":
        return lambda ms: [dict(m) for m in ms]
    if parts[0] == "mask" and len(parts) == 3:
        k, s = int(parts[1]), float(parts[2])
        return lambda ms: mask(ms, k, s)
    raise SystemExit(f"error: no such method {spec!r} (none, mask:K:S)")


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
    """The next step against the truth: the same tool, the same target (file, command, pattern)."""
    g, t = tool_calls(got), tool_calls(truth)
    if not t:
        return {"tool": not g, "target": not g, "got": "text" if not g else g[0]["name"], "want": "text"}
    if not g:
        return {"tool": False, "target": False, "got": "text", "want": t[0]["name"]}
    return {"tool": g[0]["name"] == t[0]["name"], "target": call_target(g[0]) == call_target(t[0]),
            "got": f"{g[0]['name']} {call_target(g[0])}", "want": f"{t[0]['name']} {call_target(t[0])}"}


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


def error_recalled(said: str, truth: str) -> bool:
    """The last error named: the failing test, or the file and line, from the truth's line (a paraphrase of the rest
    counts); no error and an empty answer agree."""
    if not truth:
        return not said.strip()
    keys = ERROR_KEYS_RE.findall(truth)
    if keys:
        return any(k in said for k in keys)
    return bool(said) and (truth[:60] in said or said[:60] in truth)


def score_probe(answer: Optional[Mapping[str, Any]], full: Sequence[Mapping[str, Any]]) -> JsonDict:
    """The probe's answer against code's reading of the whole transcript up to the point."""
    want = files_changed(full)
    err = last_error(full)
    known = all_paths(full)
    if answer is None:
        return {"parsed": False, "file_recall": 0.0, "file_precision": 0.0, "invented": 0, "error": False}
    got = [norm_path(str(p)) for p in (answer.get("files_changed") or []) if isinstance(p, str)]
    hit = [p for p in got if p in want]
    invented = [p for p in got if p not in known]
    said = str(answer.get("last_error") or "")
    return {"parsed": True,
            "file_recall": len(set(hit)) / len(want) if want else (1.0 if not got else 0.0),
            "file_precision": len(set(hit)) / len(set(got)) if got else (1.0 if not want else 0.0),
            "invented": len(set(invented)),
            "error": error_recalled(said, err)}


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
    msg = ((r.get("choices") or [{}])[0].get("message")) or {}
    t = r.get("timings") or {}
    return {"message": msg, "seconds": round(time.time() - t0, 2), "prompt_n": t.get("prompt_n"),
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


def _drivers(client: str, home: str, cwd: str, env: Mapping[str, str], model: str):  # type: ignore[no-untyped-def]
    import cachematrix as cm
    cm.TURN_LIMIT = TASK_LIMIT
    return cm.OpenCodeDriver(home, cwd, env, model, False, "") if client == "opencode" else cm.PiDriver(home, cwd, env, model, False)


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
                drv = _drivers(client, home, cwd, env, a.model)
                tasks = []
                try:
                    time.sleep(1)
                    drv.start()
                    sid = drv.new()
                    for pid in ids:
                        t0 = time.time()
                        try:
                            reply = drv.say(sid, prompts[pid].text if pid in prompts else pid)
                        except Exception as e:  # noqa: BLE001  (one task's failure is data; the session goes on)
                            reply = f"error: {e}"
                        tasks.append({"task": pid[:60], "seconds": round(time.time() - t0, 1), "reply": reply[:200]})
                        say(f"{name}: {pid[:40]} in {time.time() - t0:.0f} s")
                finally:
                    drv.stop()
                    proxy.terminate()
                    proxy.wait(10)
                with open(os.path.join(folder, "done.json"), "w", encoding="utf-8") as f:
                    json.dump({"client": client, "script": script, "fixture": fixture, "tasks": tasks,
                               "model": a.model, "cwd": cwd}, f, indent=1)
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
        for c in checkpoints(name, reqs, a.window, [float(x) for x in a.at.split(",")]):
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
        for p in points:
            req = reqs[p["session"]][p["index"]]
            full = req["messages"]
            for spec in cm.csv(a.methods):
                if (p["session"], p["index"], spec) in done:
                    continue
                short = method_fn(spec)(full)
                nxt = ask(base, key, req, short, 512)
                probe = ask(base, key, req, short + [{"role": "user", "content": PROBE}], 800)
                row = {"session": p["session"], "index": p["index"], "fraction": p["fraction"], "method": spec,
                       "tokens_full": p["tokens"], "prompt_n": nxt["prompt_n"], "cache_n": nxt["cache_n"],
                       "read_ms": nxt["prompt_ms"],
                       "next": score_next(nxt["message"], p["truth"]),
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


def report(rows: Iterable[Mapping[str, Any]]) -> List[List[str]]:
    by: Dict[str, List[Mapping[str, Any]]] = {}
    for r in rows:
        by.setdefault(str(r["method"]), []).append(r)
    head = ["method", "points", "next tool", "next target", "files recall", "files precision", "invented",
            "last error", "prompt tokens", "tokens read", "read s"]
    out = [head]
    mean = (lambda xs: sum(xs) / len(xs) if xs else 0.0)
    for name, rs in by.items():
        out.append([name, str(len(rs)),
                    f"{mean([r['next']['tool'] for r in rs]):.0%}", f"{mean([r['next']['target'] for r in rs]):.0%}",
                    f"{mean([r['probe']['file_recall'] for r in rs]):.2f}",
                    f"{mean([r['probe']['file_precision'] for r in rs]):.2f}",
                    f"{mean([r['probe']['invented'] for r in rs]):.1f}",
                    f"{mean([r['probe']['error'] for r in rs]):.0%}",
                    f"{mean([(r.get('prompt_n') or 0) + (r.get('cache_n') or 0) for r in rs]):,.0f}",
                    f"{mean([r.get('prompt_n') or 0 for r in rs]):,.0f}",
                    f"{mean([(r.get('read_ms') or 0) / 1000 for r in rs]):.1f}"])
    return out


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
    c.set_defaults(fn=cmd_collect)
    k = sub.add_parser("checkpoints")
    k.add_argument("out")
    k.add_argument("--window", type=int, default=WINDOW)
    k.add_argument("--at", default=",".join(str(x) for x in AT))
    k.set_defaults(fn=cmd_checkpoints)
    s = sub.add_parser("score", parents=[common])
    s.add_argument("out")
    s.add_argument("--methods", default="none,mask:4:0.3,mask:8:0.3,mask:4:0.5")
    s.set_defaults(fn=cmd_score)
    r = sub.add_parser("report")
    r.add_argument("out")
    r.add_argument("--md", action="store_true")
    r.set_defaults(fn=cmd_report)
    a = ap.parse_args(argv)
    return int(a.fn(a))


if __name__ == "__main__":
    sys.exit(main())
