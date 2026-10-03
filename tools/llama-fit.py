#!/usr/bin/env python3
"""What fits on this Mac: for each model (host/catalog.json + the models folder), the GPU
memory it needs and the largest context window that fits, per KV cache type.

  ./carl.sh fit                  # this Mac
  ./carl.sh fit --ram 24         # what a 24 GB Mac would get (estimated GPU limit)
  ./carl.sh fit --ctx 64k        # check one window size for every model

Need = weights + KV cache (window x bytes/token) + recurrent state + ~1 GiB of
compute buffers. The limit is what macOS lets the GPU use (Metal's
recommendedMaxWorkingSetSize, ~2/3 of RAM on 24-32 GB Macs, ~3/4 above), or an
`sudo sysctl iogpu.wired_limit_mb=N` override. Estimates: leave some margin.
Also used by the launcher (--check) to warn before loading a model that won't fit.
"""
import argparse, os, sys
sys.dont_write_bytecode = True                    # keep the shared folder free of __pycache__
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gguf_shape import GIB, OVERHEAD, gpu_limit, kv_bytes_per_token, local_meta, model_shape, remote_meta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_DIR = os.environ.get("MODELS_DIR", os.path.expanduser("~/models/gguf"))
B, DIM, R, GRN, YEL, RED = "\x1b[1m", "\x1b[2m", "\x1b[0m", "\x1b[32m", "\x1b[33m", "\x1b[31m"
if not sys.stdout.isatty():
    B = DIM = R = GRN = YEL = RED = ""

def parse_ctx(v):
    v = v.lower()
    return int(v[:-1]) * 1024 if v.endswith("k") else int(v)

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--ram", type=float, help="pretend this Mac has N GB of RAM (estimated GPU limit)")
ap.add_argument("--ctx", type=parse_ctx, help="check this window size (tokens or Nk)")
ap.add_argument("--slots", type=int, default=1, help="parallel slots (e.g. 2 = main session + a subagent); each gets the full window")
ap.add_argument("--check", metavar="GGUF", help=argparse.SUPPRESS)
ap.add_argument("--plan", metavar="GGUF", help=argparse.SUPPRESS)
ap.add_argument("--pick-default", action="store_true", help=argparse.SUPPRESS)   # launcher: print the default model name for this Mac       # launcher: print "SLOTS CACHE_MIB"
ap.add_argument("--want-slots", default="auto", help=argparse.SUPPRESS)
ap.add_argument("--reserve-gb", type=float, default=None, help=argparse.SUPPRESS)   # launcher: one model, one line
ap.add_argument("--kv", default="q4_0", help=argparse.SUPPRESS)
args = ap.parse_args()

if args.ram:
    mem = args.ram * GIB
    frac = 2 / 3 if mem <= 32 * GIB else 3 / 4
    limit, how = int(mem * frac), f"estimate for a {args.ram:g} GB Mac ({frac:.0%} of RAM)"
else:
    limit, how = gpu_limit()

SL = max(args.slots, 1)

def need(shape, weights, ctx, kv):
    """ctx is per slot; the unified KV pool holds SL x ctx, each slot has its own recurrent state."""
    return weights + kv_bytes_per_token(shape, kv) * ctx * SL + shape["rs_bytes"] * SL + OVERHEAD

