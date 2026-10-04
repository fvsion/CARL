#!/usr/bin/env python3
"""What fits on this Mac: auto fit's pick for each goal (and why every better model was
passed over), then for each model (host/catalog.json + the models folder) the GPU memory
it needs and the largest context window that fits, per KV cache type.

  ./carl.sh fit                  # this Mac
  ./carl.sh fit --ram 24         # what a 24 GB Mac would get (estimated GPU limit)
  ./carl.sh fit --ctx 64k        # check one window size for every model
  ./carl.sh fit --goal hard-code --scope downloaded   # auto fit's reasons for that goal / scope

Auto fit picks the best ranked stock model (rank 1 = best: parameters and density, then
quantization; abliterated models are only picked by hand; a custom model only when its card
says auto_fit: ./carl.sh card NAME) for a goal: everyday = the MoE
builds first (fast, usually sufficient), hard-code = the dense builds first (better at
code and hard tasks, slower). It wants two 96K windows (main session + a subagent), else
one, else the largest window of at least 32K, within the GPU limit and RAM less a reserve
for macOS and apps (6 GiB, 10 with the VM up; --reserve-gb N). llama.model = auto starts it;
config.json llama.auto_goal / llama.auto_fit (catalogue | downloaded) set goal and scope.

Need = weights + KV cache (window x bytes/token) + recurrent state + ~1 GiB of
compute buffers. The limit is what macOS lets the GPU use (Metal's
recommendedMaxWorkingSetSize, ~2/3 of RAM on 24-32 GB Macs, ~3/4 above), or an
`sudo sysctl iogpu.wired_limit_mb=N` override. Estimates: leave some margin.
The launcher (--check) refuses a start that needs more than the limit (FIT_CHECK=0 skips it).
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
from carl_core.domain.autofit import (GOAL_TEXT, GOALS, SCOPES, AutoFit, Budget, Goal, Scope, as_goal,  # noqa: E402
                                      as_scope, gib)
from carl_core.domain.errors import ConfigError  # noqa: E402
from carl_core.domain.fit import (DEFAULT_CTX, check_start, estimated_limit, max_ctx, need_bytes,  # noqa: E402
                                  prompt_cache_mib, reserve_bytes, swa_plan, window_label)
from carl_core.domain.gguf import GIB, OVERHEAD, ModelShape, kv_bytes_per_token, model_shape  # noqa: E402
from carl_core.domain.types import ModelInfo  # noqa: E402
from carl_core.wiring import GPU  # noqa: E402

TTY = sys.stdout.isatty()
B, DIM, R, GRN, YEL, RED, CYN = ("\x1b[1m", "\x1b[2m", "\x1b[0m", "\x1b[32m", "\x1b[33m", "\x1b[31m", "\x1b[36m") \
    if TTY else ("",) * 7
REFUSED = 3                                     # --check: the start needs more than the GPU limit


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
    ap.add_argument("--goal", choices=GOALS, help="auto fit's goal to explain (default: config llama.auto_goal)")
    ap.add_argument("--scope", choices=SCOPES, help="auto fit's candidates (default: config llama.auto_fit)")
    ap.add_argument("--reserve-gb", type=float, default=None,
                    help="RAM kept for macOS and apps (default 6, 10 with the VM network up)")
    # launcher-only options
    ap.add_argument("--check", metavar="GGUF", help=argparse.SUPPRESS)      # refuse a start that won't fit (exit 3)
    ap.add_argument("--plan", metavar="GGUF", help=argparse.SUPPRESS)       # print "SLOTS CACHE_MIB SWA"
    ap.add_argument("--pick-default", action="store_true", help=argparse.SUPPRESS)  # auto fit's pick (catalogue)
    ap.add_argument("--want-slots", default="auto", help=argparse.SUPPRESS)
    ap.add_argument("--kv", default="q4_0", help=argparse.SUPPRESS)
    ap.add_argument("--swa", default="auto", choices=("auto", "full", "window"), help=argparse.SUPPRESS)
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
    """Launcher plan, as "SLOTS CACHE_MIB SWA": slots (auto = 2 when two full windows fit the GPU
    limit, else 1), a RAM prompt cache from what is left after a reserve for macOS + apps (+ the
    VM), and for a model with sliding-window layers full or window (--swa; "-" for other models)."""
    shape, w = local_shape(args.plan)
    ctx = args.ctx or DEFAULT_CTX
    slots, full = swa_plan(args.swa, shape, w, ctx, args.want_slots, args.kv, limit)
    reserve = reserve_bytes(args.reserve_gb, vm_network_up())
    need = need_bytes(shape, w, ctx, slots, args.kv, swa_full=full is not False)
    print(slots, prompt_cache_mib(sysctl_int("hw.memsize"), need, reserve), "-" if full is None else "full" if full else "window")


def start_hint(fit: AutoFit) -> str:
    """How to start auto fit's pick with its plan."""
    if not fit.pick or not fit.plan:
        return ""
    slots = f" --slots {fit.plan.slots}" if fit.plan.slots == 1 else ""
    return f"./carl.sh llama --model {fit.pick.name} --ctx {window_label(fit.plan.ctx).lower()}{slots}"


