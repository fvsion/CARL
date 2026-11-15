"""`carl fit`: what fits on this computer: auto fit's pick for each goal (and why every better model was passed over),
then for each model (the catalogue and the models folder) the GPU memory it needs and the largest context window that
fits, per context memory type. The hidden options are the start's memory plan and check (`carl fit --plan GGUF ...`,
`carl fit --check GGUF ...`; the hidden commands `carl plan` and `carl check`) and auto fit's pick for the first run
(`--pick-default`).

  carl fit                  # this computer
  carl fit --ram 24         # what a computer with 24 GB of RAM would get (estimated GPU limit)
  carl fit --vram 12,8      # what fits on GPUs with 12 and 8 GiB (a preview: each less gpu.vram_keep; the model is
                            # split over them by layers: domain/memory/split.py)
  carl fit --ctx 64k        # check one window size for every model
  carl fit --goal hard-code --scope downloaded   # auto fit's reasons for that goal / scope
  carl fit NAME NAME        # measure these models with llama-server's own fit (A72)

Auto fit picks the best ranked stock model (rank 1 = best: published benchmarks and CARL's code test, then
quantization; abliterated models are only picked by hand; a custom model only when its card says auto_fit: carl card
NAME) for a goal: everyday = the fast builds first (MoE), hard-code = the dense builds first (better at code and hard
tasks, slower; a MoE build when the only dense builds that fit rank below it, W1.6). A small fast dense build (the
Gemma 4 E4B) is the pick only when no other model of its rank or better fits: else it is named as the fast option (W1.6). Its
order: 2 x 96K, 2 x 64K, 2 x 48K (main session + a subagent), then 1 x 96K, 1 x 64K, 1 x 48K; nothing below 48K. On NVIDIA GPUs it starts at 128K (A53): 2 x 128K, 2 x
96K, 2 x 64K, 2 x 48K, then 1 x 128K, 1 x 96K, 1 x 64K, 1 x 48K. A model whose MTP fits only without it runs n-gram in
that setup first. All within the GPU limit and RAM less a reserve for the system and apps (6 GiB, 10 with the VM up;
--reserve-gb N). llama.model = auto starts it; config.json llama.auto_goal / llama.auto_fit (catalogue | downloaded)
set goal and scope.

Need = weights (the whole file; a separate MTP drafter's while MTP runs) + KV cache (window x bytes/token) + recurrent
state + compute buffers (by -ub and the KV pool) + with MTP the draft context + a 0.75% margin (domain/memory/fit.py,
reference/memory.md). The limit and the plan come from application/fit.py, the one budget builder. Auto fit's pick for
each goal (and --pick-default's) is measured by llama-server's own fit (A67, FitService.measured_fit): on its file, or
before a download on a stub of its header; its line names the need's source, and Auto fit chooses again with less room
when that need is more than the room. The start (--check)
refuses a start that needs more than the limit (FIT_CHECK=0 skips it). On a PC with GPUs the plan and the check work on
each GPU: over several the model is split by layers (domain/memory/split.py, W6.6), the plan names the devices and the
layers of each (`devices CUDA0,CUDA1 21,20`, after the first line) and the lines about each GPU, and the check refuses a
split that does not fit with a line for each GPU. A MoE model that does not fit the GPUs whole keeps the experts of
its first N layers in the RAM (domain/memory/offload.py, W6.7): the plan answers `cpu-moe N` after the devices line, a
line about the RAM side after the GPUs' lines, and the RAM cache that the RAM leaves after the experts; the check takes
`--n-cpu-moe N` and refuses a start whose GPU side or RAM side does not fit. The table sizes each model at the -ub its
start uses (FitService.ub_order, A51: a typed -ub, the model's saved choice, its own, which a start lowers before it costs
context), and a line after Auto fit's
reasons says the pick's -ub and load mode, each with why (WCAL, A44, A45). The table shows a MoE model's largest
context with the experts of some layers in the RAM, with N in a note (decision A48; a model whole on the GPUs shows what
it holds). Contracts C-404, C-415.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Sequence, Set, Tuple

from ...application.fit import estimated
from ...domain.catalog.inventory import draft_bytes
from ...domain.config.settings import default_ub
from ...domain.memory.autofit import (GOAL_NAME, GOALS, SCOPE_TEXT, SCOPES, AutoFit, Budget, Goal, Scope, as_goal,
                                      as_scope, gib, ladder_words, model_spec)
from ...domain.memory.fit import (DEFAULT_CTX, UBATCH, Spec, check_start, max_ctx, mtp_spec, need_bytes, need_parts,
                                  ub_choice, window_label)
from ...domain.memory.budget import parse_vram
from ...domain.memory.gguf import GIB, ModelShape, kv_bytes_per_token
from ...domain.memory.shape import has_head
from ...domain.memory import calibration, offload, server_fit
from ...domain.memory.cpu_kv import KvChoice, name as kv_name
from ...domain.memory.offload import Offload
from ...domain.memory.server_fit import PickNeed, RowFit, row_line
from ...domain.memory.split import (Slot, Split, calib_of, fits, load_of, max_ctx as split_max_ctx, one_limit,
                                    plan_split)
from ...domain.server import ub_order as uo
from ...domain.server.launch_plan import KV_NOTE_WORDS, LOAD_WORDS, own_load
from ...domain.shared.errors import ConfigError
from ...domain.shared.types import ModelInfo
from ...domain.shared.ansi import ANSI
from ...ui.layout import columns, wrap
from .base import Cli

TTY = sys.stdout.isatty()
B, DIM, R, GRN, YEL, RED, CYN = ("\x1b[1m", "\x1b[2m", "\x1b[0m", "\x1b[32m", "\x1b[33m", "\x1b[31m", "\x1b[36m") \
    if TTY else ("",) * 7
ESTIMATE = "~"                                  # before a figure that is CARL's estimate, not llama-server's own fit (A72)
REFUSED = 3                                     # --check: the start needs more than the GPU limit


def parse_ctx(v: str) -> int:
    """A context size: tokens or Nk."""
    v = v.lower()
    try:
        return int(v[:-1]) * 1024 if v.endswith("k") else int(v)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a context size: {v!r} (use tokens or Nk, for example 96k)") from None


def help_action(cli: Cli, topic: str) -> type:
    """-h / --help: the same help as carl help TOPIC (cli/help_topics.py)."""
    class HelpAction(argparse.Action):
        def __init__(self, option_strings: List[str], dest: str = argparse.SUPPRESS, **kw: object) -> None:
            super().__init__(option_strings, dest, nargs=0, default=argparse.SUPPRESS, help="show this help")

        def __call__(self, parser: argparse.ArgumentParser, ns: argparse.Namespace, values: object,
                     option_string: Optional[str] = None) -> None:
            from . import help_text
            print(help_text(cli, topic))
            parser.exit()
    return HelpAction


def parse_args(cli: Cli, argv: Sequence[str]) -> argparse.Namespace:
    words = cli.words
    ap = argparse.ArgumentParser(prog=f"{words.command} fit", add_help=False)
    ap.add_argument("-h", "--help", action=help_action(cli, "fit"))
    ap.add_argument("names", nargs="*", metavar="NAME",
                    help="measure these models with llama-server's own fit (the table marks the other figures ~)")
    ap.add_argument("--ram", type=float, help=f"pretend {words.machine} has N GB of RAM (estimated GPU limit)")
    ap.add_argument("--vram", metavar="GIB[,GIB]", help="preview GPUs with these sizes in GiB (each less the part "
                                                         "kept free)")
    ap.add_argument("--ctx", type=parse_ctx, help="check this window size (tokens or Nk)")
    ap.add_argument("--slots", type=int, default=1,
                    help="parallel slots (e.g. 2 = main session + a subagent); each gets the full window")
    ap.add_argument("--goal", choices=GOALS, help="auto fit's goal to explain (default: config llama.auto_goal)")
    ap.add_argument("--scope", choices=SCOPES, help="auto fit's candidates (default: config llama.auto_fit)")
    ap.add_argument("--reserve-gb", type=float, default=None,
                    help=f"RAM kept for {words.os_and_apps} (default 6, 10 with the VM network up)")
    # launcher-only options
    ap.add_argument("--check", metavar="GGUF", help=argparse.SUPPRESS)      # refuse a start that won't fit (exit 3)
    ap.add_argument("--plan", metavar="GGUF", help=argparse.SUPPRESS)       # print "SLOTS CACHE_MIB SWA"
    ap.add_argument("--compute", action="store_true", help=argparse.SUPPRESS)  # with --check: print the compute buffer
    ap.add_argument("--need", action="store_true", help=argparse.SUPPRESS)  # with --check: CARL's need (A67)
    ap.add_argument("--bound", action="store_true", help=argparse.SUPPRESS)  # with --need: B's need (A67)
    ap.add_argument("--gpu-extra", type=int, default=0, help=argparse.SUPPRESS)  # bytes the server's fit needs more
    ap.add_argument("--pick-default", action="store_true", help=argparse.SUPPRESS)  # auto fit's pick (catalogue)
    ap.add_argument("--measured-only", action="store_true", help=argparse.SUPPRESS)  # NAME... : the measure, no table
    ap.add_argument("--want-slots", default="auto", help=argparse.SUPPRESS)
    ap.add_argument("--kv", default="q4_0", help=argparse.SUPPRESS)
    ap.add_argument("--kv-auto", action="store_true", help=argparse.SUPPRESS)  # --kv is nobody's choice: the CPU picks
    ap.add_argument("--swa", default="auto", choices=("auto", "full", "window"), help=argparse.SUPPRESS)
    ap.add_argument("--draft", metavar="GGUF", help=argparse.SUPPRESS)     # the MTP drafter the start loads (-md)
    ap.add_argument("--mmproj", metavar="GGUF", help=argparse.SUPPRESS)    # the vision projector the start loads
    ap.add_argument("--spec", default="none", help=argparse.SUPPRESS)      # the start's --spec-type (MTP counted)
    ap.add_argument("--spec-n", type=int, default=1, help=argparse.SUPPRESS)  # its --spec-draft-n-max
    ap.add_argument("--ub", type=int, default=UBATCH, help=argparse.SUPPRESS)  # its -ub (the compute buffers)
    ap.add_argument("--devices", metavar="NAMES", help=argparse.SUPPRESS)      # plan over these devices (CUDA0,CUDA1)
    ap.add_argument("--n-cpu-moe", type=cpu_moe_arg, default=None, metavar="N",  # experts of N layers in the RAM
                    help=argparse.SUPPRESS)                                      # (auto: the plan finds N)
    ap.add_argument("--ub-auto", action="store_true", help=argparse.SUPPRESS)  # --ub is the model's: may go lower
    ap.add_argument("--ub-pick", action="store_true", help=argparse.SUPPRESS)  # ...and untuned: the largest size that
    #                                                                           keeps the setup (W1.6, A52 replaced)
    ap.add_argument("--spec-kept", action="store_true", help=argparse.SUPPRESS)  # MTP is a person's or Auto-tune's
    #                                                                            choice: it stays with experts in RAM
    ap.add_argument("--load-reserve-gib", type=float, default=None,           # the load reserve (load mode none)
                    help=argparse.SUPPRESS)
    return ap.parse_args(list(argv))


def cpu_moe_arg(v: str) -> Optional[int]:
    """--n-cpu-moe: auto (None) or a number of layers from 0 to 999 (llama.n_cpu_moe)."""
    if v == "auto":
        return None
    if not v.isdigit() or int(v) > 999:
        raise argparse.ArgumentTypeError(f"not auto or a number of layers from 0 to 999: {v!r}")
    return int(v)


def vram_sizes(args: argparse.Namespace) -> Optional[List[float]]:
    """--vram's GPU sizes in GiB (None without it; ConfigError when it is not a list of sizes)."""
    return parse_vram(args.vram) if args.vram else None


