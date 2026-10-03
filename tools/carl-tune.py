#!/usr/bin/env python3
"""Auto-tune one model for this Mac: speculative decoding and context window.

  ./carl.sh tune NAME [--quick] [--port 8093]

Steps (the model loads once per speculation mode, ~5-10 min in all):
  1. memory: the largest window that fits with 1 and 2 slots (tools/llama-fit.py's model)
  2. speculation: none, n-gram, and (when the file has an MTP head) MTP and MTP + n-gram
     at n = 1 and 2. Each runs prose, fresh code and a code re-emit, twice. Score =
     weighted geometric mean (prose 0.4, code 0.4, re-emit 0.2); a mode with drafting
     must beat a simpler one by 3% to win.
  3. prompt reading: a cold read at 8K and 32K tokens (64K too without --quick). The
     read time for a full window gives this Mac's context zones: a cold re-read of the
     whole window within 3 min = fast, 10 min = slow, beyond = very slow.
  4. result: kv q4_0, the best speculation, the context (the catalogue's window while
     a cold read of it is no worse than "slow" here and it fits, else the largest
     standard window in the fast zone), 2 slots when two windows fit. Saved to
     ~/.config/llm-deploy/models.json (models.NAME.tune); every start of NAME uses it
     unless config.json sets a value (the monitor's Settings tab).

Needs the GPU to itself: refuses to run while another model is loaded
(stop the server first; the monitor's Auto-tune panel does that for you).
Progress lines start with "STEP i/N" (the monitor shows them).
"""
import argparse, json, math, os, random, re, signal, subprocess, sys, time, urllib.request
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import carl
from gguf_shape import OVERHEAD, gpu_limit, kv_bytes_per_token, local_meta, model_shape

REPO = carl.REPO
ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("model")
ap.add_argument("--port", type=int, default=8093)
ap.add_argument("--quick", action="store_true", help="fewer modes (n=1 only) and no 64K read")
ap.add_argument("--dry-run", action="store_true", help="print the plan, don't save")
args = ap.parse_args()

m = carl.find(args.model)
if not m:
    sys.exit(f"error: unknown model '{args.model}' (./carl.sh models)")
if m["status"] != "downloaded":
    sys.exit(f"error: {m['name']} is not downloaded (./carl.sh download {m['name']})")

KEY = open(os.path.expanduser("~/.mtplx/api-key")).read().strip() if os.path.exists(os.path.expanduser("~/.mtplx/api-key")) else ""
BASE = f"http://127.0.0.1:{args.port}"
shape = model_shape(local_meta(m["path"]))
weights = os.path.getsize(m["path"])
limit = gpu_limit()[0]
iq = str(shape.get("ftype", "")).upper().startswith("IQ")
has_mtp = bool(shape.get("nextn"))

modes = [("none", 1), ("ngram-mod", 2)]
if has_mtp:
    modes += [("draft-mtp", 1), ("draft-mtp,ngram-mod", 1)]
    if not args.quick:
        modes += [("draft-mtp", 2), ("draft-mtp,ngram-mod", 2)]
depths = [8192, 32768] + ([] if args.quick else [65536])
N = 2 + len(modes) + 1
step_no = [0]


def step(msg):
    step_no[0] += 1
    print(f"STEP {step_no[0]}/{N} {msg}", flush=True)


def note(msg):
    print(f"  {msg}", flush=True)


def need(ctx, slots, kv="q4_0"):
    return weights + kv_bytes_per_token(shape, kv) * ctx * slots + shape["rs_bytes"] * slots + OVERHEAD


