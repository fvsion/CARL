#!/usr/bin/env python3
"""One A/B measurement against a freshly started llama-server (empty cache).
Prefill ~12K tokens cold, then generate 200 tokens at that context.
Usage: python3 tools/llama-ab-measure.py LABEL [BASE_URL]"""
from __future__ import annotations

import argparse
import sys

from carl_bench import DEFAULT_BASE, Message, chat_body, completion_from, post_chat, read_api_key, repo_sources, \
    validate_base

PROMPT_CHARS = 46000            # ~12K tokens of this repo's source


def build_prompt(files: dict[str, str], limit: int = PROMPT_CHARS) -> str:
    """The smallest FILES (path -> source) that fit LIMIT chars, plus the question."""
    buf: list[str] = []
    n = 0
    for rel, t in files.items():
        if n + len(t) > limit:
            continue
        buf.append(f"### {rel}\n{t}")
        n += len(t)
    return "\n\n".join(buf) + "\n\nWrite a detailed summary of what these files do."


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="One cold-prefill + decode A/B measurement against llama-server.")
    ap.add_argument("label", help="name printed in front of the result")
    ap.add_argument("base", nargs="?", default=DEFAULT_BASE, help=f"server URL (default {DEFAULT_BASE})")
    a = ap.parse_args(argv)
    base, key = validate_base(a.base), read_api_key()
    msgs: list[Message] = [{"role": "user", "content": build_prompt(repo_sources())}]
    t = completion_from(post_chat(base, key, chat_body(msgs, 200), timeout=3600)).timings
    print(f"{a.label:22} prefill {t.get('prompt_n')} tok @ {t.get('prompt_per_second', 0):6.1f} tok/s | "
          f"gen@ctx {t.get('predicted_n')} @ {t.get('predicted_per_second', 0):5.2f} tok/s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