def gpu_limit(cli: Cli, ram_gb: Optional[float], vram: Optional[Sequence[float]] = None) -> Tuple[int, str]:
    """(limit, how): this computer's, the estimate for one with ram_gb of RAM, or a preview of GPUs with vram GiB
    each (application/fit.py)."""
    if vram:
        return cli.api().carl.fit.preview_limit(vram)
    if ram_gb:
        limit, frac = estimated(ram_gb)
        return limit, f"an estimate for {cli.words.a_machine} with {ram_gb:g} GB of RAM: {frac:.0%} of the RAM"
    limit, how = cli.api().gpu_limit()
    return limit, ("macOS sets it" if "recommendedMaxWorkingSetSize" in how else
                   f"your setting {how}" if "wired_limit" in how else how)


def pair(a: float, b: float) -> Tuple[str, str]:
    """Two memory sizes in GiB that do not look equal when they are not (two decimals then)."""
    one = (f"{a / GIB:.1f} GiB", f"{b / GIB:.1f} GiB")
    return one if one[0] != one[1] or a == b else (f"{a / GIB:.2f} GiB", f"{b / GIB:.2f} GiB")


def local_shape(cli: Cli, path: str, draft: Optional[str] = None) -> Tuple[ModelShape, int]:
    """The model's header shape and its weights: the file, plus the MTP drafter a start loads with it
    when draft is given (-md). The launcher options give the drafter through --spec (start_spec)."""
    return cli.api().shape_of(path), os.path.getsize(path) + (os.path.getsize(draft) if draft else 0)


def start_spec(args: argparse.Namespace, shape: ModelShape) -> Spec:
    """The start's speculation (--spec, --spec-n; the drafter's weights from --draft)."""
    return mtp_spec(args.spec, args.spec_n, shape, os.path.getsize(args.draft) if args.draft else 0, args.spec_kept,
                    os.path.getsize(args.mmproj) if args.mmproj else 0)


def devices_of(cli: Cli, args: argparse.Namespace) -> List[Slot]:
    """The GPUs the plan and the check work on (application/fit.py FitService.slots): this PC's, those of --vram, or
    the ones --devices names; none on a Mac, with --ram (an estimate of another Mac) or when no GPU can be named."""
    if args.ram:
        return []
    slots = cli.api().carl.fit.slots(vram_sizes(args), [n for n in (args.devices or "").split(",") if n])
    if args.gpu_extra > 0:                       # the room llama-server's own fit needs beyond the estimate (A67)
        slots = [replace(s, room=s.room - args.gpu_extra) for s in slots]
    return slots


def split_of(devices: Sequence[Slot], shape: ModelShape, weights: float, ctx: int, slots: int, kv: str, full: bool,
             spec: Spec, ub: int) -> Split:
    """The split of a start over the devices."""
    return plan_split(devices, load_of(shape, weights, ctx, slots, kv, full, spec, ub, calib_of(devices)))


def room_text(d: Slot) -> str:
    """What a device's line says of its room: of the Y that a model can use there. Not for a GPU that shares the RAM:
    the plan's line about it (FitService.shared_note) already says what a model can use."""
    return "" if d.shared else f" of the {gib(d.room)} that a model can use there"


def device_lines(devices: Sequence[Slot], split: Split, need: float, off: Optional[Offload] = None,
                 os_and_apps: str = "the system and apps") -> List[str]:
    """The lines of a plan about the GPUs: one for each (the start shows them after the address). split: the plan over
    the devices when they are several; need: the whole start's need on one device (a single GPU's line). off: the
    experts in the RAM (its split is the GPU side; a line about the RAM side follows; os_and_apps: what the reserve
    is kept for, in the platform's words)."""
    if off is not None:
        return offload_lines(devices, off, os_and_apps)
    if len(devices) == 1:
        d = devices[0]
        return [f"{d.label()}, {d.name}: the whole model, {gib(need)}{room_text(d)}."]
    if not split.fits:
        return [f"The model does not fit the {len(devices)} GPUs as a split: {gib(split.need)} are needed, and the "
                f"GPUs hold {gib(split.room)} together."]
    out = [f"The model is split over {len(split.parts)} GPUs by layers ({split.layers} layers, {split.tensor_split()})."]
    out += [f"{p.slot.label()}, {p.slot.name}: {p.layers} layers, {gib(p.need)}{room_text(p.slot)}."
            for p in split.parts]
    out += [f"{s.label()}, {s.name}: no layers ({why})." for s, why in split.left]
    return out


