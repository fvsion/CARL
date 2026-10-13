#!/usr/bin/env python3
"""Does OpenCode, does Pi wait for a reply that goes silent mid-stream (carl-compaction holding it while it compacts),
with and without SSE keep-alive comments? (Phase 23.5.2; user, 2026-10-13: "run the keep-alive test here".) Runs the
real clients of an agent-bench harness HOME (never yours) against hold_server.py on the port the HOME's clients use,
one prompt each ("HOLDTEST <seconds> [KA]"): the reply is whole when the client prints "Part two.".

    python3 hold_test.py --home HARNESS_HOME --port 8097 --holds 130,330 --out hold-results.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "agent-bench"))
from agentbench import clients as cl, home as hm  # noqa: E402

MODEL = "qwen3.6-35b-a3b-iq3"


def run(client: str, home: str, hold: int, keep: bool, cwd: str) -> dict:
    env = cl.client_env(home)
    prompt = f"HOLDTEST {hold}{' KA' if keep else ''}: reply to this message."
    argv = (["opencode", "run", "-m", f"{cl.PROVIDER}/{MODEL}", prompt] if client == "opencode"
            else ["pi", "-p", "--no-session", "--model", f"{cl.PROVIDER}/{MODEL}:off", prompt])
    t0 = time.time()
    try:
        p = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=hold + 240)
        out, code = p.stdout + p.stderr, p.returncode
    except subprocess.TimeoutExpired as e:
        out, code = f"(timed out) {e.stdout or ''}{e.stderr or ''}", -1
    return {"client": client, "hold": hold, "keepalive": keep, "exit": code, "seconds": round(time.time() - t0, 1),
            "whole": "Part two." in out, "part_one": "Part one." in out, "tail": out.strip()[-300:]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True)
    ap.add_argument("--port", type=int, default=8097)
    ap.add_argument("--holds", default="130,330")
    ap.add_argument("--out", default="hold-results.jsonl")
    a = ap.parse_args()
    home = hm.require_harness_home(a.home)
    srv = subprocess.Popen([sys.executable, os.path.join(HERE, "hold_server.py"), "--port", str(a.port),
                            "--log", os.path.splitext(a.out)[0] + "-server.jsonl"])
    try:
        time.sleep(1.5)
        cwd = tempfile.mkdtemp(prefix="holdtest-")
        cases = [(int(h), k) for h in a.holds.split(",") for k in (False, True)]

        def client_runs(client: str) -> None:       # one client's cases in turn (two OpenCode runs lock its database)
            for hold, keep in cases:
                res = run(client, home, hold, keep, cwd)
                print(json.dumps(res), flush=True)
                with open(a.out, "a", encoding="utf-8") as f:
                    f.write(json.dumps(res) + "\n")
        with ThreadPoolExecutor(max_workers=2) as pool:            # the two clients side by side
            list(pool.map(client_runs, ("opencode", "pi")))
    finally:
        srv.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