def alternatives(budget: Budget) -> List[str]:
    """Auto fit's alternatives for a refused start: the best downloaded model that fits and,
    when better, the catalogue's pick to download. [] when they can't be worked out."""
    try:
        cfg = carl.app().load_config()
        goal, _ = carl.app().auto_settings(cfg)
        models = carl.app().all_models(cfg)
        here = carl.app().auto_fit(models, goal, "downloaded", budget)
        best = carl.app().auto_fit(models, goal, "catalogue", budget)
    except (ConfigError, OSError) as e:
        return [f"(auto fit unavailable: {e})"]
    out = []
    if here.pick and here.plan:
        out.append(f"Auto fit for this Mac ({goal}): {here.pick.name}, {here.plan.label()}: {start_hint(here)}")
    if best.pick and best.plan and best.name != here.name:
        out.append(f"Best for this Mac: {best.pick.name}, {best.plan.label()} (download: ./carl.sh download {best.pick.name})")
    return out


def model_label(path: str) -> str:
    """The model name a path belongs to, else its file name."""
    try:
        m = carl.app().find(path, carl.all_models())
    except (ConfigError, OSError):
        m = None
    return m.get("name", "") if m else os.path.basename(path)


def cmd_check(args: argparse.Namespace, limit: int, how: str) -> int:
    """Refuse (exit 3, the reasons on stderr) a start that needs more than the GPU limit: it
    would fail to load or swap the Mac to a crawl."""
    shape, w = local_shape(args.check)
    chk = check_start(shape, w, args.ctx or DEFAULT_CTX, args.slots, args.kv, limit, swa_full=args.swa != "window")
    if chk.fits:
        return 0
    err = sys.stderr
    pad = "       "
    print(f"{RED}error:{R} {model_label(args.check)} needs {gib(chk.need)} of GPU memory at {chk.setup()}, "
          f"but this Mac allows {gib(limit)} ({how}).", file=err)
    print(f"{pad}A start over the limit fails to load or swaps the Mac to a crawl, so it is refused.", file=err)
    if chk.largest:
        one = max_ctx(shape, w, limit, 1, args.kv) if chk.slots > 1 else 0
        print(f"{pad}Largest window that fits this model: --ctx {window_label(chk.largest).lower()}"
              + (f" with {chk.slots} slots, --ctx {window_label(one).lower()} with 1 slot" if one else "") + ".", file=err)
    else:
        print(f"{pad}The weights alone don't fit this Mac: use a smaller model.", file=err)
    for line in alternatives(carl.app().budget(reserve_gb=args.reserve_gb)):
        print(pad + line, file=err)
    print(f"{pad}What fits: ./carl.sh fit · expert override (it may not load, or swap): FIT_CHECK=0", file=err)
    return REFUSED


@dataclass(frozen=True)
class Row:
    name: str
    size: int
    rank: Optional[int]
    abliterated: bool
    shape: Optional[ModelShape]
    status: str                      # "downloaded", "not downloaded", or why the header is unavailable
    note: str = ""                   # a custom model's rank: from the user's card (in auto fit or not)


def model_rows(models: List[ModelInfo]) -> List[Row]:
    """Every model with its header shape, best rank first (unranked models last)."""
    app = carl.app()
    rows = []
    for m in models:
        rank = m.get("rank")
        try:
            shape: Optional[ModelShape] = app.shapes.local(m.get("path", "")) if m.get("status") == "downloaded" else \
                app.shapes.remote(m.get("hf") or {})
            status = "downloaded" if m.get("status") == "downloaded" else "not downloaded"
        except (OSError, ValueError) as e:
            shape, status = None, str(e)[:40]
        note = (f" · your card's rank (auto fit: {'on' if m.get('auto_fit') else 'off'})"
                if m.get("custom") and isinstance(rank, int) else "")
        rows.append(Row(m.get("name", ""), m.get("bytes", 0), rank if isinstance(rank, int) else None,
                        bool(m.get("abliterated")), shape, status, note))
    rows.sort(key=lambda r: (r.rank is None, r.rank or 0, r.name))
    return rows