def offload_lines(devices: Sequence[Slot], off: Offload, os_and_apps: str) -> List[str]:
    """The lines of a plan with MoE experts in the RAM: the GPUs' side (one line for each GPU, as a split over several)
    and the RAM side."""
    ram = offload.ram_line(off, os_and_apps)
    if not off.split.fits:
        return [f"The model does not fit the GPUs even with {off.words()}: {gib(off.split.need)} are needed on the "
                f"GPUs, and they hold {gib(off.split.room)}.", ram]
    if len(devices) == 1:
        d = devices[0]
        return [f"{d.label()}, {d.name}: the model without {off.words().replace(' in the RAM', '')}, "
                f"{gib(off.split.need)}{room_text(d)}.", ram]
    out = [f"The model is split over {len(off.split.parts)} GPUs by layers ({off.split.layers} layers, "
           f"{off.split.tensor_split()}), with {off.words()}."]
    out += [f"{p.slot.label()}, {p.slot.name}: {p.layers} layers, {gib(p.need)}{room_text(p.slot)}."
            for p in off.split.parts]
    out += [f"{s.label()}, {s.name}: no layers ({why})." for s, why in off.split.left]
    return out + [ram]


def cpu_kv_pick(cli: Cli, args: argparse.Namespace, shape: ModelShape, w: float, ctx: int, spec: Spec, limit: int,
                dedicated: int, devices: Sequence[Slot]) -> Optional[Tuple[KvChoice, int]]:
    """The context memory type of a start on the CPU alone (A71, cpu_kv.py) and the slots of the setup it keeps: the
    plan is made with the type the start asked for, and the type is chosen for that setup. None on any other start (a
    GPU backend, a Mac, a preview of another computer)."""
    fit = cli.api().carl.fit
    if args.ram or args.vram or not fit.cpu_only():
        return None
    choice = fit.plan_ub(args.ub, shape, w, ctx, args.want_slots, args.kv, args.swa, spec, args.reserve_gb, limit,
                         dedicated, devices, args.n_cpu_moe, args.ub_pick) if args.ub_auto else None
    ub = choice.ub if choice else args.ub
    sp, _, _ = fit.plan_offload(shape, w, ctx, args.want_slots, args.kv, args.swa, spec, ub, args.reserve_gb, limit,
                                dedicated, devices, args.n_cpu_moe)
    return fit.cpu_kv(sp, shape, w, ctx, ub, args.load_reserve_gib, args.reserve_gb), sp.slots


def cmd_plan(cli: Cli, args: argparse.Namespace, limit: int) -> None:
    """Launcher plan, as "SLOTS CACHE_MIB SWA SPEC" (Auto fit's order at the start's window): slots
    (auto = 2 when two windows fit what a model can use: the GPU limit, or RAM less the reserve for
    macOS + apps (+ the VM), whichever is smaller), MTP dropped before a slot (SPEC: the speculation
    to run, n-gram when MTP does not fit), a RAM prompt cache from what is left after the reserve, and
    for a model with sliding-window layers full or window (--swa; "-" for other models). The plan and the
    budget it stands on: application/fit.py, the one builder. A MoE model that does not fit the GPUs whole: the
    experts of N layers in the RAM (`cpu-moe N` after the devices line), when the GPU side fits with some N; the check
    then judges the RAM side too. A PC's answer ends with `room BYTES`: what a model could use on the GPUs (the start
    records it in its server file, W1.6). With --ub-auto (-ub is the model's, A45) a `ub N` line names the -ub that keeps
    the setup -ub 512 gets (1024 or 512 when --ub would cost a slot, the context or the fit), with a line why; with
    --ub-pick (a model without a tune record, W1.6) the largest size up to --ub that keeps that setup, n-gram on;
    with the free RAM known a `load none` or `load mapped` line and its line (A44, A45). With --kv-auto (nobody chose
    the context memory type) on a start on the CPU alone a `kv f16` (or q8_0, q4_0) line comes right after the first one,
    with a `Context memory type:` line why (A71: cpu_kv_pick, the first type the free RAM covers). A start with any expert layer
    in the RAM turns MTP off unless --spec-kept (a person's or Auto-tune's speculation): the answer's `mtp ram` line
    (A51)."""
    shape, w = local_shape(cli, args.plan)
    fit = cli.api().carl.fit
    ctx, devices = args.ctx or DEFAULT_CTX, devices_of(cli, args)
    dedicated = 0 if args.ram or args.vram else fit.dedicated
    spec = start_spec(args, shape)
    kvc = cpu_kv_pick(cli, args, shape, w, ctx, spec, limit, dedicated, devices) if args.kv_auto else None
    if kvc is not None:                                              # the CPU alone: the type for speed, the setup kept
        args = argparse.Namespace(**{**vars(args), "kv": kvc[0].kv, "want_slots": str(kvc[1])})
    choice = fit.plan_ub(args.ub, shape, w, ctx, args.want_slots, args.kv, args.swa, spec, args.reserve_gb, limit,
                         dedicated, devices, args.n_cpu_moe, args.ub_pick) if args.ub_auto else None
    ub = choice.ub if choice else args.ub
    sp, cache, off = fit.plan_offload(shape, w, ctx, args.want_slots, args.kv, args.swa, spec, ub, args.reserve_gb,
                                      limit, dedicated, devices, args.n_cpu_moe)
    if off is not None and not off.gpu_fits and args.n_cpu_moe is None:   # not even every expert in the RAM: as before
        sp, cache, off = fit.plan_offload(shape, w, ctx, args.want_slots, args.kv, args.swa, spec, ub,
                                          args.reserve_gb, limit, dedicated, devices, 0)
    print(sp.slots, cache, "-" if sp.swa_full is None else "full" if sp.swa_full else "window", sp.spec.kind)
    if kvc is not None:                                              # the CPU alone (A71): the type, and why
        print(f"kv {kvc[0].kv}\n{KV_NOTE_WORDS}{kvc[0].why()}")
    if fit.shared_gpus and not (args.ram or args.vram):              # a GPU that shares the RAM: the start names it
        print(fit.shared_note(args.reserve_gb))
    if sp.ram_off:                                                   # experts in the RAM: MTP off by default (A51)
        print("mtp ram")
    if choice is not None:                                           # the model's -ub, or a lower one for the setup
        print(f"ub {ub}" + (f"\n{choice.why}" if choice.why else ""))
    load = fit.load_mode(fit.load_need(sp, off, shape, w, ctx, args.kv, ub, devices), args.load_reserve_gib)
    if load.mode and not (args.ram or args.vram):                    # a preview is not this computer's free RAM
        print(f"load {load.mode}\n{load.line()}")
    if devices:                                                      # a PC: -dev, -ts, and a line for each GPU
        full = sp.swa_full is not False
        split = off.split if off else split_of(devices, shape, w, ctx, sp.slots, args.kv, full, sp.spec, ub)
        names = split.devices() if split.fits else ",".join(d.name for d in devices)
        print(f"devices {names}" + (f" {split.tensor_split()}" if split.fits and split.tensor_split() else ""))
        if off is not None:
            print(f"cpu-moe {off.n}")
        print("\n".join(device_lines(devices, split, need_bytes(shape, w, ctx, sp.slots, args.kv, full, sp.spec,
                                                                 ub, calib_of(devices)), off,
                                      cli.words.os_and_apps)))
        print(f"room {sum(d.room for d in devices) + max(args.gpu_extra, 0) * len(devices)}")   # before A67's gap (W1.6)


