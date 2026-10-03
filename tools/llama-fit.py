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
import os
import sys

sys.dont_write_bytecode = True                    # keep the shared folder free of __pycache__
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from typing import List, Optional, Tuple  # noqa: E402

import carl  # noqa: E402
from carl_core.adapters.gguf_reader import local_meta  # noqa: E402
from carl_core.adapters.system import sysctl_int, vm_network_up  # noqa: E402
from carl_core.domain.errors import ConfigError  # noqa: E402
from carl_core.domain.fit import (DEFAULT_CTX, estimated_limit, max_ctx, need_bytes, plan_slots,  # noqa: E402
                                  prompt_cache_mib, reserve_bytes, window_label)
from carl_core.domain.gguf import GIB, OVERHEAD, ModelShape, kv_bytes_per_token, model_shape  # noqa: E402
from carl_core.domain.types import ModelInfo  # noqa: E402
from carl_core.wiring import GPU  # noqa: E402

TTY = sys.stdout.isatty()
B, DIM, R, GRN, YEL, RED = ("\x1b[1m", "\x1b[2m", "\x1b[0m", "\x1b[32m", "\x1b[33m", "\x1b[31m") if TTY else ("",) * 6


def parse_ctx(v: str) -> int:
    """A window size: tokens or Nk."""
    v = v.lower()
    try:
        return int(v[:-1]) * 1024 if v.endswith("k") else int(v)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a window size: {v!r}") from None


