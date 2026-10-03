#!/usr/bin/env python3
"""Long-context KV-cache A/B measurement for a freshly started llama-server.

Usage: python3 tools/llama-kv-longctx.py LABEL [TARGET_TOKENS=65536] [BASE_URL]

Builds a deterministic ~TARGET_TOKENS haystack (MTPLX source code) with 8
"needle" facts at fixed depths (2%..98%), then runs one session:
  t1  cold prefill of the whole haystack + question: recall all 8 needles
      -> prefill tok/s, recall score (the q4 K-cache quality risk is
         long-range recall, so this is the quality proxy)
  t2  append a short (~0.5K-token) follow-up and generate up to 400 tokens of prose
      at long context -> append prefill tok/s, decode tok/s at ~TARGET ctx,
      speculative acceptance
  t3  short follow-up that re-emits a function from deep in the haystack
      (n-gram friendly) -> decode tok/s at long context on re-emitted text
Server RSS / wired / swap are sampled after each turn. Thinking is off so
decode numbers aren't dominated by variable reasoning length. Sampling is the
production default (temp 1.0), so expect +-5% run-to-run noise on tok/s.
"""
from __future__ import annotations

import argparse
import glob
import os
import random
import re
import sys
import time
from dataclasses import dataclass

from carl_bench import DEFAULT_BASE, Message, chat_body, completion_from, mtplx_source_dir, port_of, post_chat, \
    read_api_key, read_source, server_memory, validate_base

CHARS_PER_TOKEN = 3.7          # measured on this corpus (207K chars ~ 56K tokens)
DEPTHS = (0.02, 0.10, 0.25, 0.40, 0.55, 0.70, 0.85, 0.98)


@dataclass(frozen=True)
class Haystack:
    text: str
    parts: list[str]                    # the files, "### FILE: path" + source
    needles: list[tuple[str, str]]      # (vault name, access code)
    deep_functions: list[str]           # functions defined ~20-30% deep


def build_haystack(files: dict[str, str], target_tokens: int, seed: int = 42) -> Haystack:
    """Deterministic haystack from FILES (relative path -> source, smallest first)
    with needles at exact character depths (snapped to the next line break)."""
    budget = int(target_tokens * CHARS_PER_TOKEN)
    parts: list[str] = []
    n = 0
    for rel, t in files.items():                # many small files, smallest first
        if n + len(t) > budget:
            break
        parts.append(f"### FILE: {rel}\n{t}")
        n += len(t)
    haystack = "\n\n".join(parts)
    rng = random.Random(seed)
    needles: list[tuple[str, str]] = []
    inserts: list[tuple[int, str]] = []
    for i, depth in enumerate(DEPTHS):
        code = f"{rng.randint(1000, 9999)}-{rng.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}{rng.randint(10, 99)}"
        needles.append((f"vault-{i + 1}", code))
        pos = haystack.find("\n", int(depth * len(haystack))) + 1
        inserts.append((pos, f"# NOTE: the access code for vault-{i + 1} is {code}.\n"))
    for pos, text in sorted(inserts, reverse=True):
        haystack = haystack[:pos] + text + haystack[pos:]
    fifth = haystack[int(0.2 * len(haystack)):int(0.3 * len(haystack))]
    return Haystack(haystack, parts, needles, re.findall(r"\ndef (\w{6,})\(", fifth))


def corpus(site: str) -> dict[str, str]:
    files = sorted(glob.glob(site + "/**/*.py", recursive=True), key=lambda f: (os.path.getsize(f), f))
    return {os.path.relpath(f, site): read_source(f) for f in files}


class Session:
    """One conversation with the server; each call prints its timings and memory."""

    def __init__(self, base: str, key: str, label: str, first: str) -> None:
        self.base, self.key, self.label = base, key, label
        self.port = port_of(base)
        self.msgs: list[Message] = [{"role": "user", "content": first}]

    def ask(self, tag: str, max_tokens: int, question: str | None = None) -> str:
        if question is not None:
            self.msgs.append({"role": "user", "content": question})
        t0 = time.time()
        c = completion_from(post_chat(self.base, self.key, chat_body(self.msgs, max_tokens), timeout=7200))
        t = c.timings
        m = server_memory(self.port)
        acc = f" spec {t.get('draft_n_accepted')}/{t.get('draft_n')}" if t.get("draft_n") else ""
        print(f"{self.label:18} {tag}: ctx={c.usage.get('prompt_tokens')} cached={t.get('cache_n')} "
              f"read {t.get('prompt_n')} @ {t.get('prompt_per_second', 0):.1f} tok/s | "
              f"gen {t.get('predicted_n')} @ {t.get('predicted_per_second', 0):.2f} tok/s{acc} | "
              f"{time.time() - t0:.0f}s | rss={m.rss_gib:.1f}G wired={m.wired_gib:.1f}G swap={m.swap_used}", flush=True)
        self.msgs.append({"role": "assistant", "content": c.content})
        return c.content


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Long-context KV-cache A/B measurement (needle recall, decode, re-emit).")
    ap.add_argument("label", help="name printed in front of each result row")
    ap.add_argument("target", nargs="?", type=int, default=65536, help="haystack size in tokens (default 65536)")
    ap.add_argument("base", nargs="?", default=DEFAULT_BASE, help=f"server URL (default {DEFAULT_BASE})")
    a = ap.parse_args(argv)
    if a.target < 1024:
        raise SystemExit("error: TARGET_TOKENS must be at least 1024")
    base, key = validate_base(a.base), read_api_key()
    h = build_haystack(corpus(mtplx_source_dir()), a.target)

    s = Session(base, key, a.label, h.text + "\n\nThe text above contains access codes for vault-1 "
                "through vault-8 in comments. Reply with ONLY a JSON object mapping each vault name to its code.")
    a1 = s.ask("t1 recall ", 200)
    got = sum(1 for _, code in h.needles if code in a1)
    print(f"{a.label:18} recall: {got}/{len(h.needles)} needles correct", flush=True)

    s.ask("t2 decode ", 400, "Thanks. Now, drawing on the code above, write a ~400-word explanation of how "
          "these modules fit together, as plain prose. " + " ".join(
              f"Consider {p.splitlines()[0][10:]}." for p in h.parts[:: max(1, len(h.parts) // 40)]))

    fn = h.deep_functions[0] if h.deep_functions else "the first function in the file listed about a fifth of the way in"
    s.ask("t3 re-emit", 400, f"Reproduce the full source of `{fn}` exactly as it appears above, code only.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