def start_hint(cli: Cli, fit: AutoFit) -> str:
    """How to start auto fit's pick with its plan."""
    if not fit.pick or not fit.plan:
        return ""
    slots = f" --slots {fit.plan.slots}" if fit.plan.slots == 1 else ""
    return f"{cli.words.command} start --model {fit.pick.name} --ctx {window_label(fit.plan.ctx).lower()}{slots}"


def alternatives(cli: Cli, budget: Budget) -> List[str]:
    """Auto fit's alternatives for a refused start: the best downloaded model that fits and,
    when better, the catalogue's pick to download. [] when they can't be worked out."""
    try:
        app = cli.api().carl
        cfg = app.load_config()
        goal, _ = app.auto_settings(cfg)
        models = app.all_models(cfg)
        here = app.auto_fit(models, goal, "downloaded", budget)
        best = app.auto_fit(models, goal, "catalogue", budget)
    except (ConfigError, OSError) as e:
        return [f"CARL cannot get the choice of Auto fit: {e}."]
    out = []
    words = cli.words
    if here.pick and here.plan:
        out.append(f"Auto fit chooses {here.pick.name} with {here.plan.describe()} for {words.machine} (goal "
                   f"{GOAL_NAME[goal]}). To start it: {start_hint(cli, here)}")
    if best.pick and best.plan and best.name != here.name:
        out.append(f"The best model for {words.machine} is {best.pick.name} with {best.plan.describe()}. To "
                   f"download it: {words.command} download {best.pick.name}")
    return out


def model_label(cli: Cli, path: str) -> str:
    """The model name a path belongs to, else its file name."""
    try:
        m = cli.api().carl.find(path, cli.api().all_models())
    except (ConfigError, OSError):
        m = None
    return m.get("name", "") if m else os.path.basename(path)


def cmd_check(cli: Cli, args: argparse.Namespace, limit: int, how: str) -> int:
    """Refuse (exit 3, the reasons on stderr) a start that needs more than the GPU limit: it
    would fail to load or swap the Mac to a crawl. The first line is one whole "error:" sentence
    (the dashboard shows the launcher's error lines); the advice follows, indented."""
    shape, w = local_shape(cli, args.check)
    spec, full = start_spec(args, shape), args.swa != "window"
    devices = devices_of(cli, args)
    if args.need:                               # CARL's need of the start on its device, beside the server's fit (A67)
        if len(devices) <= 1:
            print(need_answer(cli, args, devices, shape, w, spec, full))
        return 0
    if args.compute:                            # the start's compute buffer, bytes, for the dry run's gap line (A57)
        if len(devices) <= 1:
            parts = need_parts(shape, w, args.ctx or DEFAULT_CTX, args.slots, args.kv, full, spec, args.ub,
                               calib_of(devices), args.n_cpu_moe or None)       # the experts in the RAM set the staged copy
            print(f"compute {parts.compute:.0f}")
        return 0
    if args.n_cpu_moe and offload.offloads(devices, shape):
        return check_offload(cli, args, devices, shape, w, spec, full)
    if len(devices) > 1:
        return check_split(cli, args, devices, shape, w, spec, full)
    limit = int(one_limit(devices, limit))      # one device: its own room, not every GPU's added up
    calib = calib_of(devices)                   # the backend's constants (CUDA); the Mac's with no device
    chk = check_start(shape, w, args.ctx or DEFAULT_CTX, args.slots, args.kv, limit, full, spec, args.ub, calib)
    if chk.fits:
        return 0
    err = sys.stderr
    pad = "       "
    words = cli.words
    need, lim = pair(chk.need, limit)
    print(f"{RED}error:{R} {model_label(cli, args.check)} does not fit {words.machine} with {chk.setup()}: it needs "
          f"{need}, "
          f"and the GPU memory limit is {lim}. CARL refuses the start, because the model would fail to load or "
          f"{words.the_machine} would become very slow.", file=err)
    if spec.mtp:
        plain = need_bytes(shape, w, chk.ctx, chk.slots, args.kv, full, spec.without_mtp(), args.ub, calib)
        print(f"{pad}With MTP, it needs the draft context of MTP too. Without MTP (n-gram only), it needs "
              f"{pair(plain, limit)[0]}.", file=err)
    if chk.largest:
        one = max_ctx(shape, w, limit, 1, args.kv, full, spec, args.ub, calib) if chk.slots > 1 else 0
        text = (f"With {chk.slots} slot{'s' if chk.slots != 1 else ''}, the largest context that fits is "
                f"{window_label(chk.largest)} tokens (--ctx {window_label(chk.largest).lower()}).")
        if one:
            text += f" With 1 slot, it is {window_label(one)} tokens (--ctx {window_label(one).lower()} --slots 1)."
        print(pad + text, file=err)
    else:
        print(f"{pad}The weights alone do not fit {words.machine}. Use a smaller model.", file=err)
    for line in alternatives(cli, cli.api().budget(reserve_gb=args.reserve_gb)):
        print(pad + line, file=err)
    print(f"{pad}{words.command} fit shows what fits. To start anyway (the model can fail to load): FIT_CHECK=0", file=err)
    return REFUSED


def need_answer(cli: Cli, args: argparse.Namespace, devices: Sequence[Slot], shape: ModelShape, w: float, spec: Spec,
                full: bool) -> str:
    """--check --need on one device (A67): `need BYTES CONTEXT` (the need the check compares with the room, without
    --gpu-extra, and the backend's context in it), `draft BYTES` (a drafter in its own file: its file and its context's
    compute buffer bounded from the header, which llama-server's fit cannot measure) and `output NAME` (the output
    weight, for D's check)."""
    ctx, slots, calib = args.ctx or DEFAULT_CTX, max(args.slots, 1), calib_of(devices)
    if args.bound:                              # B's need: the upper bound where llama-server's fit cannot run
        calib = calibration.bound(calib)
    fit = cli.api().carl.fit
    ram, reserve = fit.ram_room(args.reserve_gb) if args.n_cpu_moe else (0.0, 0.0)
    need = fit.need_of(shape, w, ctx, slots, args.kv, full, spec, args.ub, devices, args.n_cpu_moe, calib, ram,
                       reserve)
    lines = [f"need {need:.0f} {calib.context:.0f}"]
    if spec.draft_bytes and (spec.drafted or (spec.mtp and not has_head(shape))):
        bound = server_fit.draft_bound_of(shape, ctx, slots, args.ub, full, "cuda" if devices else "metal",
                                          spec.draft_bytes)
        lines.append(f"draft {spec.draft_bytes + bound:.0f}")
    lines.append("output " + ("output.weight" if shape.get("output_bytes", 1) else "token_embd.weight"))
    return "\n".join(lines)


