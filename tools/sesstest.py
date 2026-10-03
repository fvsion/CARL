#!/usr/bin/env python3
"""Multi-turn same-session long-context test against the MTPLX server.

Usage: python3 tools/sesstest.py OUT_DIR KEY SITE SESSION_ID
  OUT_DIR     holds memtest-msgs.json (run tools/make-memtest-prompt.py OUT_DIR first)
  KEY         "-" reads the key from $API_KEY_FILE or ~/.mtplx/api-key (preferred: a key
              given here is visible in the process list); the key itself still works
  SITE        the MTPLX package source, e.g. "$(uv tool dir)/mtplx/lib/python3.12/site-packages/mtplx"
  SESSION_ID  sent as x-mtplx-session-id
Prints per-turn memory/cache stats from MTPLX's request log.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
import urllib.error

from carl_bench import JsonObj, Message, chat_body, post_chat, read_api_key, read_source

BASE = "http://192.168.42.1:8000"
MODEL = "qwen3.8-27b-abliterated-grant"
LOG = os.path.expanduser("~/.mtplx/logs/request-log-8000.jsonl")
APPEND_CHARS = 30000


def stats_line(r: JsonObj) -> str:
    """One request-log record as the short summary this test prints."""
    def gib(k: str) -> float:
        return float(r.get(k) or 0) / 2**30
    return (f"cached={r.get('cached_tokens')} src={r.get('cache_source')} miss={r.get('cache_miss_reason')} "
            f"ttft={round(r.get('ttft_s') or 0, 1)} dec={round(r.get('decode_tok_s') or 0, 1)} "
            f"active={gib('active_memory_bytes'):.1f} peak={gib('peak_memory_bytes'):.1f} err={r.get('error_kind')} "
            f"memo_rebuilds={r.get('paged_kv_quant_dequant_memo_rebuilds')} kvq={r.get('paged_kv_quant_mode')} "
            f"restore={r.get('session_restore_mode')} liveref={r.get('request_session_keep_live_ref')} "
            f"route={r.get('prefill_route')}")


def stats() -> str:
    with open(LOG, encoding="utf-8") as f:
        last: JsonObj = json.loads(f.read().strip().splitlines()[-1])
    return stats_line(last)


def call(key: str, sid: str, msgs: list[Message], tag: str) -> str | None:
    headers = {"x-mtplx-client": "opencode", "x-mtplx-session-id": sid}
    t = time.time()
    try:
        d = post_chat(BASE, key, chat_body(msgs, 60, model=MODEL), timeout=2400, headers=headers)
    except urllib.error.HTTPError as e:
        time.sleep(1)
        print(tag, "HTTP", e.code, "|", stats(), flush=True)
        return None
    print(tag, "OK", round(time.time() - t, 1), "s prompt", d["usage"]["prompt_tokens"], "|", stats(), flush=True)
    content: str = d["choices"][0]["message"]["content"]
    return content


def nth_largest_server_file(site: str, n: int) -> str:
    return read_source(sorted(glob.glob(site + "/server/*.py"), key=os.path.getsize)[-n])[:APPEND_CHARS]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Multi-turn same-session long-context test against MTPLX.",
                                 epilog='KEY "-" reads the key file (keeps it out of the process list).')
    ap.add_argument("out_dir")
    ap.add_argument("key")
    ap.add_argument("site")
    ap.add_argument("session_id")
    a = ap.parse_args(argv)
    key = read_api_key() if a.key in ("-", "") else a.key
    with open(os.path.join(a.out_dir, "memtest-msgs.json"), encoding="utf-8") as f:
        msgs: list[Message] = json.load(f)
    turns: list[tuple[str, str | None]] = [
        ("t1", None), ("t2", "Now name one other file above that deals with sessions, in one sentence."),
        ("t3", "Here is one more file:\n" + nth_largest_server_file(a.site, 3) + "\n\nOne sentence: what does it do?"),
        ("t4", "Thanks. One word: yes or no, is it Python?"),
        ("t5", "And one more file:\n" + nth_largest_server_file(a.site, 4) + "\n\nOne sentence: what does it do?")]
    for tag, q in turns:
        if q:
            msgs.append({"role": "user", "content": q})
        answer = call(key, a.session_id, msgs, tag)
        if answer is None:
            break
        msgs.append({"role": "assistant", "content": answer})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
