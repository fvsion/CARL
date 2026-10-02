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
import glob, json, os, random, re, subprocess, sys, time, urllib.request

label = sys.argv[1]
target = int(sys.argv[2]) if len(sys.argv) > 2 else 65536
base = sys.argv[3] if len(sys.argv) > 3 else "http://192.168.42.1:8080"
key = open(os.path.expanduser("~/.mtplx/api-key")).read().strip()
site = subprocess.check_output(["uv", "tool", "dir"], text=True).strip() + "/mtplx/lib/python3.12/site-packages/mtplx"

# ---- deterministic haystack -------------------------------------------------
chars_per_token = 3.7          # measured on this corpus (207K chars ~ 56K tokens)
budget = int(target * chars_per_token)
files = sorted(glob.glob(site + "/**/*.py", recursive=True), key=lambda f: (os.path.getsize(f), f))
parts, n = [], 0
for f in files:                                # many small files, smallest first
    t = open(f, errors="ignore").read()
    if n + len(t) > budget:
        break
    parts.append(f"### FILE: {os.path.relpath(f, site)}\n{t}")
    n += len(t)
haystack = "\n\n".join(parts)
# Needles at exact character depths (snapped to the next line break).
rng = random.Random(42)
needles, inserts = [], []
for i, depth in enumerate([0.02, 0.10, 0.25, 0.40, 0.55, 0.70, 0.85, 0.98]):
    code = f"{rng.randint(1000, 9999)}-{rng.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}{rng.randint(10, 99)}"
    needles.append((f"vault-{i+1}", code))
    pos = haystack.find("\n", int(depth * len(haystack))) + 1
    inserts.append((pos, f"# NOTE: the access code for vault-{i+1} is {code}.\n"))
for pos, text in sorted(inserts, reverse=True):
    haystack = haystack[:pos] + text + haystack[pos:]
fifth = haystack[int(0.2 * len(haystack)):int(0.3 * len(haystack))]
deep_fn = re.findall(r"\ndef (\w{6,})\(", fifth)           # a function ~20-30% deep

def mem():
    pid = subprocess.check_output(["lsof", "-tiTCP:" + base.rsplit(":", 1)[1], "-sTCP:LISTEN"], text=True).split()[0]
    rss = int(subprocess.check_output(["ps", "-o", "rss=", "-p", pid], text=True)) / 1048576
    wired = [l for l in subprocess.check_output(["vm_stat"], text=True).splitlines() if "wired" in l][0]
    swap = subprocess.check_output(["sysctl", "-n", "vm.swapusage"], text=True).split("used = ")[1].split()[0]
    return f"rss={rss:.1f}G wired={int(wired.split()[-1].rstrip('.'))*16384/2**30:.1f}G swap={swap}"

msgs = [{"role": "user", "content": haystack + "\n\nThe text above contains access codes for vault-1 "
         "through vault-8 in comments. Reply with ONLY a JSON object mapping each vault name to its code."}]

def call(tag, max_tokens):
    body = {"model": "x", "max_tokens": max_tokens, "chat_template_kwargs": {"enable_thinking": False}, "messages": msgs}
    req = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + key, "content-type": "application/json"})
    t0 = time.time()
    r = json.load(urllib.request.urlopen(req, timeout=7200))
    t = r.get("timings", {})
    acc = f" spec {t.get('draft_n_accepted')}/{t.get('draft_n')}" if t.get("draft_n") else ""
    print(f"{label:18} {tag}: ctx={r['usage']['prompt_tokens']} cached={t.get('cache_n')} "
          f"read {t.get('prompt_n')} @ {t.get('prompt_per_second',0):.1f} tok/s | "
          f"gen {t.get('predicted_n')} @ {t.get('predicted_per_second',0):.2f} tok/s{acc} | "
          f"{time.time()-t0:.0f}s | {mem()}", flush=True)
    out = r["choices"][0]["message"]["content"]
    msgs.append({"role": "assistant", "content": out})
    return out

a1 = call("t1 recall ", 200)
got = sum(1 for name, code in needles if code in a1)
print(f"{label:18} recall: {got}/{len(needles)} needles correct", flush=True)

msgs.append({"role": "user", "content": "Thanks. Now, drawing on the code above, write a ~400-word explanation of how "
             "these modules fit together, as plain prose. " + " ".join(
                 f"Consider {p.splitlines()[0][10:]}." for p in parts[:: max(1, len(parts) // 40)])})
call("t2 decode ", 400)

fn = deep_fn[0] if deep_fn else "the first function in the file listed about a fifth of the way in"
msgs.append({"role": "user", "content": f"Reproduce the full source of `{fn}` exactly as it appears above, code only."})
call("t3 re-emit", 400)
