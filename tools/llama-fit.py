#!/usr/bin/env python3
"""What fits on this Mac: auto fit's pick for each goal (and why every better model was
passed over), then for each model (host/catalog.json + the models folder) the GPU memory
it needs and the largest context window that fits, per KV cache type.

  ./carl.sh fit                  # this Mac
  ./carl.sh fit --ram 24         # what a 24 GB Mac would get (estimated GPU limit)
  ./carl.sh fit --ctx 64k        # check one window size for every model
  ./carl.sh fit --goal hard-code --scope downloaded   # auto fit's reasons for that goal / scope

Auto fit picks the best ranked stock model (rank 1 = best: published benchmarks and CARL's
code test, then quantization; abliterated models are only picked by hand; a custom model only
when its card says auto_fit: ./carl.sh card NAME) for a goal: everyday = the fast builds
first (MoE, and small dense models such as the Gemma 4 E4B: fast, usually sufficient),
hard-code = the dense builds first (better at code and hard tasks, slower). Its order, on every
Mac: 2 x 96K, 2 x 64K, 2 x 48K (main session + a subagent), then 1 x 96K, 1 x 64K, 1 x 48K;
nothing below 48K. A model whose MTP fits only without it runs n-gram in that setup first. All within
the GPU limit and RAM less a reserve for macOS and apps (6 GiB, 10 with the VM up; --reserve-gb N).
llama.model = auto starts it; config.json llama.auto_goal / llama.auto_fit (catalogue | downloaded)
set goal and scope.

Need = weights (the whole file; a separate MTP drafter's while MTP runs) + KV cache (window x
bytes/token) + recurrent state + compute buffers (by -ub and the KV pool) + with MTP the draft
context + a 0.75% margin (carl_core/domain/fit.py, reference/memory.md). The limit is what macOS
lets the GPU use (Metal's recommendedMaxWorkingSetSize, ~2/3 of RAM on 24-32 GB Macs, ~3/4
above), or an `sudo sysctl iogpu.wired_limit_mb=N` override.
The launcher (--check) refuses a start that needs more than the limit (FIT_CHECK=0 skips it).
"""
import os
import sys

sys.dont_write_bytecode = True                    # keep the shared folder free of __pycache__
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse  # noqa: E402
import re  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from typing import List, Optional, Tuple  # noqa: E402

import carl  # noqa: E402
from carl_core.adapters.gguf_reader import local_meta  # noqa: E402
from carl_core.adapters.system import sysctl_int, vm_network_up  # noqa: E402
from carl_core.domain.autofit import (GOAL_NAME, GOALS, SCOPE_TEXT, SCOPES, AutoFit, Budget,  # noqa: E402
                                      Goal, Scope, as_goal, as_scope, gib)
from carl_core.domain.errors import ConfigError  # noqa: E402
from carl_core.domain.fit import (DEFAULT_CTX, UBATCH, Spec, check_start, estimated_limit, max_ctx,  # noqa: E402
                                  mtp_spec, need_bytes, prompt_cache_mib, reserve_bytes, start_plan, window_label)
from carl_core.domain.gguf import GIB, ModelShape, kv_bytes_per_token, model_shape  # noqa: E402
from carl_core.domain.models import draft_bytes  # noqa: E402
from carl_core.domain.types import ModelInfo  # noqa: E402
from carl_core.wiring import GPU  # noqa: E402
from carl_help import columns, render, width, wrap  # noqa: E402

TTY = sys.stdout.isatty()
B, DIM, R, GRN, YEL, RED, CYN = ("\x1b[1m", "\x1b[2m", "\x1b[0m", "\x1b[32m", "\x1b[33m", "\x1b[31m", "\x1b[36m") \
    if TTY else ("",) * 7
ANSI = re.compile(r"\x1b\[[0-9;]*m")
REFUSED = 3                                     # --check: the start needs more than the GPU limit


def parse_ctx(v: str) -> int:
    """A context size: tokens or Nk."""
    v = v.lower()
    try:
        return int(v[:-1]) * 1024 if v.endswith("k") else int(v)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a context size: {v!r} (use tokens or Nk, for example 96k)") from None


class HelpAction(argparse.Action):
    """-h / --help: the same help as ./carl.sh help fit (tools/carl_help.py)."""
    def __init__(self, option_strings: List[str], dest: str = argparse.SUPPRESS, **kw: object) -> None:
        super().__init__(option_strings, dest, nargs=0, default=argparse.SUPPRESS, help="show this help")

    def __call__(self, parser: argparse.ArgumentParser, ns: argparse.Namespace, values: object,
                 option_string: Optional[str] = None) -> None:
        print(render("fit"))
        parser.exit()