def check_split(cli: Cli, args: argparse.Namespace, devices: Sequence[Slot], shape: ModelShape, w: float, spec: Spec,
                full: bool) -> int:
    """cmd_check over several GPUs: refuse (exit 3, the reasons on stderr) a start whose split does not fit them. The
    first line is one whole "error:" sentence; then a line for each GPU (how many layers it could hold), the largest
    context that fits as a split, and what Auto fit chooses instead."""
    ctx, slots = args.ctx or DEFAULT_CTX, max(args.slots, 1)
    split = split_of(devices, shape, w, ctx, slots, args.kv, full, spec, args.ub)
    if split.fits:
        return 0
    err, pad, words = sys.stderr, "       ", cli.words
    setup = f"{slots} slot{'s' if slots != 1 else ''} × {window_label(ctx)} tokens ({args.kv.replace('_0', '')})"
    need, room = pair(split.need, split.room)
    print(f"{RED}error:{R} {model_label(cli, args.check)} does not fit {len(devices)} GPUs with {setup}: as a split it "
          f"needs {need} (each GPU has a compute buffer of its own), and a model can use {room} on them together. CARL "
          f"refuses the start, because the model would fail to load.", file=err)
    for d, k in zip(devices, split.holds):
        print(f"{pad}{d.label()}, {d.name}: a model can use {gib(d.room)}; it can hold {k} of the {split.layers} "
              f"layers.", file=err)
    if spec.mtp:
        plain = fits(devices, load_of(shape, w, ctx, slots, args.kv, full, spec.without_mtp(), args.ub,
                                      calib_of(devices)))
        print(f"{pad}With MTP, it needs the draft context of MTP too. Without MTP (n-gram only), it "
              f"{'fits' if plain else 'does not fit either'}.", file=err)
    calib = calib_of(devices)
    largest = split_max_ctx(shape, w, devices, slots, args.kv, full, spec, args.ub, calib)
    if largest:
        one = split_max_ctx(shape, w, devices, 1, args.kv, full, spec, args.ub, calib) if slots > 1 else 0
        text = (f"With {slots} slot{'s' if slots != 1 else ''}, the largest context that fits is "
                f"{window_label(largest)} tokens (--ctx {window_label(largest).lower()}).")
        if one:
            text += f" With 1 slot, it is {window_label(one)} tokens (--ctx {window_label(one).lower()} --slots 1)."
        print(pad + text, file=err)
    else:
        print(f"{pad}The weights alone do not fit the GPUs. Use a smaller model.", file=err)
    for line in alternatives(cli, cli.api().budget(reserve_gb=args.reserve_gb, vram=vram_sizes(args))):
        print(pad + line, file=err)
    print(f"{pad}{words.command} fit shows what fits. To start anyway (the model can fail to load): FIT_CHECK=0", file=err)
    return REFUSED


def check_offload(cli: Cli, args: argparse.Namespace, devices: Sequence[Slot], shape: ModelShape, w: float,
                  spec: Spec, full: bool) -> int:
    """cmd_check for a start with the MoE experts of --n-cpu-moe N layers in the RAM: refuse (exit 3, the reasons on
    stderr) when its GPU side does not fit the GPUs, or its RAM side and the smallest RAM cache do not fit the RAM less
    the part kept for the system and apps."""
    ctx, slots = args.ctx or DEFAULT_CTX, max(args.slots, 1)
    ram, reserve = cli.api().carl.fit.ram_room(args.reserve_gb)
    off = offload.plan_of(shape, w, ctx, slots, args.kv, full, spec, args.ub, devices, ram, reserve,
                          calib_of(devices), args.n_cpu_moe)
    if off is None or off.fits:
        return 0
    err, pad, words = sys.stderr, "       ", cli.words
    setup = f"{slots} slot{'s' if slots != 1 else ''} × {window_label(ctx)} tokens ({args.kv.replace('_0', '')})"
    gpus = "the GPU" if len(devices) == 1 else f"{len(devices)} GPUs"
    head = f"{RED}error:{R} {model_label(cli, args.check)} does not fit {words.machine} with {setup} and {off.words()}:"
    if not off.gpu_fits:
        need, room = pair(off.split.need, off.split.room)
        print(f"{head} on {gpus} it needs {need}, and a model can use {room} there. CARL refuses the start, because "
              f"the model would fail to load.", file=err)
        for d, k in zip(devices, off.split.holds):
            print(f"{pad}{d.label()}, {d.name}: a model can use {gib(d.room)}; it can hold {k} of the "
                  f"{off.split.layers} layers.", file=err)
        print(f"{pad}More layers' experts in the RAM need less on the GPUs: the setting llama.n_cpu_moe "
              f"({words.command} config set llama.n_cpu_moe auto finds the number).", file=err)
    else:
        need, room = pair(off.ram + offload.CACHE_MIN, off.room)
        print(f"{head} in the RAM the experts, the CPU's compute buffer and the smallest RAM cache need {need}, and the "
              f"RAM leaves {room} after {gib(reserve)} kept free for {words.os_and_apps}. CARL refuses the start, "
              f"because {words.machine} would become very slow.", file=err)
        print(f"{pad}Fewer layers' experts in the RAM, a smaller context or fewer slots need less: the setting "
              f"llama.n_cpu_moe ({words.command} config set llama.n_cpu_moe auto finds the number).", file=err)
    print(f"{pad}{words.command} fit shows what fits. To start anyway (the model can fail to load): FIT_CHECK=0", file=err)
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
    spec: Spec = Spec()              # its speculation: this computer's Auto-tune result, else the catalogue's
    ub: int = UBATCH                 # the -ub its start uses (FitService.ub_order, A51)
    ub_auto: bool = False            # ub is the model's own, so a start lowers it before it costs the setup
    info: Optional[ModelInfo] = None  # the models-list entry (its file, its place on Hugging Face): what the fit measures


def model_rows(cli: Cli, models: List[ModelInfo], cfg: Optional[object] = None) -> List[Row]:
    """Every model with its header shape, best rank first (unranked models last), each at the -ub its start uses
    (FitService.ub_order, A51: the one order). cfg: config.json."""
    app = cli.api().carl
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
        spec = model_spec(m, shape)
        order = app.fit.ub_order(m, cfg)                                # type: ignore[arg-type]
        rows.append(Row(m.get("name", ""), m.get("bytes", 0) + draft_bytes(m), rank if isinstance(rank, int) else None,
                        bool(m.get("abliterated")), shape, status, note, m.get("bytes", 0), spec,
                        order.ub, not order.fixed, m))
    rows.sort(key=lambda r: (r.rank is None, r.rank or 0, r.name))
    return rows


def largest(row: "Row", limit: float, devices: Sequence[Slot], slots: int, kv: str, full: bool,
            ub: Optional[int] = None) -> int:
    """The largest context of each slot that fits the GPU memory limit, or (several GPUs) the split over them. At the
    -ub of the row: the one its start uses (A51; A46: the user, "Models own ub"); ub: at that -ub instead (a start
    lowers the model's own -ub down to 512 before it takes a smaller context, A45)."""
    if row.shape is None:
        return 0
    ub = ub or row.ub
    if len(devices) > 1:
        return split_max_ctx(row.shape, row.weights, devices, slots, kv, full, row.spec, ub, calib_of(devices))
    return max_ctx(row.shape, row.weights, one_limit(devices, limit), slots, kv, full, row.spec, ub,
                   calib_of(devices))


def largest_in_ram(row: "Row", devices: Sequence[Slot], ram: Tuple[float, float], slots: int, kv: str, full: bool,
                   ub: Optional[int] = None) -> int:
    """The largest context of each slot with the experts of some layers in the RAM (a MoE model on discrete GPUs; 0
    for any other, and when not even 4K fits), at the -ub of the row or ub. ram: the RAM and the part kept free for the
    system and apps. Decision A48: the table showed only the model whole on the GPUs, so a MoE model on an 8 GB card
    showed –."""
    if row.shape is None or not offload.offloads(devices, row.shape):
        return 0
    return offload.max_ctx(row.shape, row.weights, devices, ram[0], ram[1], slots, kv, full, offload.ram_spec(row.spec),
                           ub or row.ub, calib_of(devices))


def experts_at(row: "Row", ctx: int, devices: Sequence[Slot], ram: Tuple[float, float], slots: int, kv: str,
               full: bool, ub: Optional[int] = None) -> Optional[Offload]:
    """The start of this window with the experts in the RAM (None: not a MoE model on discrete GPUs)."""
    if row.shape is None:
        return None
    return offload.plan_of(row.shape, row.weights, ctx, slots, kv, full, offload.ram_spec(row.spec), ub or row.ub,
                           devices, ram[0], ram[1], calib_of(devices))


