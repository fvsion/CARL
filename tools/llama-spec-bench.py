#!/usr/bin/env python3
"""Decode-speed benchmark for llama-server speculative-decoding modes.

Usage: python3 tools/llama-spec-bench.py LABEL [BASE_URL]
Runs three workloads (prose, fresh code, code re-emission/edit) with thinking
off and prints llama-server's own timings (prompt/predicted tok/s, draft
acceptance) per workload. BASE_URL defaults to http://192.168.42.1:8080.
"""
from __future__ import annotations

import argparse
import sys

from carl_bench import DEFAULT_BASE, Message, Timings, chat_body, completion_from, post_chat, read_api_key, \
    read_source, validate_base


def make_workloads() -> dict[str, str]:
    edit_src = "\n".join(read_source(__file__).splitlines() * 3)  # this file x3: code to re-emit
    workloads = {
        "prose": "Explain how a B-tree insertion works, in detail.",
        "code": "Write a complete Python module implementing an LRU cache with TTL expiry, "
                "thread safety, type hints and docstrings. Code only.",
        "edit": "Return the following file in full, unchanged except rename the variable "
                "`workloads` to `tasks` everywhere. Output only the code.\n\n" + edit_src,
    }
    return workloads


def format_row(label: str, name: str, t: Timings) -> str:
    acc = f" draft {t.get('draft_n_accepted')}/{t.get('draft_n')} accepted" if t.get("draft_n") else ""
    return (f"{label:24} {name:5} prompt {t.get('prompt_n'):>5} @ {t.get('prompt_per_second', 0):6.1f} tok/s | "
            f"gen {t.get('predicted_n'):>4} @ {t.get('predicted_per_second', 0):5.1f} tok/s{acc}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    ap.add_argument("label", help="name printed in front of each result row")
    ap.add_argument("base", nargs="?", default=DEFAULT_BASE, help=f"server URL (default {DEFAULT_BASE})")
    a = ap.parse_args(argv)
    base, key = validate_base(a.base), read_api_key()
    for name, prompt in make_workloads().items():
        msgs: list[Message] = [{"role": "user", "content": prompt}]
        body = chat_body(msgs, 400, temperature=1.0, top_p=0.95, top_k=20)
        c = completion_from(post_chat(base, key, body, timeout=1800))
        print(format_row(a.label, name, c.timings), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
