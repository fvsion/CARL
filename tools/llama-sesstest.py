#!/usr/bin/env python3
"""Multi-turn same-session long-context test against llama-server.

Usage: python3 tools/llama-sesstest.py [BASE_URL]
Turns: a ~56K-token prompt (this repo's smallest source files), a short
follow-up, then ~7-8K-token appends (its largest files), printing llama-server
timings (cached/processed prompt tokens, prompt & gen tok/s) and the server's
RSS + system wired memory after each turn.
"""
from __future__ import annotations

import argparse
import sys
import time
import urllib.error

from carl_bench import DEFAULT_BASE, Message, chat_body, completion_from, port_of, post_chat, read_api_key, \
    repo_sources, server_memory, validate_base

PROMPT_CHARS = 215000           # ~56K tokens
APPEND_CHARS = 30000
APPENDS = 4                     # turns t3..t6


def mem(port: int) -> str:
    m = server_memory(port)
    return f"rss={m.rss_gib:.1f}G wired={m.wired_gib:.1f}G swap_used={m.swap_used}"


def first_messages(files: dict[str, str], limit: int = PROMPT_CHARS) -> list[Message]:
    """The opening prompt: the smallest FILES (path -> source, smallest first) that fit LIMIT chars."""
    buf: list[str] = []
    n = 0
    for rel, t in files.items():
        if n + len(t) > limit:
            continue
        buf.append(f"### FILE: {rel}\n{t}")
        n += len(t)
    return [{"role": "system", "content": "You are a code reviewer."},
            {"role": "user", "content": "\n\n".join(buf)
             + "\n\nIn one sentence: which file above looks most related to memory planning?"}]


def turns(files: dict[str, str]) -> list[tuple[str, str | None]]:
    """t1 the prompt as is, t2 a short follow-up, t3-t6 one of the largest files each."""
    big = list(files.values())[-APPENDS:][::-1]
    out: list[tuple[str, str | None]] = [
        ("t1", None), ("t2", "Now name one other file above that deals with settings, in one sentence.")]
    for i, text in enumerate(big, start=3):
        out.append((f"t{i}", "Here is one more file:\n" + text[:APPEND_CHARS] + "\n\nOne sentence: what does it do?"))
    return out


def call(base: str, key: str, msgs: list[Message], tag: str) -> str | None:
    port = port_of(base)
    t0 = time.time()
    try:
        c = completion_from(post_chat(base, key, chat_body(msgs, 60), timeout=3600))
    except urllib.error.HTTPError as e:
        print(tag, "HTTP", e.code, e.read()[:300], mem(port), flush=True)
        return None
    t, u = c.timings, c.usage
    print(f"{tag} OK {time.time() - t0:6.1f}s prompt_total={u.get('prompt_tokens')} cached={t.get('cache_n')} "
          f"processed={t.get('prompt_n')} @ {t.get('prompt_per_second', 0):.0f} tok/s | gen {t.get('predicted_n')} "
          f"@ {t.get('predicted_per_second', 0):.1f} tok/s | {mem(port)}", flush=True)
    return c.content


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Multi-turn same-session long-context test against llama-server.")
    ap.add_argument("base", nargs="?", default=DEFAULT_BASE, help=f"server URL (default {DEFAULT_BASE})")
    a = ap.parse_args(argv)
    base, key = validate_base(a.base), read_api_key()
    files = repo_sources()
    msgs = first_messages(files)
    for tag, q in turns(files):
        if q:
            msgs.append({"role": "user", "content": q})
        answer = call(base, key, msgs, tag)
        if answer is None:
            break
        msgs.append({"role": "assistant", "content": answer})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