def needed(row: "Row", ctx: int, limit: float, devices: Sequence[Slot], slots: int, kv: str,
           full: bool) -> Tuple[float, bool]:
    """(the memory a window needs, whether it fits): within the limit, or (several GPUs) as a split over them. At the
    -ub the start uses: a person's as it is, or the model's own lowered to 1024 or 512 when it would not fit
    (fit.ub_choice)."""
    assert row.shape is not None
    shape = row.shape

    def at(ub: int) -> Tuple[float, bool]:
        if len(devices) > 1:
            split = split_of(devices, shape, row.weights, ctx, slots, kv, full, row.spec, ub)
            return split.need, split.fits
        nd = need_bytes(shape, row.weights, ctx, slots, kv, full, row.spec, ub, calib_of(devices))
        return nd, nd <= one_limit(devices, limit)
    if not row.ub_auto:
        return at(row.ub)
    return at(ub_choice(row.ub, lambda u: (at(u)[1], window_label(ctx))).ub)


KVS = ("q4_0", "q8_0")                          # the two context memory types of the table's columns
Measured = Dict[str, Tuple[Optional[RowFit], Optional[RowFit]]]       # a row's name: its q4 and q8 columns, by the fit


def row_fit(cli: Cli, row: "Row", kv: str, limit: float, devices: Sequence[Slot], slots: int, full: bool,
            ram: Tuple[float, float], ask: bool) -> Optional[RowFit]:
    """One column of a row by llama-server's own fit (FitService.measure_row, A72): the largest window the estimate gives,
    measured, and made again with the gap less room when the fit needs more. ask False: only a fit that was kept. None:
    the column stays CARL's estimate."""
    if row.shape is None or row.info is None or len(devices) > 1:
        return None
    shape = row.shape

    def plan_at(less: float) -> Optional[Tuple[int, int, float]]:
        devs = [replace(d, room=int(d.room - less)) for d in devices]
        lim = limit - less
        whole, in_ram = largest(row, lim, devs, slots, kv, full), largest_in_ram(row, devs, ram, slots, kv, full)
        ctx = max(whole, in_ram)
        if not ctx:
            return None
        off = experts_at(row, ctx, devs, ram, slots, kv, full) if in_ram > whole else None
        if off is not None:
            return ctx, off.n, off.split.need
        return ctx, 0, need_bytes(shape, row.weights, ctx, slots, kv, full, row.spec, row.ub, calib_of(devs))
    return cli.api().carl.fit.measure_row(row.info, shape, row.weights, calib_of(devices), row.spec, slots, kv, row.ub,
                                          full, devices, ram[0], ram[1], one_limit(devices, limit), plan_at, ask)


def measure_rows(cli: Cli, rows: List["Row"], asked: Set[str], limit: float, devices: Sequence[Slot], slots: int,
                 full: bool, ram: Tuple[float, float]) -> Measured:
    """Each row by the fit, in both columns: the rows asked for (Auto fit's picks, the names on the command line) run
    it, the others take what an earlier fit kept (server-fits.json, by the build, the file, the argv and the GPU)."""
    return {r.name: tuple(row_fit(cli, r, kv, limit, devices, slots, full, ram, r.name in asked)  # type: ignore[misc]
                          for kv in KVS) for r in rows if r.shape is not None}


def not_measured(cli: Cli, args: argparse.Namespace, devices: Sequence[Slot]) -> str:
    """Why a row asked for is still CARL's estimate."""
    if args.ram or args.vram:
        return "A preview of another computer cannot be measured here."
    if len(devices) > 1:
        return "The model is split over several GPUs, which the fit does not measure."
    return ("llama-server's own fit could not run on it: this computer has no llama-server with --fit, or the header of "
            "a model that is not downloaded could not be read.")


def named_models(cli: Cli, args: argparse.Namespace, models: List[ModelInfo]) -> List[str]:
    """The models named on the command line, as the list calls them, in order (ConfigError for one that is not known)."""
    app = cli.api().carl
    out: List[str] = []
    for n in args.names:
        m = app.find(n, models)
        if m is None:
            raise ConfigError(f"no model {n!r}. {cli.words.command} models lists them.")
        if m.get("name", n) not in out:
            out.append(m.get("name", n))
    return out


def measured_lines(cli: Cli, args: argparse.Namespace, named: List[str], rows: List["Row"], got: Measured,
                   devices: Sequence[Slot], limit: float, slots: int, full: bool, ram: Tuple[float, float],
                   w: int) -> List[str]:
    """The sentence on each model named on the command line: its windows by the fit next to CARL's estimate, or why it
    stays an estimate."""
    out: List[str] = []
    by = {r.name: r for r in rows}
    for n in named:
        row = by.get(n)
        if row is None:
            continue
        if row.shape is None:
            out += wrap(f"{row.name}: CARL cannot read its GGUF header: {row.status}.", w, "  ", "    ")
            continue
        both = got.get(n, (None, None))
        est = [max(largest(row, limit, devices, slots, kv, full), largest_in_ram(row, devices, ram, slots, kv, full))
               for kv in KVS]
        out += wrap(row_line(n, [("q4", both[0], est[0]), ("q8", both[1], est[1])], not_measured(cli, args, devices)),
                    w, "  ", "    ")
    return out


def start_note(cli: Cli, f: AutoFit, models: List[ModelInfo], cfg: object, devices: Sequence[Slot], limit: float,
               args: argparse.Namespace) -> str:
    """How a start of Auto fit's pick runs, in one line: its -ub and where it comes from (and why it is lower, A45), and
    the load mode and why (A44, A45; not for a preview of another computer, whose free RAM CARL does not know). "" when
    there is no pick. cfg: config.json (the -ub order's inputs, and the user's own load option in llama.extra_args)."""
    pick, plan = f.pick, f.plan
    m = next((x for x in models if pick and x.get("name") == pick.name), None)
    if not pick or not plan or pick.shape is None or m is None:
        return ""
    fs = cli.api().carl.fit
    preview = bool(args.ram or args.vram)
    dedicated = 0 if preview else fs.dedicated
    order = fs.ub_order(m, cfg)                       # type: ignore[arg-type]
    want = order.ub
    source = {uo.TYPED: "your server options", uo.KEPT: "your choice for this model", uo.CONFIG: "your setting llama.ub",
              uo.TUNED: "its Auto-tune record", uo.DEFAULT: f"the default on {cli.words.machine}"}[order.source]
    slots, swa = str(plan.slots), f.budget.swa
    choice = None if order.fixed else fs.plan_ub(want, pick.shape, pick.weights, plan.ctx, slots, plan.kv, swa,
                                                 plan.spec, args.reserve_gb, limit, dedicated, devices,
                                                 pick=order.source == uo.DEFAULT)
    ub = choice.ub if choice else want
    if ub != want:                                  # lowered for the setup: not the default or the record's own
        source = f"lowered from {want}, " + source.replace("the default on", "the default of")
    text = f"A start of {pick.name} with {plan.describe()} runs at -ub {ub} ({source})."
    if order.ask:                                   # a start that cannot ask uses the tuned -ub (A51)
        text += f" {order.ask.sets()}, which a start in a terminal asks about; a script or router mode ignores it."
    if choice and choice.why:
        text += f" {choice.why}"
    if preview:
        return text
    mine = own_load([str(a) for a in (getattr(cfg, "llama", {}).get("extra_args") or [])])
    if mine:                                        # the user's own load option wins (the start keeps it)
        return text + f" {LOAD_WORDS}as your server options say ({mine})."
    sp, _, off = fs.plan_offload(pick.shape, pick.weights, plan.ctx, slots, plan.kv, swa, plan.spec, ub,
                                 args.reserve_gb, limit, dedicated, devices)
    load = fs.load_mode(fs.load_need(sp, off, pick.shape, pick.weights, plan.ctx, plan.kv, ub, devices))
    text += " " + (load.line() or f"{LOAD_WORDS}the weights stay mapped from the file (llama.cpp's default): "
                                  f"CARL cannot read the free RAM.")
    if fs.cpu_kv_open(m, cfg):                      # type: ignore[arg-type]
        kvc = fs.cpu_kv(sp, pick.shape, pick.weights, plan.ctx, ub, None, args.reserve_gb)      # on the CPU alone (A71)
        text += f" {KV_NOTE_WORDS}{kv_name(kvc.kv)} ({kvc.why()})."
    return text


