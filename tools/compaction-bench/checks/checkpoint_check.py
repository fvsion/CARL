#!/usr/bin/env python3
"""Ranked change 14 (Phase 23.5.2; user, 2026-10-14: "worth looking into but we don't just take their defaults"; "run
#14"): CARL starts llama-server with 8 context checkpoints a slot, at least 4,096 tokens apart; llama.cpp 0.6.0's own
defaults are 32 and 8,192. On a hybrid model (the A3B: a recurrent state that cannot be rolled back) a request that
differs from the slot's state at token P is read again from the last checkpoint before P. This measures, on one real
agent request (~58K tokens), how many tokens are read again after a change at several depths, and after the compaction
event's own edits (the bench's method), for each setting; the RAM cache off so it cannot stand in. Run by
checkpoint_check.sh, one llama-server per setting.

    python3 checkpoint_check.py http://127.0.0.1:8199 REQUEST.json SETTING-NAME OUT.jsonl
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import compbench as cb  # noqa: E402

BASE, REQ, NAME, OUT = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]


def ask(messages: list, tools: list) -> dict:
    body = {"messages": messages, "tools": tools, "temperature": 0, "max_tokens": 1, "cache_prompt": True, "id_slot": 0,
            "chat_template_kwargs": {"enable_thinking": True, "preserve_thinking": True}}
    r = urllib.request.Request(BASE + "/v1/chat/completions", data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(r, timeout=1800) as f:
        doc = json.load(f)
    t = doc.get("timings") or {}
    return {"read": t.get("prompt_n"), "cached": t.get("cache_n"), "read_s": round((t.get("prompt_ms") or 0) / 1000, 1),
            "wall_s": round(time.time() - t0, 1)}


def changed_at(messages: list, share: float) -> list:
    """The messages with one tool result changed (a placeholder), the first one at or past share of the messages'
    tokens: a change at that depth."""
    out = [dict(m) for m in messages]
    sizes = [cb.est_tokens(m) for m in out]
    total, acc = sum(sizes), 0
    for i, m in enumerate(out):
        acc += sizes[i]
        if acc >= share * total and m.get("role") == "tool":
            m["content"] = "[pruned] a tool result changed here."
            return out
    return out


def main() -> int:
    with open(REQ, encoding="utf-8") as f:
        body = json.load(f)
    msgs, tools = body["messages"], body.get("tools") or []
    rows = []

    def step(label: str, messages: list) -> None:
        r = dict(ask(messages, tools), setting=NAME, step=label)
        rows.append(r)
        print(json.dumps(r), flush=True)
        with open(OUT, "a", encoding="utf-8") as f:
            f.write(json.dumps(r) + "\n")
    step("first read", msgs)
    for share in (0.5, 0.75, 0.9, 0.97):
        step(f"a change at {share:.0%}", changed_at(msgs, share))
        step("back to the original", msgs)
    event = cb.method_fn("mask:8:0.2+turn+ph=facts+args+think=last", 98304)(msgs, cb.body_tokens({"tools": tools}))
    step("the compaction event's edits", event)
    step("the next turn after the event (one more message)", event + [{"role": "user", "content": "Go on."}])
    return 0


if __name__ == "__main__":
    sys.exit(main())