def auto_lines(fits: List[AutoFit], shown: Goal, mine: Tuple[Goal, Scope], here: Optional[AutoFit]) -> List[str]:
    """Auto fit's pick per goal, then the reasons for one goal."""
    out = [f"{B}Auto fit{R} {DIM}(ranked stock models only; abliterated ones are picked by hand){R}"]
    for f in fits:
        star = "*" if (f.goal, f.scope) == mine else " "
        if f.pick and f.plan:
            where = "" if f.pick.downloaded else f"  {YEL}not downloaded{R}: ./carl.sh download {f.pick.name}"
            out.append(f" {star}{GOAL_TEXT[f.goal]:<40} {GRN}{f.pick.name}{R}, {f.plan.label()}, "
                       f"needs {gib(f.plan.need)}{where}")
        else:
            out.append(f" {star}{GOAL_TEXT[f.goal]:<40} {RED}nothing fits{R}")
    sel = next(f for f in fits if f.goal == shown)
    out.append(f"  {DIM}why ({shown}, {sel.scope}): {sel.because()}{R}")
    if sel.rejected:
        out.append(f"  {DIM}passed over (better rank first):{R}")
        out += [f"    {r.line()}" for r in sel.rejected]
    if here and here.pick and sel.pick and not sel.pick.downloaded:
        out.append(f"  {DIM}until it is downloaded, llama.model = auto starts {here.pick.name}, "
                   f"{here.plan.label() if here.plan else ''}: the best downloaded model that fits{R}")
    out.append(f"  {DIM}* = your setting: llama.auto_goal {mine[0]}, llama.auto_fit {mine[1]} (change it: ./carl.sh "
               f"config set llama.auto_goal everyday|hard-code, llama.auto_fit catalogue|downloaded){R}")
    return out


def cmd_table(args: argparse.Namespace, limit: int, how: str) -> None:
    slots = max(args.slots, 1)
    app = carl.app()
    cfg = app.load_config()
    models = app.all_models(cfg)
    mine = app.auto_settings(cfg)
    scope = as_scope(args.scope) if args.scope else mine[1]
    budget = app.budget(args.ram, args.reserve_gb)
    fits = [app.auto_fit(models, g, scope, budget) for g in GOALS]
    shown = as_goal(args.goal) if args.goal else mine[0]
    sel = next(f for f in fits if f.goal == shown)
    here = (app.auto_fit(models, shown, "downloaded", budget)          # this Mac only: what a start uses
            if sel.pick and not sel.pick.downloaded and not args.ram else None)
    print(f"{B}GPU memory limit:{R} {limit / GIB:.1f} GiB  {DIM}({how}){R}"
          + (f"   {B}slots:{R} {slots} (windows are per slot)" if slots > 1 else ""))
    print(f"{B}Auto fit allows:{R} {budget.describe()}\n")
    print("\n".join(auto_lines(fits, shown, mine, here)) + "\n")
    print(f"{DIM}need = weights + KV cache + recurrent state + ~{OVERHEAD / GIB:.0f} GiB buffers; "
          f"max window per KV type; rank 1 = best quality{R}\n")
    hdr = f"{'model':22} {'rank':>4} {'weights':>8} {'KV/token q4':>11}  {'max ctx q4':>10} {'max ctx q8':>10}"
    if args.ctx:
        hdr += f"  {'need @' + window_label(args.ctx):>10}"
    print(B + hdr + R)
    picks = {f.name for f in fits if f.name}
    for row in model_rows(models):
        mark = f"{CYN}★{R}" if row.name in picks else " "
        rank = f"{row.rank:>4}" if row.rank else f"{'–':>4}"
        name = f"{mark}{row.name:21}"
        if not row.shape:
            print(f"{name} {rank} {row.size / GIB:7.1f}G  {RED}(header unavailable: {row.status}){R}")
            continue
        m4, m8 = max_ctx(row.shape, row.size, limit, slots, "q4_0"), max_ctx(row.shape, row.size, limit, slots, "q8_0")
        col = RED if not m4 else YEL if m4 < 65536 else GRN
        line = (f"{name} {rank} {row.size / GIB:7.1f}G {kv_bytes_per_token(row.shape, 'q4_0') / 1024:9.1f}K  "
                f"{col}{window_label(m4):>10}{R} {window_label(m8):>10}")
        if args.ctx:
            nd = need_bytes(row.shape, row.size, args.ctx, slots, "q4_0")
            line += f"  {(GRN if nd <= limit else RED)}{nd / GIB:9.1f}G{R}"
        print(line + f"  {DIM}{row.status}{' · abliterated' if row.abliterated else ''}{row.note}{R}")
    print(f"\n{DIM}★ = an auto-fit pick · Raise the limit (resets at reboot; leave >= 6 GB for macOS):  "
          f"sudo sysctl iogpu.wired_limit_mb=<MB>{R}")


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    limit, how = gpu_limit(args.ram)
    if args.plan:
        cmd_plan(args, limit)
    elif args.check:
        return cmd_check(args, limit, how)
    elif args.pick_default:
        print(carl.app().pick_default(carl.all_models(), carl.app().budget(args.ram, args.reserve_gb)))
    else:
        cmd_table(args, limit, how)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (ConfigError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