def need_text(f: AutoFit, need: Optional[PickNeed]) -> str:
    """The pick's need in one sentence: measured, with its source (A67), else CARL's estimate."""
    assert f.plan is not None
    return need.sentence() if need is not None else f"It needs {gib(f.plan.need)}."


def auto_lines(cli: Cli, fits: List[AutoFit], shown: Goal, mine: Tuple[Goal, Scope], here: Optional[AutoFit],
               w: int, needs: Optional[Dict[Goal, Optional[PickNeed]]] = None) -> List[str]:
    """Auto fit's choice for each goal, then the reasons for one goal, wrapped to w columns. needs: each goal's pick
    measured by llama-server's own fit (FitService.measured_fit; none: the estimate's words)."""
    needs = needs or {}
    cmd = cli.words.command
    out = [f"{B}AUTO FIT{R}"]
    out += wrap(f"Auto fit chooses from the stock models with a quality rank ({SCOPE_TEXT[fits[0].scope]}). "
                "It never chooses an abliterated model. The goal everyday takes the fast models first (MoE). The "
                "goal hard code takes the dense models first: better code, but slower. It takes a MoE model when "
                "the only dense models that fit rank below it. It takes a small fast dense model only when no other "
                "model of its rank or better fits: otherwise it names it as the fast option.", w, "  ")
    out += wrap(f"Its order of setups: {ladder_words(fits[0].budget.tiers)}. Nothing below 48K. When MTP does not "
                "fit a setup, Auto fit uses n-gram only before it takes the next setup.", w, "  ")
    for f in fits:
        yours = " (your goal)" if f.goal == mine[0] else ""
        if f.pick and f.plan:
            where = ("" if f.pick.downloaded else
                     f" It is not downloaded. To download it: {cmd} download {f.pick.name}")
            note = f.plan.spec_note(f.budget.words)
            text = (f"Goal {GOAL_NAME[f.goal]}{yours}: {f.pick.name} with {f.plan.describe()}. "
                    f"{need_text(f, needs.get(f.goal))}" + (f" {note}." if note else "") + where)
        else:
            text = f"Goal {GOAL_NAME[f.goal]}{yours}: no model fits."
        out += wrap(text, w, "  ", "    ")
    sel = next(f for f in fits if f.goal == shown)
    measured = needs.get(shown)                     # the goal line's figure, so both lines give one need (W1.6)
    out += wrap(f"Why ({GOAL_NAME[shown]}): {sel.because(measured.need if measured else None)}.", w, "  ", "    ")
    if here and here.pick and sel.pick and not sel.pick.downloaded:
        out += wrap(f"Until {sel.pick.name} is downloaded, the model auto starts {here.pick.name} with "
                    f"{here.plan.describe() if here.plan else ''}: the best downloaded model that fits.", w, "  ", "    ")
    if sel.rejected:
        out += wrap("Models with a better quality rank that Auto fit did not choose:", w, "  ")
        for r in sel.rejected:
            out += wrap(r.line() + ".", w, "    ", "      ")
    out += wrap(f"To change your goal: {cmd} config set llama.auto_goal everyday or hard-code. To choose only "
                f"from the downloaded models: {cmd} config set llama.auto_fit downloaded.", w, "  ")
    return out