def max_ctx(slots):
    room = limit - weights - shape["rs_bytes"] * slots - OVERHEAD
    return 0 if room <= 0 else min(int(room // (kv_bytes_per_token(shape, "q4_0") * slots)) // 4096 * 4096,
                                   shape.get("ctx_train") or 262144)


def call(path, body=None, timeout=1800):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {KEY}", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def port_busy(port):
    out = subprocess.run(["/usr/sbin/netstat", "-anv", "-p", "tcp"], capture_output=True, text=True, timeout=10).stdout
    return any(len(f) > 5 and f[5] == "LISTEN" and f[3].endswith(f".{port}") for f in (l.split() for l in out.splitlines()))


def other_big_processes():
    """Processes over BIG_GB (default 8) GB resident, or known model servers: like host/common.sh guard_other_models."""
    if os.environ.get("ALLOW_SECOND_MODEL") == "1":
        return []
    big = int(os.environ.get("BIG_GB", 8)) * 2**20
    out = []
    for line in subprocess.run(["ps", "-axo", "pid=,rss=,comm="], capture_output=True, text=True, timeout=10).stdout.splitlines():
        f = line.split(None, 2)
        if len(f) == 3 and int(f[0]) != os.getpid() and (int(f[1]) > big or re.search(r"(^|/)(llama-server|mtplx|ollama|LM Studio)", f[2])):
            out.append(f"pid {f[0]} ({os.path.basename(f[2])}, {int(f[1]) / 2**20:.1f} GB)")
    return out


SERVER = [None]


def start(spec, n, ctx):
    stop()
    env = dict(os.environ, SETTINGS_FILE="none", MONITOR="0", KEEP_AWAKE="0", LOG_FILE="none", PORT=str(args.port),
               SLOTS="1", SPEC=spec, SPEC_N=str(n), FIT_CHECK="0")
    for k in ("CTX", "KV", "KV_K", "KV_V", "MODEL", "ALIAS", "CACHE_RAM"):
        env.pop(k, None)
    log = open(os.path.expanduser(f"~/models/logs/.tune-{args.port}.out"), "w")
    SERVER[0] = subprocess.Popen([os.path.join(REPO, "host", "serve-llama.sh"), "--model", m["path"], "--local",
                                  "--ctx", str(ctx), "--kv", "q4"], env=env, stdout=log, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, start_new_session=True)
    t0 = time.time()
    while time.time() - t0 < 600:
        if SERVER[0].poll() is not None:
            raise RuntimeError("the server exited: " + open(log.name).read().strip().splitlines()[-1][:200])
        try:
            if call("/health", timeout=2).get("status") == "ok":
                return time.time() - t0
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("the server did not start in 10 min")


def stop():
    p = SERVER[0]
    if p and p.poll() is None:
        os.killpg(p.pid, signal.SIGTERM)
        try:
            p.wait(60)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL); p.wait()
    SERVER[0] = None
    for _ in range(60):
        if not port_busy(args.port):
            return
        time.sleep(1)


SRC = open(__file__).read()
WORK = {
    "prose": "Explain how a B-tree insertion works, in detail, with an example.",
    "code": "Write a complete Python module implementing an LRU cache with TTL expiry, thread safety, "
            "type hints and docstrings. Code only.",
    "edit": "Return the following file in full, unchanged except rename the function `call` to `request` "
            "everywhere. Output only the code.\n\n" + SRC[:9000],
}
WEIGHT = {"prose": 0.4, "code": 0.4, "edit": 0.2}


def gen(prompt, max_tokens=256):
    d = call("/v1/chat/completions", {"model": "x", "max_tokens": max_tokens, "temperature": 1.0, "top_p": 0.95, "top_k": 20,
                                       "chat_template_kwargs": {"enable_thinking": False}, "cache_prompt": False,
                                       "messages": [{"role": "user", "content": prompt}]})
    return d.get("timings", {})


def bench_mode():
    gen("Say hello.", 16)                                   # warm-up
    res = {}
    for name, prompt in WORK.items():
        vals = [gen(prompt).get("predicted_per_second", 0) for _ in range(2)]
        res[name] = round(sum(vals) / len(vals), 1)
    res["score"] = round(math.exp(sum(WEIGHT[k] * math.log(max(res[k], 0.1)) for k in WEIGHT)), 2)
    return res


WORDS = ("system memory cache token window model server request decode prompt layer tensor kernel metal quant "
         "expert router batch slot context speculative draft accept reject verify stream session agent tool").split()


