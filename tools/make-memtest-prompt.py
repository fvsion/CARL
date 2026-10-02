#!/usr/bin/env python3
"""Build the ~56K-token long-context test prompt used by sesstest.py.

Usage: python3 tools/make-memtest-prompt.py OUT_DIR
Concatenates the smallest MTPLX source files (~207K chars) into
OUT_DIR/memtest-msgs.json.
"""
import glob, json, os, subprocess, sys

out = sys.argv[1]
site = subprocess.check_output(["uv", "tool", "dir"], text=True).strip() + "/mtplx/lib/python3.12/site-packages/mtplx"
files = sorted(glob.glob(site + "/*.py"), key=os.path.getsize)
buf, n = [], 0
for f in files:
    t = open(f, errors="ignore").read()
    if n + len(t) > 215000:
        continue
    buf.append(f"### FILE: {os.path.basename(f)}\n{t}")
    n += len(t)
msgs = [{"role": "system", "content": "You are a code reviewer."},
        {"role": "user", "content": "\n\n".join(buf) + "\n\nIn one sentence: which file above looks most related to memory planning?"}]
json.dump(msgs, open(os.path.join(out, "memtest-msgs.json"), "w"))
print(n, "chars,", len(buf), "files")