def parse_args(argv: List[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="./carl.sh fit", add_help=False)
    ap.add_argument("-h", "--help", action=HelpAction)
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
    ap.add_argument("--draft", metavar="GGUF", help=argparse.SUPPRESS)     # the MTP drafter the start loads (-md)
    ap.add_argument("--spec", default="none", help=argparse.SUPPRESS)      # the start's --spec-type (MTP counted)
    ap.add_argument("--spec-n", type=int, default=1, help=argparse.SUPPRESS)  # its --spec-draft-n-max
    ap.add_argument("--ub", type=int, default=UBATCH, help=argparse.SUPPRESS)  # its -ub (the compute buffers)
    return ap.parse_args(argv)


def gpu_limit(ram_gb: Optional[float]) -> Tuple[int, str]:
    """(limit, how): this Mac's, or the estimate for a Mac with ram_gb of RAM."""
    if ram_gb:
        limit, frac = estimated_limit(ram_gb * GIB)
        return limit, f"an estimate for a Mac with {ram_gb:g} GB of RAM: {frac:.0%} of the RAM"
    limit, how = GPU.limit()
    return limit, ("macOS sets it" if "recommendedMaxWorkingSetSize" in how else
                   f"your setting {how}" if "wired_limit" in how else how)


def pair(a: float, b: float) -> Tuple[str, str]:
    """Two memory sizes in GiB that do not look equal when they are not (two decimals then)."""
    one = (f"{a / GIB:.1f} GiB", f"{b / GIB:.1f} GiB")
    return one if one[0] != one[1] or a == b else (f"{a / GIB:.2f} GiB", f"{b / GIB:.2f} GiB")


def local_shape(path: str, draft: Optional[str] = None) -> Tuple[ModelShape, int]:
    """The model's header shape and its weights: the file, plus the MTP drafter a start loads with it
    when draft is given (-md). The launcher options give the drafter through --spec (start_spec)."""
    return model_shape(local_meta(path)), os.path.getsize(path) + (os.path.getsize(draft) if draft else 0)


def start_spec(args: argparse.Namespace, shape: ModelShape) -> Spec:
    """The start's speculation (--spec, --spec-n; the drafter's weights from --draft)."""
    return mtp_spec(args.spec, args.spec_n, shape, os.path.getsize(args.draft) if args.draft else 0)


def cmd_plan(args: argparse.Namespace, limit: int) -> None:
    """Launcher plan, as "SLOTS CACHE_MIB SWA SPEC" (Auto fit's order at the start's window): slots
    (auto = 2 when two windows fit what a model can use: the GPU limit, or RAM less the reserve for
    macOS + apps (+ the VM), whichever is smaller), MTP dropped before a slot (SPEC: the speculation
    to run, n-gram when MTP does not fit), a RAM prompt cache from what is left after the reserve, and
    for a model with sliding-window layers full or window (--swa; "-" for other models)."""
    shape, w = local_shape(args.plan)
    ctx = args.ctx or DEFAULT_CTX
    reserve = reserve_bytes(args.reserve_gb, vm_network_up())
    ram = sysctl_int("hw.memsize")
    allowed = min(limit, ram - reserve) if ram else limit
    sp = start_plan(args.swa, shape, w, ctx, args.want_slots, args.kv, allowed, start_spec(args, shape), args.ub)
    need = need_bytes(shape, w, ctx, sp.slots, args.kv, sp.swa_full is not False, sp.spec, args.ub)
    print(sp.slots, prompt_cache_mib(ram, need, reserve), "-" if sp.swa_full is None else "full" if sp.swa_full
          else "window", sp.spec.kind)


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
        return [f"CARL cannot get the choice of Auto fit: {e}."]
    out = []
    if here.pick and here.plan:
        out.append(f"Auto fit chooses {here.pick.name} with {here.plan.describe()} for this Mac (goal "
                   f"{GOAL_NAME[goal]}). To start it: {start_hint(here)}")
    if best.pick and best.plan and best.name != here.name:
        out.append(f"The best model for this Mac is {best.pick.name} with {best.plan.describe()}. To download it: "
                   f"./carl.sh download {best.pick.name}")
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
    would fail to load or swap the Mac to a crawl. The first line is one whole "error:" sentence
    (the dashboard shows the launcher's error lines); the advice follows, indented."""
    shape, w = local_shape(args.check)
    spec, full = start_spec(args, shape), args.swa != "window"
    chk = check_start(shape, w, args.ctx or DEFAULT_CTX, args.slots, args.kv, limit, full, spec, args.ub)
    if chk.fits:
        return 0
    err = sys.stderr
    pad = "       "
    need, lim = pair(chk.need, limit)
    print(f"{RED}error:{R} {model_label(args.check)} does not fit this Mac with {chk.setup()}: it needs {need}, "
          f"and the GPU memory limit is {lim}. CARL refuses the start, because the model would fail to load or "
          f"the Mac would become very slow.", file=err)
    if spec.mtp:
        plain = need_bytes(shape, w, chk.ctx, chk.slots, args.kv, full, spec.without_mtp(), args.ub)
        print(f"{pad}With MTP, it needs the draft context of MTP too. Without MTP (n-gram only), it needs "
              f"{pair(plain, limit)[0]}.", file=err)
    if chk.largest:
        one = max_ctx(shape, w, limit, 1, args.kv, full, spec, args.ub) if chk.slots > 1 else 0
        text = (f"With {chk.slots} slot{'s' if chk.slots != 1 else ''}, the largest context that fits is "
                f"{window_label(chk.largest)} tokens (--ctx {window_label(chk.largest).lower()}).")
        if one:
            text += f" With 1 slot, it is {window_label(one)} tokens (--ctx {window_label(one).lower()} --slots 1)."
        print(pad + text, file=err)
    else:
        print(f"{pad}The weights alone do not fit this Mac. Use a smaller model.", file=err)
    for line in alternatives(carl.app().budget(reserve_gb=args.reserve_gb)):
        print(pad + line, file=err)
    print(f"{pad}./carl.sh fit shows what fits. To start anyway (the model can fail to load): FIT_CHECK=0", file=err)
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
    weights: int = 0                 # the model file (size: with its MTP drafter)
    spec: Spec = Spec()              # its speculation from the catalogue (MTP counted)


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
        tune = m.get("tune") or {}
        n = tune.get("spec_n", 1)
        spec = mtp_spec(str(tune.get("spec") or "none"), n if isinstance(n, int) else 1, shape, draft_bytes(m))
        rows.append(Row(m.get("name", ""), m.get("bytes", 0) + draft_bytes(m), rank if isinstance(rank, int) else None,
                        bool(m.get("abliterated")), shape, status, note, m.get("bytes", 0), spec))
    rows.sort(key=lambda r: (r.rank is None, r.rank or 0, r.name))
    return rows


def auto_lines(fits: List[AutoFit], shown: Goal, mine: Tuple[Goal, Scope], here: Optional[AutoFit],
               w: int) -> List[str]:
    """Auto fit's choice for each goal, then the reasons for one goal, wrapped to w columns."""
    out = [f"{B}AUTO FIT{R}"]
    out += wrap(f"Auto fit chooses from the stock models with a quality rank ({SCOPE_TEXT[fits[0].scope]}). "
                "It never chooses an abliterated model. The goal everyday takes the fast models first (MoE and "
                "small dense). The goal hard code takes the dense models first: better code, but slower.", w, "  ")
    out += wrap("Its order of setups: 2 slots × 96K, 64K or 48K tokens (the main session and a subagent), then "
                "1 slot × 96K, 64K or 48K tokens. Nothing below 48K. When MTP does not fit a setup, Auto fit "
                "uses n-gram only before it takes the next setup.", w, "  ")
    for f in fits:
        yours = " (your goal)" if f.goal == mine[0] else ""
        if f.pick and f.plan:
            where = ("" if f.pick.downloaded else
                     f" It is not downloaded. To download it: ./carl.sh download {f.pick.name}")
            note = f.plan.spec_note()
            text = (f"Goal {GOAL_NAME[f.goal]}{yours}: {f.pick.name} with {f.plan.describe()}. It needs "
                    f"{gib(f.plan.need)}." + (f" {note}." if note else "") + where)
        else:
            text = f"Goal {GOAL_NAME[f.goal]}{yours}: no model fits."
        out += wrap(text, w, "  ", "    ")
    sel = next(f for f in fits if f.goal == shown)
    out += wrap(f"Why ({GOAL_NAME[shown]}): {sel.because()}.", w, "  ", "    ")
    if here and here.pick and sel.pick and not sel.pick.downloaded:
        out += wrap(f"Until {sel.pick.name} is downloaded, the model auto starts {here.pick.name} with "
                    f"{here.plan.describe() if here.plan else ''}: the best downloaded model that fits.", w, "  ", "    ")
    if sel.rejected:
        out += wrap("Models with a better quality rank that Auto fit did not choose:", w, "  ")
        for r in sel.rejected:
            out += wrap(r.line() + ".", w, "    ", "      ")
    out += wrap("To change your goal: ./carl.sh config set llama.auto_goal everyday or hard-code. To choose only "
                "from the downloaded models: ./carl.sh config set llama.auto_fit downloaded.", w, "  ")
    return out


def cmd_table(args: argparse.Namespace, limit: int, how: str) -> None:
    w = width()
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
    mac = "a Mac with " + f"{args.ram:g} GB of RAM" if args.ram else "this Mac"
    print("\n".join(wrap(f"{B}Memory.{R} The GPU memory limit of {mac} is {limit / GIB:.1f} GiB ({how}). "
                         f"{budget.explain()} (GiB is memory: 1 GiB = 1.07 GB.)", w)))
    print()
    print("\n".join(auto_lines(fits, shown, (mine[0], scope), here, w)))
    print()
    swa_full = budget.swa_full
    cache = "the full cache" if swa_full else "the window cache"
    print("\n".join(wrap(f"{B}ALL MODELS{R}", w)))
    print("\n".join(wrap(f"The largest context of each slot that fits the GPU memory limit, with {slots} "
                         f"slot{'s' if slots != 1 else ''}, for each context memory type (q4 and q8), with the "
                         f"speculation of the catalogue (MTP counted).", w, "  ")))
    rows = model_rows(models)
    nw = max([len("model")] + [len(r.name) for r in rows])
    extra = f"  {'needed ' + window_label(args.ctx):>11}" if args.ctx else ""
    head1 = f"  {'':{nw}}  {'':>4}  {'':>9}  {'per 1K':>8}  {'largest context':>15}"
    head2 = f"  {'model':{nw}}  {'rank':>4}  {'weights':>9}  {'tokens':>8}  {'q4':>7} {'q8':>7}{extra}  status"
    print(B + head1.rstrip() + R)
    print(B + head2 + R)
    picks = {f.name for f in fits if f.name}
    for row in rows:
        mark = f"{CYN}★{R}" if row.name in picks else " "
        rank = f"{row.rank:>4}" if row.rank else f"{'–':>4}"
        lead = f"{mark} {row.name:{nw}}  {rank}  {row.size / GIB:5.1f} GiB"
        if not row.shape:
            print(f"{lead}  {RED}CARL cannot read its GGUF header: {row.status}{R}")
            continue
        m4 = max_ctx(row.shape, row.weights, limit, slots, "q4_0", swa_full, row.spec)
        m8 = max_ctx(row.shape, row.weights, limit, slots, "q8_0", swa_full, row.spec)
        col = RED if not m4 else YEL if m4 < 65536 else GRN
        per_k = kv_bytes_per_token(row.shape, "q4_0") * 1024 / 2 ** 20
        line = f"{lead}  {per_k:4.1f} MiB  {col}{window_label(m4):>7}{R} {window_label(m8):>7}"
        if args.ctx:
            nd = need_bytes(row.shape, row.weights, args.ctx, slots, "q4_0", swa_full, row.spec)
            line += f"  {(GRN if nd <= limit else RED)}{nd / GIB:7.1f} GiB{R}"
        notes = []
        if row.abliterated:
            notes.append("abliterated")
        if row.shape.get("swa_window"):              # sliding-window layers: the other cache, for comparison
            other = max_ctx(row.shape, row.weights, limit, slots, "q4_0", not swa_full, row.spec)
            notes.append(f"{'window' if swa_full else 'full'} cache: {window_label(other)}")
        if row.note:
            notes.append(row.note)
        status = f"  {DIM}{row.status}{R}"
        tail = f"  {DIM}{' · '.join(notes)}{R}" if notes else ""
        if len(ANSI.sub("", line + status + tail)) <= w:
            print(line + status + tail)
        else:
            print(line + status)
            if notes:
                print(f"    {DIM}{' · '.join(notes)}{R}")
    print()
    legend = [("★", "Auto fit chooses this model for a goal."),
              ("rank", "The quality rank: 1 is the best. Models with the same rank have the same base model."),
              ("weights", "The memory of the model file, with its MTP drafter (Gemma 4)."),
              ("per 1K tokens", "The context memory for 1K tokens (q4)."),
              ("largest context", f"The largest context of each slot that fits, in K tokens (K = 1024). "
                                  f"For Gemma models it is the context with {cache}, as Auto fit plans it (your "
                                  f"setting cache.swa is {budget.swa}). The note gives the context with the other "
                                  f"cache. – means that the model does not fit."),
              ("colours", "Green: 64K or more. Yellow: less than 64K. Red: the model does not fit.")]
    if args.ctx:
        legend.append((f"needed {window_label(args.ctx)}", f"The memory needed for {window_label(args.ctx)} tokens "
                                                          f"in each slot (q4)."))
    print("\n".join(columns(legend, w, max_term=16)))
    print()
    print("\n".join(wrap("Memory needed = weights + context memory + recurrent state + compute buffers (by the "
                         "batch size and the context) + with MTP its draft context (its context memory and two more "
                         "compute buffers) + 0.75%. These are estimates: keep some memory free.", w)))
    print("\n".join(wrap("Experts only: sudo sysctl iogpu.wired_limit_mb=MB gives the GPU more memory until the next "
                         "restart. Keep at least 6 GiB for macOS, or the Mac can stop.", w)))


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