def max_ctx(shape, weights, kv):
    """Largest window per slot."""
    room = limit - weights - shape["rs_bytes"] * SL - OVERHEAD
    if room <= 0:
        return 0
    n = int(room // (kv_bytes_per_token(shape, kv) * SL))
    return min(n // 4096 * 4096, shape["ctx_train"] or 262144)

def k(n):
    return f"{n // 1024}K" if n else "–"

if args.plan:
    # Launcher plan: how many slots (auto = 2 when two full windows fit the GPU
    # limit, else 1) and how big a RAM prompt cache the rest of memory allows
    # after a reserve for macOS + apps (+ the VM when VMware's network is up).
    shape = model_shape(local_meta(args.plan))
    w = os.path.getsize(args.plan)
    ctx = args.ctx or 98304
    def need_n(n):
        return w + kv_bytes_per_token(shape, args.kv) * ctx * n + shape["rs_bytes"] * n + OVERHEAD
    slots = int(args.want_slots) if args.want_slots != "auto" else (2 if need_n(2) <= limit else 1)
    import subprocess
    mem = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout or 0)
    vm = subprocess.run(["sh", "-c", "ifconfig | grep -q 'inet 192.168.42.1 '"]).returncode == 0
    reserve = (args.reserve_gb if args.reserve_gb is not None else (10 if vm else 6)) * GIB
    cache = (mem - need_n(slots) - reserve) / 2**20
    cache = int(max(1024, min(8192, cache)) // 256 * 256)
    print(slots, cache)
    sys.exit(0)

if args.check:
    shape = model_shape(local_meta(args.check))
    w = os.path.getsize(args.check)
    ctx = args.ctx or 98304
    nd = need(shape, w, ctx, args.kv)
    if nd > limit:
        mx = max_ctx(shape, w, args.kv)
        slots = f" x {SL} slots" if SL > 1 else ""
        print(f"{YEL}warning:{R} this model needs about {nd / GIB:.1f} GiB of GPU memory at --ctx {k(ctx)}{slots} "
              f"({args.kv} KV), but this Mac allows {limit / GIB:.1f} GiB ({how}).")
        print("         " + (f"Largest window that fits: --ctx {k(mx)}." if mx else
                             "The weights alone don't fit: use a smaller model (./carl.sh fit).")
              + " It may still load and then fail or swap.")
    sys.exit(0)

import carl
cat = carl.load_catalog()
small_default = cat.get("default_small")
rows = []
for m in carl.all_models():
    if args.pick_default and m["name"] not in (cat["default"], small_default):
        continue                               # only the default and the small default matter
    try:
        meta = local_meta(m["path"]) if m["status"] == "downloaded" else remote_meta(m["hf"]["repo"], m["hf"]["revision"], m["hf"]["file"])
    except Exception as e:
        rows.append((m["name"], int(m["bytes"]), None, str(e)[:40])); continue
    rows.append((m["name"], int(m["bytes"]), model_shape(meta), "downloaded" if m["status"] == "downloaded" else "not downloaded"))
rows.sort(key=lambda r: r[0] != cat["default"])   # the main default first

# The default model for this Mac: the catalogue default, or the
# "default-small" entry when the first one can't hold one window of --ctx.
ctx_d = args.ctx or 98304
first = rows[0] if rows else None
pick = first[0] if first else None
def need_one(shape, weights, ctx):
    return weights + kv_bytes_per_token(shape, "q4_0") * ctx + shape["rs_bytes"] + OVERHEAD
if first and first[2] and small_default and need_one(first[2], first[1], ctx_d) > limit:
    pick = small_default
if args.pick_default:
    print(pick or "")
    sys.exit(0)

print(f"{B}GPU memory limit:{R} {limit / GIB:.1f} GiB  {DIM}({how}){R}" + (f"   {B}slots:{R} {SL} (windows are per slot)" if SL > 1 else ""))
print(f"{B}Default model on this Mac:{R} {pick}" + (f" {DIM}(the main default, {first[0]}, doesn't fit){R}" if pick != (first[0] if first else None) else ""))
print(f"{DIM}need = weights + KV cache + recurrent state + ~{OVERHEAD / GIB:.0f} GiB buffers; max window per KV type{R}\n")
hdr = f"{'model':20} {'weights':>8} {'KV/token q4':>11}  {'max ctx q4':>10} {'max ctx q8':>10}"
if args.ctx:
    hdr += f"  {'need @' + k(args.ctx):>10}"
print(B + hdr + R)
for name, size, shape, status in rows:
    if not shape:
        print(f"{name:20} {size / GIB:7.1f}G  {RED}(header unavailable: {status}){R}"); continue
    m4, m8 = max_ctx(shape, size, "q4_0"), max_ctx(shape, size, "q8_0")
    col = RED if not m4 else YEL if m4 < 65536 else GRN
    line = (f"{name:20} {size / GIB:7.1f}G {kv_bytes_per_token(shape, 'q4_0') / 1024:9.1f}K  "
            f"{col}{k(m4):>10}{R} {k(m8):>10}")
    if args.ctx:
        nd = need(shape, size, args.ctx, "q4_0")
        line += f"  {(GRN if nd <= limit else RED)}{nd / GIB:9.1f}G{R}"
    print(line + f"  {DIM}{status}{R}")
print(f"\n{DIM}Raise the limit (resets at reboot; leave >= 6 GB for macOS):  sudo sysctl iogpu.wired_limit_mb=<MB>{R}")
