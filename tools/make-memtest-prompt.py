#!/usr/bin/env python3
"""Build the ~56K-token long-context test prompt used by sesstest.py and
llama-sesstest.py.

Usage: python3 tools/make-memtest-prompt.py OUT_DIR
Concatenates the smallest MTPLX source files (~207K chars) into
OUT_DIR/memtest-msgs.json.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

from carl_bench import Message, mtplx_source_dir, read_source

PROMPT_CHARS = 215000


def build_messages(site: str, limit: int = PROMPT_CHARS) -> tuple[list[Message], int, int]:
    """(messages, chars, files): the smallest files of SITE that fit LIMIT chars."""
    buf: list[str] = []
    n = 0
    for f in sorted(glob.glob(site + "/*.py"), key=os.path.getsize):
        t = read_source(f)
        if n + len(t) > limit:
            continue
        buf.append(f"### FILE: {os.path.basename(f)}\n{t}")
        n += len(t)
    msgs: list[Message] = [
        {"role": "system", "content": "You are a code reviewer."},
        {"role": "user", "content": "\n\n".join(buf) + "\n\nIn one sentence: which file above looks most related to memory planning?"}]
    return msgs, n, len(buf)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Write OUT_DIR/memtest-msgs.json, the long-session test prompt.")
    ap.add_argument("out_dir")
    a = ap.parse_args(argv)
    if not os.path.isdir(a.out_dir):
        raise SystemExit(f"error: no such directory: {a.out_dir}")
    msgs, n, files = build_messages(mtplx_source_dir())
    with open(os.path.join(a.out_dir, "memtest-msgs.json"), "w", encoding="utf-8") as f:
        json.dump(msgs, f)
    print(n, "chars,", files, "files")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
