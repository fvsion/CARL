#!/usr/bin/env python3
"""Capture the real event streams of OpenCode and Pi for the parser tests (tests/agent_bench/events/).

    python3 tests/agent_bench/capture_events.py [CLIENT_HOME]

It copies a client HOME (see _support.client_home), points the copy to a fake server, and runs each client once
for each decision (delegated, self, answer, error). It writes CLIENT-DECISION.jsonl (the JSON events, with the
paths replaced) and CLIENT-DECISION.log (OpenCode's log lines about tools and errors). No model runs."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from typing import Any, List, Tuple

import _support as sp

import fakeserver as fs
from agentbench import clients as cl, events as ev, fixtures


def short_system(value: Any) -> Any:
    """The system prompt's sections cut to their start: the tests need the event shapes, not Pi's prompt."""
    if isinstance(value, list):
        return [short_system(v) for v in value]
    if not isinstance(value, dict):
        return value
    out = {k: short_system(v) for k, v in value.items()}
    if out.get("role") == "system" and isinstance(out.get("sections"), dict):
        out["sections"] = {k: (v[:80] + " ...[cut]" if isinstance(v, str) and len(v) > 80 else v)
                           for k, v in out["sections"].items()}
    return out


def sanitize(text: str, paths: List[Tuple[str, str]]) -> str:
    for old, new in paths:
        text = text.replace(old, new)
    return text


def main() -> int:
    src = sys.argv[1] if len(sys.argv) > 1 else sp.client_home()
    if not src:
        print("no client HOME: set AGENT_BENCH_CLIENT_HOME", file=sys.stderr)
        return 1
    tmp = tempfile.mkdtemp(prefix="ab-capture-")
    try:
        port = cl.free_port()
        home = sp.clone_home(src, os.path.join(tmp, "home"), port)
        env = cl.client_env(home)
        os.makedirs(sp.EVENTS, exist_ok=True)
        for client in ev.CLIENTS:
            for name in sp.SCENARIOS:
                srv = fs.FakeServer(fs.Script.from_json(sp.scenario_script(name)), port).start()
                try:
                    repo = fixtures.make_fresh("pylib" if name == "delegated" else "pycli", os.path.join(tmp, "runs"))
                    spec = cl.RunSpec(client, sp.MODEL, "default", sp.PROMPTS[name], repo, home, 180)
                    lines: List[Tuple[str, str]] = []
                    out = cl.run_decision(spec, env, record=lines)
                finally:
                    srv.stop()
                paths = [(os.path.realpath(repo), "/tmp/agent-bench/notes"), (repo, "/tmp/agent-bench/notes"),
                         (os.path.realpath(home), "/tmp/agent-bench/home"), (home, "/tmp/agent-bench/home"),
                         (os.path.realpath(tmp), "/tmp/agent-bench"), (tmp, "/tmp/agent-bench")]
                base = os.path.join(sp.EVENTS, f"{client}-{name}")
                with open(base + ".jsonl", "w", encoding="utf-8") as f:
                    for stream, text in lines:
                        doc = ev.parse_line(text) if stream == "out" else None
                        if doc is not None:
                            doc = json.loads(sanitize(json.dumps(doc, ensure_ascii=False), paths))
                            doc = ev.trim(short_system(doc), max_str=1500, max_items=200, depth=14)
                            f.write(json.dumps(doc, ensure_ascii=False) + "\n")
                log = [sanitize(t.rstrip("\n"), paths) for s, t in lines
                       if s == "err" and ("message=evaluated permission" in t or "level=ERROR" in t)]
                if log:
                    with open(base + ".log", "w", encoding="utf-8") as f:
                        f.write("\n".join(log) + "\n")
                print(f"{client} {name}: strict {out.decision}, practical {out.practical.decision} after "
                      f"{out.practical.looks} looks ({out.seconds:.1f} s) -> {base}.jsonl")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
