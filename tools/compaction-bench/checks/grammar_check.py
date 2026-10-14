#!/usr/bin/env python3
"""Ranked change 9 (Phase 23.5.2; user, 2026-10-14: "run #9"): with a Qwen thinking model on llama.cpp, does output
held to a grammar (a JSON schema) land in the reply, or in reasoning_content (llama.cpp issues #20345 and #24807)?
Stage 2's summary may use a grammar only after the thinking; this checks where the constrained text goes, with thinking
on and off, through response_format (json_schema) and through a raw GBNF grammar.

    python3 grammar_check.py http://127.0.0.1:8199     # a llama-server started as CARL starts it
"""
from __future__ import annotations

import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8199"
SCHEMA = {"type": "object", "properties": {"goal": {"type": "string"}, "files": {"type": "array", "items": {"type": "string"}},
                                           "next_step": {"type": "string"}}, "required": ["goal", "files", "next_step"]}
GBNF = 'root ::= "{\\"goal\\": \\"" [a-zA-Z ]+ "\\", \\"next_step\\": \\"" [a-zA-Z ]+ "\\"}"'


def post(body: dict) -> dict:
    r = urllib.request.Request(BASE + "/v1/chat/completions", data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=900) as f:
        return json.load(f)


def main() -> int:
    msgs = [{"role": "system", "content": "You are a coding agent."},
            {"role": "user", "content": "We fixed mean() in textstats/stats.py and added tests in tests/test_stats.py. "
                                        "Summarize the session as JSON: the goal, the files changed, the next step."}]
    for think in (True, False):
        for how, extra in (("response_format json_schema", {"response_format": {"type": "json_schema", "json_schema":
                                                                                  {"name": "s", "schema": SCHEMA}}}),
                           ("grammar (GBNF)", {"grammar": GBNF}), ("no constraint", {})):
            body = {"messages": msgs, "temperature": 0, "max_tokens": 3000, "cache_prompt": False,
                    "chat_template_kwargs": {"enable_thinking": think, "preserve_thinking": True}, **extra}
            try:
                r = post(body)
                m = r["choices"][0]["message"]
                c, rc = m.get("content") or "", m.get("reasoning_content") or ""
                def parses(t: str) -> bool:
                    try:
                        json.loads(t.strip())
                        return True
                    except ValueError:
                        return False
                print(f"thinking {'on ' if think else 'off'} | {how:28} | finish {r['choices'][0]['finish_reason']:6} | "
                      f"content {len(c):5} chars, JSON {parses(c)!s:5} | reasoning {len(rc):5} chars, JSON "
                      f"{parses(rc)!s:5} | content starts {c[:60]!r} | reasoning starts {rc[:60]!r}")
            except Exception as e:  # noqa: BLE001  (a refused request is a result too)
                print(f"thinking {'on ' if think else 'off'} | {how:28} | error {str(e)[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