def parse_args(argv: List[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ram", type=float, help="pretend this Mac has N GB of RAM (estimated GPU limit)")
    ap.add_argument("--ctx", type=parse_ctx, help="check this window size (tokens or Nk)")
    ap.add_argument("--slots", type=int, default=1,
                    help="parallel slots (e.g. 2 = main session + a subagent); each gets the full window")
    # launcher-only options
    ap.add_argument("--check", metavar="GGUF", help=argparse.SUPPRESS)      # warn when a start won't fit
    ap.add_argument("--plan", metavar="GGUF", help=argparse.SUPPRESS)       # print "SLOTS CACHE_MIB"
    ap.add_argument("--pick-default", action="store_true", help=argparse.SUPPRESS)  # this Mac's default model
    ap.add_argument("--want-slots", default="auto", help=argparse.SUPPRESS)
    ap.add_argument("--reserve-gb", type=float, default=None, help=argparse.SUPPRESS)
    ap.add_argument("--kv", default="q4_0", help=argparse.SUPPRESS)
    return ap.parse_args(argv)


def gpu_limit(ram_gb: Optional[float]) -> Tuple[int, str]:
    """(limit, how): this Mac's, or the estimate for a Mac with ram_gb of RAM."""
    if ram_gb:
        limit, frac = estimated_limit(ram_gb * GIB)
        return limit, f"estimate for a {ram_gb:g} GB Mac ({frac:.0%} of RAM)"
    return GPU.limit()


def local_shape(path: str) -> Tuple[ModelShape, int]:
    return model_shape(local_meta(path)), os.path.getsize(path)


def cmd_plan(args: argparse.Namespace, limit: int) -> None:
    """Launcher plan: slots (auto = 2 when two full windows fit the GPU limit, else 1) and a
    RAM prompt cache from what is left after a reserve for macOS + apps (+ the VM)."""
    shape, w = local_shape(args.plan)
    ctx = args.ctx or DEFAULT_CTX
    slots = plan_slots(args.want_slots, need_bytes(shape, w, ctx, 2, args.kv) <= limit)
    reserve = reserve_bytes(args.reserve_gb, vm_network_up())
    print(slots, prompt_cache_mib(sysctl_int("hw.memsize"), need_bytes(shape, w, ctx, slots, args.kv), reserve))


def cmd_check(args: argparse.Namespace, limit: int, how: str) -> None:
    """A warning (never an error) when a start would need more than the GPU limit."""
    shape, w = local_shape(args.check)
    ctx, slots = args.ctx or DEFAULT_CTX, max(args.slots, 1)
    need = need_bytes(shape, w, ctx, slots, args.kv)
    if need <= limit:
        return
    mx = max_ctx(shape, w, limit, slots, args.kv)
    per = f" x {slots} slots" if slots > 1 else ""
    print(f"{YEL}warning:{R} this model needs about {need / GIB:.1f} GiB of GPU memory at --ctx {window_label(ctx)}{per} "
          f"({args.kv} KV), but this Mac allows {limit / GIB:.1f} GiB ({how}).")
    print("         " + (f"Largest window that fits: --ctx {window_label(mx)}." if mx else
                         "The weights alone don't fit: use a smaller model (./carl.sh fit).")
          + " It may still load and then fail or swap.")
    print("         Run ./carl.sh tune MODEL to find the best window for this Mac.")


@dataclass(frozen=True)
class Row:
    name: str
    size: int
    shape: Optional[ModelShape]
    status: str                      # "downloaded", "not downloaded", or why the header is unavailable


def model_rows(models: List[ModelInfo], default: str) -> List[Row]:
    app = carl.app()
    rows = []
    for m in models:
        try:
            shape = app.shapes.local(m.get("path", "")) if m.get("status") == "downloaded" else \
                app.shapes.remote(m.get("hf") or {})
        except (OSError, ValueError) as e:
            rows.append(Row(m.get("name", ""), m.get("bytes", 0), None, str(e)[:40]))
            continue
        rows.append(Row(m.get("name", ""), m.get("bytes", 0), shape,
                        "downloaded" if m.get("status") == "downloaded" else "not downloaded"))
    rows.sort(key=lambda r: r.name != default)          # the main default first
    return rows


def cmd_table(args: argparse.Namespace, limit: int, how: str) -> None:
    slots = max(args.slots, 1)
    models = carl.all_models()
    default = carl.load_catalog().get("default", "")
    pick = carl.app().pick_default(models, args.ctx or DEFAULT_CTX, limit)
    print(f"{B}GPU memory limit:{R} {limit / GIB:.1f} GiB  {DIM}({how}){R}"
          + (f"   {B}slots:{R} {slots} (windows are per slot)" if slots > 1 else ""))
    print(f"{B}Default model on this Mac:{R} {pick}"
          + (f" {DIM}(the main default, {default}, doesn't fit){R}" if pick != default else ""))
    print(f"{DIM}need = weights + KV cache + recurrent state + ~{OVERHEAD / GIB:.0f} GiB buffers; "
          f"max window per KV type{R}\n")
    hdr = f"{'model':20} {'weights':>8} {'KV/token q4':>11}  {'max ctx q4':>10} {'max ctx q8':>10}"
    if args.ctx:
        hdr += f"  {'need @' + window_label(args.ctx):>10}"
    print(B + hdr + R)
    for row in model_rows(models, default):
        if not row.shape:
            print(f"{row.name:20} {row.size / GIB:7.1f}G  {RED}(header unavailable: {row.status}){R}")
            continue
        m4, m8 = max_ctx(row.shape, row.size, limit, slots, "q4_0"), max_ctx(row.shape, row.size, limit, slots, "q8_0")
        col = RED if not m4 else YEL if m4 < 65536 else GRN
        line = (f"{row.name:20} {row.size / GIB:7.1f}G {kv_bytes_per_token(row.shape, 'q4_0') / 1024:9.1f}K  "
                f"{col}{window_label(m4):>10}{R} {window_label(m8):>10}")
        if args.ctx:
            nd = need_bytes(row.shape, row.size, args.ctx, slots, "q4_0")
            line += f"  {(GRN if nd <= limit else RED)}{nd / GIB:9.1f}G{R}"
        print(line + f"  {DIM}{row.status}{R}")
    print(f"\n{DIM}Raise the limit (resets at reboot; leave >= 6 GB for macOS):  sudo sysctl iogpu.wired_limit_mb=<MB>{R}")


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    limit, how = gpu_limit(args.ram)
    if args.plan:
        cmd_plan(args, limit)
    elif args.check:
        cmd_check(args, limit, how)
    elif args.pick_default:
        print(carl.app().pick_default(carl.all_models(), args.ctx or DEFAULT_CTX, limit))
    else:
        cmd_table(args, limit, how)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (ConfigError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
