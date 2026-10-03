#!/usr/bin/env python3
"""Multi-turn same-session long-context test against llama-server.

Usage: python3 tools/llama-sesstest.py MSGS_DIR [BASE_URL]
(MSGS_DIR/memtest-msgs.json from tools/make-memtest-prompt.py). Turns: 56K
prompt, short follow-up, then ~7-8K-token appends, printing llama-server
timings (cached/processed prompt tokens, prompt & gen tok/s) and the
server's RSS + system wired memory after each turn.
"""
import glob, json, os, subprocess, sys, time, urllib.request

def listen_pid(port):  # netstat-based: lsof hangs on a stale network share
    import re as _re
    out = subprocess.check_output(["/usr/sbin/netstat", "-anv", "-p", "tcp"], text=True)
    for line in out.splitlines():
        f = line.split()
        if len(f) > 5 and f[5] == "LISTEN" and f[3].endswith("." + str(port)):
            return int(_re.search(r":(\d+)\s+\d{5}\s", line).group(1))
    raise SystemExit(f"no server listens on port {port}")

d, base = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "http://192.168.42.1:8080")
key = open(os.path.expanduser("~/.mtplx/api-key")).read().strip()
site = subprocess.check_output(["uv", "tool", "dir"], text=True).strip() + "/mtplx/lib/python3.12/site-packages/mtplx"
msgs = json.load(open(os.path.join(d, "memtest-msgs.json")))
big = sorted(glob.glob(site + "/server/*.py"), key=os.path.getsize)

def mem():
    pid = listen_pid(base.rsplit(":", 1)[1])
    rss = int(subprocess.check_output(["ps", "-o", "rss=", "-p", pid], text=True)) / 1048576
    wired = [l for l in subprocess.check_output(["vm_stat"], text=True).splitlines() if "wired" in l][0]
    swap = subprocess.check_output(["sysctl", "-n", "vm.swapusage"], text=True).split("used = ")[1].split()[0]
    return f"rss={rss:.1f}G wired={int(wired.split()[-1].rstrip('.'))*16384/2**30:.1f}G swap_used={swap}"

def call(tag):
    body = {"model": "x", "max_tokens": 60, "chat_template_kwargs": {"enable_thinking": False}, "messages": msgs}
    req = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + key, "content-type": "application/json"})
    t0 = time.time()
    try:
        r = json.load(urllib.request.urlopen(req, timeout=3600))
    except urllib.error.HTTPError as e:
        print(tag, "HTTP", e.code, e.read()[:300], mem(), flush=True); return None
    t = r.get("timings", {})
    u = r.get("usage", {})
    print(f"{tag} OK {time.time()-t0:6.1f}s prompt_total={u.get('prompt_tokens')} cached={t.get('cache_n')} "
          f"processed={t.get('prompt_n')} @ {t.get('prompt_per_second',0):.0f} tok/s | gen {t.get('predicted_n')} "
          f"@ {t.get('predicted_per_second',0):.1f} tok/s | {mem()}", flush=True)
    return r["choices"][0]["message"]["content"]

turns = [("t1", None), ("t2", "Now name one other file above that deals with sessions, in one sentence.")]
for i, f in enumerate(big[-3:-7:-1], start=3):
    turns.append((f"t{i}", "Here is one more file:\n" + open(f, errors="ignore").read()[:30000] + "\n\nOne sentence: what does it do?"))
for tag, q in turns:
    if q: msgs.append({"role": "user", "content": q})
    a = call(tag)
    if a is None: break
    msgs.append({"role": "assistant", "content": a})