def cmd_table(cli: Cli, args: argparse.Namespace, limit: int, how: str) -> None:
    """The table (and, for the models named on the command line, what the fit says of each)."""
    w = cli.width()
    slots = max(args.slots, 1)
    app = cli.api().carl
    cfg = app.load_config()
    models = app.all_models(cfg)
    mine = app.auto_settings(cfg)
    scope = as_scope(args.scope) if args.scope else mine[1]
    vram = vram_sizes(args)
    budget = app.budget(args.ram, args.reserve_gb, vram)
    named = named_models(cli, args, models)
    if args.measured_only:
        cmd_measure(cli, args, limit, models, cfg, budget, named)
        return
    measured = {g: app.measured_fit(models, g, scope, budget) for g in GOALS}     # each pick measured (A67)
    fits = [measured[g][0] for g in GOALS]
    needs = {g: measured[g][1] for g in GOALS}
    shown = as_goal(args.goal) if args.goal else mine[0]
    sel = next(f for f in fits if f.goal == shown)
    here = (app.auto_fit(models, shown, "downloaded", budget)          # this Mac only: what a start uses
            if sel.pick and not sel.pick.downloaded and not args.ram and not vram else None)
    words = app.words
    mac = f"{words.a_machine} with " + f"{args.ram:g} GB of RAM" if args.ram else words.machine
    print("\n".join(wrap(f"{B}Memory.{R} The GPU memory limit of {mac} is {limit / GIB:.1f} GiB ({how}). "
                         f"{budget.explain()} (GiB is memory: 1 GiB = 1.07 GB.)", w)))
    devices = devices_of(cli, args)
    if devices:                                             # a PC: what a model can use on each GPU
        for d in devices:
            print("\n".join(wrap(f"{d.label()}, {d.name}: a model can use {gib(d.room)}.", w, "  ")))
        if len(devices) > 1:
            print("\n".join(wrap("A model is split over the GPUs by layers, and each GPU has a compute buffer of its "
                                 "own, so the largest context below is a little less than the GPUs hold together.",
                                 w, "  ")))
    print()
    print("\n".join(auto_lines(cli, fits, shown, (mine[0], scope), here, w, needs)))
    note = start_note(cli, here if here and here.pick else sel, models, cfg, devices, limit, args)
    if note:
        print("\n".join(wrap(note, w, "  ", "    ")))
    print()
    swa_full = budget.swa_full
    cmd = words.command
    cache = "the full cache" if swa_full else "the window cache"
    rows = model_rows(cli, models, cfg)
    ram = app.fit.ram_room(args.reserve_gb) if devices else (0.0, 0.0)         # experts in the RAM: a PC with GPUs
    preview = bool(args.ram or vram)                  # another computer's figures: this one's fit says nothing of it
    asked = set() if preview else {f.name for f in fits if f.name} | set(named)
    got = {} if preview else measure_rows(cli, rows, asked, limit, devices, slots, swa_full, ram)
    if named:
        print("\n".join(wrap(f"{B}MEASURED{R}", w)))
        print("\n".join(measured_lines(cli, args, named, rows, got, devices, limit, slots, swa_full, ram, w)))
        print()
    print("\n".join(wrap(f"{B}ALL MODELS{R}", w)))
    print("\n".join(wrap(f"The largest context of each slot that fits the GPU memory limit, with {slots} "
                         f"slot{'s' if slots != 1 else ''}, for each context memory type (q4 and q8), with the "
                         f"speculation of the catalogue (MTP counted).", w, "  ")))
    yours = [r.name for r in rows if not r.ub_auto]                   # a -ub of the user's (A51), not the model's own
    batch = (f"The batch size is each model's own -ub: its Auto-tune record's, else {default_ub(app.fit.os_name)} on "
             f"{words.machine}"
             + (f", except for {', '.join(yours)}, where it is yours (a -ub typed in your server options, your choice "
                f"for the model, or llama.ub for a model without a record)" if yours else "")
             + f". The largest context is the one at that -ub. A start takes a smaller -ub (down to 512) before it "
               f"takes a smaller context; the note gives the context at -ub 512 when that is more.")
    print("\n".join(wrap(batch, w, "  ")))
    nw = max([len("model")] + [len(r.name) for r in rows])
    extra = f"  {'needed ' + window_label(args.ctx):>11}" if args.ctx else ""
    head1 = f"  {'':{nw}}  {'':>4}  {'':>9}  {'per 1K':>8}  {'largest context':>15}"
    head2 = f"  {'model':{nw}}  {'rank':>4}  {'weights':>9}  {'tokens':>8}  {'q4':>7} {'q8':>7}{extra}  status"
    print(B + head1.rstrip() + R)
    print(B + head2 + R)
    picks = {f.name for f in fits if f.name}
    in_ram = False                                   # a row's context needs experts in the RAM (the legend says so)
    for row in rows:
        mark = f"{CYN}★{R}" if row.name in picks else " "
        rank = f"{row.rank:>4}" if row.rank else f"{'–':>4}"
        lead = f"{mark} {row.name:{nw}}  {rank}  {row.size / GIB:5.1f} GiB"
        if not row.shape:
            print(f"{lead}  {RED}CARL cannot read its GGUF header: {row.status}{R}")
            continue
        w4, w8 = largest(row, limit, devices, slots, "q4_0", swa_full), largest(row, limit, devices, slots, "q8_0",
                                                                                swa_full)
        r4, r8 = (largest_in_ram(row, devices, ram, slots, "q4_0", swa_full),
                  largest_in_ram(row, devices, ram, slots, "q8_0", swa_full))      # a MoE model: its experts in the RAM
        m4, m8 = max(w4, r4), max(w8, r8)
        f4, f8 = got.get(row.name, (None, None))            # measured by the fit (A72), else CARL's estimate: ~
        if f4:
            m4, ram4 = f4.ctx, f4.cpu_moe > 0
        else:
            ram4 = r4 > w4
        if f8:
            m8 = f8.ctx
        col = RED if not m4 else YEL if m4 < 65536 else GRN
        per_k = kv_bytes_per_token(row.shape, "q4_0") * 1024 / 2 ** 20
        c4, c8 = ("" if f4 else ESTIMATE) + window_label(m4), ("" if f8 else ESTIMATE) + window_label(m8)
        line = f"{lead}  {per_k:4.1f} MiB  {col}{c4:>7}{R} {c8:>7}"
        if args.ctx:
            nd, ok = needed(row, args.ctx, limit, devices, slots, "q4_0", swa_full)
            off = None if ok else experts_at(row, args.ctx, devices, ram, slots, "q4_0", swa_full)
            if off is not None:                       # the GPU side of the start with the experts in the RAM
                nd, ok, in_ram = off.split.need, off.fits, True
            line += f"  {(GRN if ok else RED)}{nd / GIB:7.1f} GiB{R}"
        notes = []
        if f4 or f8:
            notes.append(f"measured: {(f4 or f8).source()}")        # type: ignore[union-attr]
        if row.abliterated:
            notes.append("abliterated")
        if ram4 and m4:                               # the context needs the experts of some layers in the RAM
            n = experts_at(row, m4, devices, ram, slots, "q4_0", swa_full)
            if n is not None:
                in_ram = True
                notes.append(f"experts in the RAM: {f4.cpu_moe if f4 else n.n} of {n.layers} layers at "
                             f"{window_label(m4)}")
        if row.ub_auto and row.ub > UBATCH:          # the model's own -ub costs context: what -ub 512 still reaches
            low = max(largest(row, limit, devices, slots, "q4_0", swa_full, UBATCH),
                      largest_in_ram(row, devices, ram, slots, "q4_0", swa_full, UBATCH))
            if low > m4:
                notes.append(f"-ub {UBATCH}: {window_label(low)}")
        if row.shape.get("swa_window"):              # sliding-window layers: the other cache, for comparison
            other = max(largest(row, limit, devices, slots, "q4_0", not swa_full),
                        largest_in_ram(row, devices, ram, slots, "q4_0", not swa_full))
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
              (ESTIMATE, f"CARL's estimate, not measured. A figure without it is measured by llama-server's own fit and "
                         f"kept: Auto fit's choices, and the models you name ({cmd} fit NAME)."),
              ("colours", "Green: 64K or more. Yellow: less than 64K. Red: the model does not fit.")]
    if in_ram:
        legend.append(("experts in the RAM", "A MoE model on discrete GPUs can keep the experts of its first N layers in "
                                             "the RAM (--n-cpu-moe). Its largest context is with that N, at the q4 "
                                             "context the note names: a little slower than the whole model on the GPUs, "
                                             "and a larger context than the GPUs hold. Every figure is at the model's "
                                             "own -ub."))
    if args.ctx:
        legend.append((f"needed {window_label(args.ctx)}", f"The memory needed for {window_label(args.ctx)} tokens "
                                                          f"in each slot (q4)" + ("; with experts in the RAM, the GPUs' "
                                                                                 "part." if in_ram else ".")))
    print("\n".join(columns(legend, w, max_term=16)))
    print()
    print("\n".join(wrap("Memory needed = weights + context memory + recurrent state + compute buffers (by the "
                         "batch size and the context) + with MTP its draft context (its context memory and two more "
                         f"compute buffers) + 0.75%. The figures marked {ESTIMATE} are estimates: keep some memory "
                         "free. Auto fit's choice and the models you name are measured with llama-server's own fit: "
                         "on the model file (E), or before a download on a stub of its header (stub E); each answer "
                         "is kept, and the table shows it later without a run. A start measures its own need the "
                         "same way, and makes its plan again when that is more. Where the fit cannot run, both take "
                         "CARL's upper bound (B).", w)))
    if cli.os == "macos":                                  # a Mac: its GPU limit is a setting (sysctl)
        print("\n".join(wrap("Experts only: sudo sysctl iogpu.wired_limit_mb=MB gives the GPU more memory until the "
                             f"next restart. Keep at least 6 GiB for macOS, or {cli.words.the_machine} can stop.", w)))


def cmd_measure(cli: Cli, args: argparse.Namespace, limit: float, models: List[ModelInfo], cfg: object,
                budget: Budget, named: List[str]) -> None:
    """--measured-only NAME...: the fit of the models named, and nothing else (the dashboard's key runs it). Each
    answer is kept for the next table."""
    if not named:
        raise ConfigError(f"name a model: {cli.words.command} fit NAME")
    devices = devices_of(cli, args)
    ram = cli.api().carl.fit.ram_room(args.reserve_gb) if devices else (0.0, 0.0)
    rows = model_rows(cli, [m for m in models if m.get("name") in named], cfg)
    slots = max(args.slots, 1)
    got = {} if args.ram or args.vram else measure_rows(cli, rows, set(named), limit, devices, slots, budget.swa_full, ram)
    print("\n".join(measured_lines(cli, args, named, rows, got, devices, limit, slots, budget.swa_full, ram, cli.width())))


def run(cli: Cli, argv: Sequence[str]) -> int:
    """The command: the table, or a hidden option's answer. An error is one "error:" line and exit 1 (a wrong
    option: argparse's message and exit 2)."""
    try:
        args = parse_args(cli, argv)
        limit, how = gpu_limit(cli, args.ram, vram_sizes(args))
        if (args.plan or args.check) and args.gpu_extra > 0:    # the server's fit needs more than the estimate (A67)
            limit -= args.gpu_extra
        if args.plan:
            cmd_plan(cli, args, limit)
        elif args.check:
            return cmd_check(cli, args, limit, how)
        elif args.pick_default:
            print(cli.api().pick_default(None, cli.api().budget(args.ram, args.reserve_gb, vram_sizes(args))))
        else:
            cmd_table(cli, args, limit, how)
        return 0
    except (ConfigError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def plan(cli: Cli, argv: Sequence[str]) -> int:
    """`carl plan GGUF ...` (hidden): `carl fit --plan GGUF ...`."""
    return run(cli, ["--plan", *argv])


def check(cli: Cli, argv: Sequence[str]) -> int:
    """`carl check GGUF ...` (hidden): `carl fit --check GGUF ...` (exit 3: the start does not fit)."""
    return run(cli, ["--check", *argv])
