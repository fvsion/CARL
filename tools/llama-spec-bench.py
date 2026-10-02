#!/usr/bin/env python3
"""Decode-speed benchmark for llama-server speculative-decoding modes.

Usage: python3 tools/llama-spec-bench.py LABEL [BASE_URL]
Runs three workloads (prose, fresh code, code re-emission/edit) with thinking
off and prints llama-server's own timings (prompt/predicted tok/s, draft
acceptance) per workload. BASE_URL defaults to http://192.168.42.1:8080.
"""
import json, os, sys, urllib.request

label = sys.argv[1]
base = sys.argv[2] if len(sys.argv) > 2 else "http://192.168.42.1:8080"
key = open(os.path.expanduser("~/.mtplx/api-key")).read().strip()

src = open(__file__).read()
edit_src = "\n".join(src.splitlines() * 3)  # ~200 lines of code to re-emit
workloads = {
    "prose": "Explain how a B-tree insertion works, in detail.",
    "code": "Write a complete Python module implementing an LRU cache with TTL expiry, "
            "thread safety, type hints and docstrings. Code only.",
    "edit": "Return the following file in full, unchanged except rename the variable "
            "`workloads` to `tasks` everywhere. Output only the code.\n\n" + edit_src,
}

for name, prompt in workloads.items():
    body = {"model": "x", "max_tokens": 400, "temperature": 1.0, "top_p": 0.95, "top_k": 20,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + key,
                                          "content-type": "application/json"})
    d = json.load(urllib.request.urlopen(req, timeout=1800))
    t = d.get("timings", {})
    acc = ""
    if t.get("draft_n"):
        acc = f" draft {t.get('draft_n_accepted')}/{t.get('draft_n')} accepted"
    print(f"{label:24} {name:5} prompt {t.get('prompt_n'):>5} @ {t.get('prompt_per_second', 0):6.1f} tok/s | "
          f"gen {t.get('predicted_n'):>4} @ {t.get('predicted_per_second', 0):5.1f} tok/s{acc}", flush=True)