def prefill_rate(tokens):
    """Cold prompt read speed (tok/s) for a prompt of about `tokens` tokens."""
    rnd = random.Random(tokens)
    lines = [f"Record {i}: " + " ".join(rnd.choice(WORDS) for _ in range(12)) + f" value={rnd.randint(0, 10**6)}."
             for i in range(tokens // 18)]
    text = "\n".join(lines)
    try:
        n = len(call("/tokenize", {"content": text}).get("tokens", []))
        if n > tokens:
            text = text[: int(len(text) * tokens / n)]
    except Exception:
        pass
    t = call("/v1/chat/completions", {"model": "x", "max_tokens": 1, "cache_prompt": False,
                                       "chat_template_kwargs": {"enable_thinking": False},
                                       "messages": [{"role": "user", "content": text + "\n\nReply with OK."}]}, timeout=3600)["timings"]
    return t["prompt_n"], t["prompt_per_second"]


def main():
    machine = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
    ram = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout or 0) // 2**30
    vout = subprocess.run(["llama-server", "--version"], capture_output=True, text=True)
    ver = [x.strip() for x in (vout.stdout + vout.stderr).splitlines() if x.startswith("version:")]
    print(f"Auto-tune {m['name']} on {machine} {ram} GB ({os.path.basename(m['path'])}, {shape.get('ftype')}, "
          f"{'MoE' if shape.get('experts') else 'dense'}, MTP head: {'yes' if has_mtp else 'no'})", flush=True)
    for p in (8080, 8000, args.port):
        if port_busy(p):
            sys.exit(f"error: a server is running on port {p}: stop it first (two models don't fit in memory)")
    big = other_big_processes()
    if big:
        sys.exit("error: another large process (probably a model) is in memory: " + "; ".join(big)
                 + ". Stop it first, or set ALLOW_SECOND_MODEL=1 if it is not a model.")

    step("memory")
    m1, m2 = max_ctx(1), max_ctx(2)
    note(f"GPU limit {limit / 2**30:.1f} GiB; largest window per slot: 1 slot {m1 // 1024}K, 2 slots {m2 // 1024}K")
    if m1 < 16384:
        sys.exit("error: the model doesn't fit this Mac's GPU memory with a 16K window")

    results, test_ctx = {}, min(36864 if args.quick else 69632, m1)
    for spec, n in modes:
        step(f"speculation {spec} n={n}")
        load = start(spec, n, test_ctx)
        r = bench_mode()
        results[f"{spec}:{n}"] = r
        note(f"loaded in {load:.0f}s · prose {r['prose']} · code {r['code']} · re-emit {r['edit']} tok/s · score {r['score']}")

    # best mode: simpler modes win unless a drafting mode is 3% better
    order = list(results)
    best = order[0]
    for key in order[1:]:
        if results[key]["score"] > results[best]["score"] * 1.03:
            best = key
    spec, n = best.split(":")
    note(f"best: {spec} n={n} (score {results[best]['score']} vs none {results['none:1']['score']})")

    step("prompt reading")
    load = start(spec, int(n), test_ctx)
    rates = []
    for d in depths:
        if d + 2048 > test_ctx:
            continue
        got, tps = prefill_rate(d)
        rates.append((got, tps))
        note(f"{got} tokens read cold at {tps:.0f} tok/s ({got / tps:.0f} s)")
    stop()
    # seconds per token grows ~linearly with depth: t(n) = a + b*n; read time of a full
    # window W = a*W + b*W^2/2.
    (n1, r1), (n2, r2) = rates[0], rates[-1]
    b = max((1 / r2 - 1 / r1) / max(n2 - n1, 1), 0)
    a = max(1 / r1 - b * n1, 1e-6)
    read = lambda w: a * w + b * w * w / 2
    def largest(secs):
        w = 4096
        while w < 262144 and read(w + 4096) <= secs:
            w += 4096
        return w
    zones = {"good": largest(180), "slow": largest(600), "very_slow": largest(1200)}
    note("context zones (cold read of a full window): fast to " + f"{zones['good'] // 1024}K (3 min), slow to "
         f"{zones['slow'] // 1024}K (10 min), very slow to {zones['very_slow'] // 1024}K (20 min)")

    # context: keep the catalogue's window while it is no worse than "slow" here and fits;
    # else the largest standard window in the fast zone
    std = [32768, 49152, 65536, 98304, 131072, 163840]
    base = carl.effective_tune(m, {"schema": carl.SCHEMA})[0]["ctx"]
    if base <= zones["slow"] and base <= m1:
        ctx = base
    else:
        ctx = max([w for w in std if w <= max(zones["good"], 32768) and w <= m1] or [min(32768, m1)])
    slots = "2" if need(ctx, 2) <= limit else "1"
    tune = {"date": time.strftime("%Y-%m-%d"), "machine": f"{machine} {ram} GB", "llama_cpp": ver[0] if ver else "?",
            "settings": {"kv": "q4_0", "spec": spec, "spec_n": int(n), "ctx": ctx, "slots": slots},
            "ctx_zones": zones, "results": {"speculation": results, "prompt_read": [list(r) for r in rates]},
            "max_ctx": {"1": m1, "2": m2}}
    step("result")
    note(f"kv q4_0 · speculation {spec} n={n} · context {ctx // 1024}K per slot · {slots} slot(s)")
    if not args.dry_run:
        db = carl.load_local()
        e = db["models"].setdefault(m["name"], {})
        if m.get("custom"):
            e.setdefault("path", m["path"]); e.setdefault("source", m["source"])
        e["tune"] = tune
        carl.save_local(db)
        note(f"saved to {carl.LOCAL_FILE.replace(os.path.expanduser('~'), '~')} (used by the next start of {m['name']})")
    print("DONE " + json.dumps(tune["settings"]), flush=True)


try:
    main()
except KeyboardInterrupt:
    print("cancelled", flush=True); sys.exit(130)
except Exception as e:
    print(f"error: {e}", flush=True); sys.exit(1)
finally:
    stop()
