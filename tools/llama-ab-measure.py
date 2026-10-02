#!/usr/bin/env python3
"""One A/B measurement against a freshly started llama-server (empty cache).
Prefill ~12K tokens cold, then generate 200 tokens at that context.
Usage: python3 tools/llama-ab-measure.py LABEL [BASE_URL]"""
import glob, json, os, subprocess, sys, urllib.request
label = sys.argv[1]; base = sys.argv[2] if len(sys.argv) > 2 else "http://192.168.42.1:8080"
key = open(os.path.expanduser("~/.mtplx/api-key")).read().strip()
site = subprocess.check_output(["uv", "tool", "dir"], text=True).strip() + "/mtplx/lib/python3.12/site-packages/mtplx"
buf, n = [], 0
for f in sorted(glob.glob(site + "/*.py"), key=os.path.getsize):
    t = open(f, errors="ignore").read()
    if n + len(t) > 46000: continue
    buf.append(f"### {os.path.basename(f)}\n{t}"); n += len(t)
msgs = [{"role": "user", "content": "\n\n".join(buf) + "\n\nWrite a detailed summary of what these files do."}]
body = {"model": "x", "max_tokens": 200, "chat_template_kwargs": {"enable_thinking": False}, "messages": msgs}
req = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps(body).encode(),
                             headers={"Authorization": "Bearer " + key, "content-type": "application/json"})
t = json.load(urllib.request.urlopen(req, timeout=3600)).get("timings", {})
print(f"{label:22} prefill {t.get('prompt_n')} tok @ {t.get('prompt_per_second',0):6.1f} tok/s | "
      f"gen@ctx {t.get('predicted_n')} @ {t.get('predicted_per_second',0):5.2f} tok/s", flush=True)
