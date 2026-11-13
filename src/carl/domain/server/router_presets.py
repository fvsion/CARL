"""Router mode (opt-in, llama.mode = router): one llama-server router that loads the model a
client asks for, one at a time (--models-max 1: llama.cpp stops the loaded model before it
starts the next, and lets a busy one finish its request first; measured 2026-10-03).

The router reads a presets INI (--models-preset): a [*] section shared by every model and one
section per downloaded model, named after it, with the settings a single-model start of that
model would use (its effective tune: window, slots, KV cache, speculation, sampling, the RAM
cache sized for it, the thinking-toggle chat template). A model whose setup doesn't fit the
GPU limit is left out, with the reason: the router would otherwise load it and fail or swap.

Keys are llama-server's long option names without the dashes ("cache-idle-slots = false" is
--no-cache-idle-slots); the router passes them to the child server it starts for the model,
and sets host, port, the API key and the alias (= the section name) itself. A model whose MTP
speculation comes from a separate drafter (Gemma 4) gets it as spec-draft-model (-md). On a PC each model also gets
its devices (device = -dev) and, over several GPUs, the layers of each (tensor-split = -ts, split-mode = layer): the
split is planned for each model (domain/memory/split.py, W6.6). A MoE model that does not fit the GPUs whole keeps
the experts of its first N layers in the RAM (n-cpu-moe = N; domain/memory/offload.py, W6.7), and its RAM cache is what
the RAM leaves after them. Each model has its own -ub (ubatch-size: the model's, as a single start of it lowers it
for the setup, A45 and A46) and, when the free RAM holds it, load-mode = none (A44, A45). Pure.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ..hardware.cpu_topology import PINNED_WORDS
from ..shared.errors import ConfigError
from ..memory.calibration import Calibration
from ..memory.fit import NGRAM, Spec, check_start, mtp_spec, prompt_cache_mib, start_plan, window_label
from ..memory import offload
from ..memory.gguf import ModelShape
from ..memory.split import Slot, calib_of, load_of, max_ctx as split_max_ctx, ok_for, one_limit, plan_split
from .argv import SPLIT_LAYER
from ..shared.platform_words import MACOS, PlatformWords
from ..shared.types import Settings


def header(words: PlatformWords = MACOS) -> str:
    """The INI's first lines: where its values come from."""
    return ("; CARL's llama.cpp router presets: written at every router start from config.json, Auto-tune and\n"
            f"; the catalogue ({words.command} config show). Edits here are lost: change the settings instead.\n")


SLOT_SIMILARITY = 0.5           # -sps: a subagent sharing the system prompt doesn't take the main slot


@dataclass(frozen=True)
class Common:
    """Server-wide settings for every model ([*])."""
    batch: int
    ubatch: int
    ckpt: int
    ckpt_step: int
    cache_ram: Optional[int]    # MiB; None = sized per model from free RAM
    slot_dir: str = ""          # --slot-save-path: the disk cache of prompt states (client/shared/carl-cache.js)
    swa_mode: str = "auto"      # cache.swa: sliding-window models at full length (full), the window, or auto


@dataclass(frozen=True)
class ModelPlan:
    """One model's section: what a single-model start of it would run."""
    name: str
    path: str
    ctx: int                    # per slot
    slots: int
    kv: str
    spec: str
    spec_n: int
    sampling: Tuple[Tuple[str, float], ...]
    cache_mib: int
    template: Optional[str]
    need: float
    swa: bool = False           # sliding-window layers at full length: swa-full (saved prompt states restore)
    draft: Optional[str] = None  # the MTP drafter (spec-draft-model, the -md of a single start)
    window: bool = False        # sliding-window layers that keep only the window (the window cache)
    devices: str = ""           # device (-dev): the devices of a PC ("" a Mac), llama.cpp's names
    tensor_split: str = ""      # tensor-split (-ts): the layers of each device ("" for one)
    cpu_moe: int = 0            # n-cpu-moe (--n-cpu-moe): the layers whose MoE experts stay in the RAM (0 none)
    ub: int = 0                 # ubatch-size (-ub): the model's own (0: the [*] section's)
    ram: float = 0.0            # bytes: the RAM side with experts in the RAM (offload.Offload.ram; 0 without)
    load: str = ""              # load-mode: "none" when the free RAM holds the model under it (A44); "" the mapped load
    ub_note: str = ""           # a config -ub the router ignored for the tuned one (A51): "-ub 2048 (tuned); config's..."
    mtp_off: bool = False       # MTP is off because experts are in the RAM (A51)
    threads: int = 0            # threads (--threads) with experts in the RAM: one per physical core, pinned (A54); 0 none
    cpu_mask: str = ""          # cpu-mask (--cpu-mask) of those threads (0x555555)
    kv_note: str = ""           # the CPU alone chose the context memory type: why, in a few words (A72; KvChoice.short)
    stale_note: str = ""        # the Auto-tune result is from another build or backend: how, and the new tune (A72)

    def label(self) -> str:
        return f"{self.slots} × {window_label(self.ctx)} {self.kv}"

    def describe(self) -> str:
        """The setup in words (the router's start lines): 2 slots × 96K tokens, q4, window cache."""
        kv = {"q4_0": "q4", "q8_0": "q8"}.get(self.kv, self.kv) + (f" ({self.kv_note})" if self.kv_note else "")
        cache = ", full cache" if self.swa else ", window cache" if self.window else ""
        on = f", on {self.devices.replace(',', ' and ')}" if self.devices else ""
        ram = f", the experts of {self.cpu_moe} layers in the RAM" if self.cpu_moe else ""
        load = ", load mode none" if self.load == "none" else ""
        ub = f", {self.ub_note}" if self.ub_note else ""
        mtp = ", MTP off while experts are in the RAM" if self.mtp_off else ""
        pin = f", {self.threads} threads, {PINNED_WORDS}" if self.threads else ""
        stale = f" (its Auto-tune result is from another build, so an estimate now: {self.stale_note})" \
            if self.stale_note else ""
        return (f"{self.slots} slot{'s' if self.slots != 1 else ''} × {window_label(self.ctx)} tokens, "
                f"{kv}{cache}{on}{ram}{pin}{load}{mtp}{ub}{stale}")


@dataclass
class Preset:
    """The models the router offers, the ones left out (name, why) and the one it loads at start."""
    models: List[ModelPlan] = field(default_factory=list)
    skipped: List[Tuple[str, str]] = field(default_factory=list)
    start: Optional[str] = None


SAMPLING_KEYS = (("temp", "temp"), ("top_p", "top-p"), ("top_k", "top-k"), ("min_p", "min-p"),
                 ("presence", "presence-penalty"), ("repeat", "repeat-penalty"))


def plan_model(name: str, path: str, vals: Settings, shape: ModelShape, weights: int, limit: float, ram: int,
               reserve: float, common: Common, template: Optional[str],
               draft: Optional[str] = None, draft_bytes: int = 0,
               devices: Sequence[Slot] = (), cpu_moe: Optional[int] = None,
               ub: int = 0, spec_kept: bool = False) -> Tuple[Optional[ModelPlan], str]:
    """(the model's section, "") or (None, why it is left out): its effective settings, slots
    auto = 2 when two windows fit, MTP dropped (n-gram kept) before a slot when it does not fit, the
    same memory check a start makes. weights: the model file; draft: the MTP drafter file its
    speculation loads, draft_bytes its size. devices: the GPUs of a PC (domain/memory/split.py): the model's devices
    and, over several, its split (several GPUs: the start fits when the split does); none on a Mac. A MoE model that
    does not fit them whole keeps the experts of N layers in the RAM (offload.py); cpu_moe: llama.n_cpu_moe (None:
    auto, 0: never). ub: the model's -ub (0: common's); the caller lowers it for the setup (fit.ub_choice). spec_kept:
    the speculation is a person's or Auto-tune's choice (a model with experts in the RAM keeps its MTP then, A51)."""
    ctx, kv, u = int(str(vals["ctx"])), str(vals["kv"]), ub or common.ubatch
    limit = one_limit(devices, limit)               # one device: its own room, not every GPU's added up
    kind, n = str(vals["spec"]), int(str(vals["spec_n"]))
    spec = Spec(kind, n, draft_bytes, spec_kept) if draft else mtp_spec(kind, n, shape, 0, spec_kept)
    many, calib = len(devices) > 1, calib_of(devices)
    sp = start_plan(common.swa_mode, shape, weights, ctx, str(vals["slots"]), kv, limit, spec, u,
                    ok_for(shape, weights, ctx, kv, u, devices, calib) if many else None, calib)
    slots, swa_full = sp.slots, sp.swa_full
    full = swa_full is not False
    if cpu_moe != 0 and offload.offloads(devices, shape) and (
            cpu_moe is not None or not _whole(shape, weights, ctx, slots, kv, full, sp.spec, u, limit,
                                              devices, calib)):
        return _offloaded(name, path, vals, shape, weights, ctx, kv, spec, ram, reserve, common, template, draft,
                          devices, cpu_moe, ub)
    if many:
        split = plan_split(devices, load_of(shape, weights, ctx, slots, kv, full, sp.spec, u, calib))
        if not split.fits:
            largest = split_max_ctx(shape, weights, devices, slots, kv, full, sp.spec, u, calib)
            return None, (f"It needs {split.need / 2**30:.1f} GiB for {slots} slot{'s' if slots != 1 else ''} × "
                          f"{window_label(ctx)} tokens, and the {len(devices)} GPUs cannot hold it as a split "
                          f"({split.room / 2**30:.1f} GiB together, each with its own compute buffer). "
                          + (f"The largest context that fits is {window_label(largest)} tokens per slot."
                             if largest else "The weights alone do not fit."))
        names, tsplit, need = split.devices(), split.tensor_split(), split.need
    else:
        chk = check_start(shape, weights, ctx, slots, kv, limit, full, sp.spec, u, calib)
        if not chk.fits:
            largest = (f"The largest context that fits is {window_label(chk.largest)} tokens per slot."
                       if chk.largest else "The weights alone do not fit.")
            return None, (f"It needs {chk.need / 2**30:.1f} GiB for {slots} slot{'s' if slots != 1 else ''} × "
                          f"{window_label(ctx)} tokens, more than the GPU memory limit "
                          f"({limit / 2**30:.1f} GiB). {largest}")
        names, tsplit, need = (devices[0].name if devices else ""), "", chk.need
    for ch in "\r\n":
        if ch in path or (template and ch in template) or (draft and ch in draft):
            raise ConfigError(f"{name}: a file path with a line break can't go into the presets file")
    cache = common.cache_ram if common.cache_ram is not None else prompt_cache_mib(ram, need, reserve)
    sampling = tuple((key, float(str(vals[k]))) for k, key in SAMPLING_KEYS)
    return ModelPlan(name, path, ctx, slots, kv, sp.spec.kind, int(str(vals["spec_n"])), sampling, cache,
                     template, need, bool(swa_full), draft if sp.spec.mtp else None, swa_full is False,
                     names, tsplit, ub=ub), ""


def _whole(shape: ModelShape, weights: float, ctx: int, slots: int, kv: str, full: bool, spec: Spec, ub: int,
           limit: float, devices: Sequence[Slot], calib: Calibration) -> bool:
    """Does the start fit the devices whole (every expert on the GPUs)?"""
    if len(devices) > 1:
        return plan_split(devices, load_of(shape, weights, ctx, slots, kv, full, spec, ub, calib)).fits
    return check_start(shape, weights, ctx, slots, kv, limit, full, spec, ub, calib).fits


def _offloaded(name: str, path: str, vals: Settings, shape: ModelShape, weights: int, ctx: int, kv: str, spec: Spec,
               ram: int, reserve: float, common: Common, template: Optional[str], draft: Optional[str],
               devices: Sequence[Slot], cpu_moe: Optional[int], ub: int = 0) -> Tuple[Optional[ModelPlan], str]:
    """plan_model for a MoE model with the experts of N layers in the RAM (W6.7): Auto fit's order of slots and
    speculation with that test, the smallest N (or the setting's), and the RAM cache that the RAM leaves after them."""
    calib, u = calib_of(devices), ub or common.ubatch
    lean = offload.ram_spec(spec)                   # any expert layer in the RAM: MTP off unless kept (A51)
    ok = offload.ok_for(shape, weights, ctx, kv, u, devices, ram, reserve, calib, cpu_moe)
    sp = start_plan(common.swa_mode, shape, weights, ctx, str(vals["slots"]), kv, 0.0, lean, u, ok, calib)
    off = offload.plan_of(shape, weights, ctx, sp.slots, kv, sp.swa_full is not False, sp.spec, u, devices,
                          ram, reserve, calib, cpu_moe)
    setup = f"{sp.slots} slot{'s' if sp.slots != 1 else ''} × {window_label(ctx)} tokens"
    if off is None or not off.gpu_fits:
        need = off.split.need if off else 0.0
        return None, (f"It needs {need / 2**30:.1f} GiB on the GPUs for {setup} even with the experts of every layer "
                      f"in the RAM, and the GPUs cannot hold that.")
    if not off.ram_fits:
        return None, (f"With {off.words()}, the RAM side needs {(off.ram + offload.CACHE_MIN) / 2**30:.1f} GiB for "
                      f"{setup} (with the smallest RAM cache), and the RAM has {off.room / 2**30:.1f} GiB after the "
                      f"part kept free.")
    for ch in "\r\n":
        if ch in path or (template and ch in template) or (draft and ch in draft):
            raise ConfigError(f"{name}: a file path with a line break can't go into the presets file")
    cache = common.cache_ram if common.cache_ram is not None else prompt_cache_mib(ram, off.ram, reserve)
    sampling = tuple((key, float(str(vals[k]))) for k, key in SAMPLING_KEYS)
    return ModelPlan(name, path, ctx, sp.slots, kv, sp.spec.kind, int(str(vals["spec_n"])), sampling, cache,
                     template, off.split.need, bool(sp.swa_full), draft if sp.spec.mtp else None, sp.swa_full is False,
                     off.split.devices(), off.split.tensor_split(), off.n, ub, off.ram,
                     mtp_off=spec.mtp and not lean.mtp), ""


def _num(v: float) -> str:
    return f"{v:g}"


def writable(value: str) -> bool:
    """A path the presets file can hold. llama.cpp's parser takes a value as it stands to the end of its line:
    backslashes, spaces and letters outside ASCII load (measured on Windows, W6.9, so a Windows path goes in as it
    is), but a ';' or '#' starts a comment (the path is cut there), a quote is part of the value, and spaces around it
    are trimmed."""
    return not (";" in value or "#" in value or value != value.strip() or value[:1] in ("'", '"'))


def _same(path: str) -> str:
    return path


def ini_safe(preset: Preset, common: Common, to_ini: Callable[[str], str] = _same) -> Tuple[Preset, Common]:
    """The presets with every path the file can hold. to_ini gives another name for a path it cannot (Windows: the
    8.3 short form of a path with a ';' or '#'); a model with a path that still cannot go in is left out with the
    reason, the model loaded first moves to the next one, and a slot folder that cannot is not passed (no disk
    cache for the router)."""
    kept: List[ModelPlan] = []
    skipped = list(preset.skipped)
    for m in preset.models:
        paths = {"model": m.path, "chat template": m.template or "", "drafter": m.draft or ""}
        fixed = {k: (to_ini(v) if v and not writable(v) else v) for k, v in paths.items()}
        bad = next((k for k, v in fixed.items() if v and not writable(v)), "")
        if bad:
            skipped.append((m.name, f"the path of its {bad} has a ';' or '#', which the presets file cannot hold"))
            continue
        kept.append(replace(m, path=fixed["model"], template=fixed["chat template"] or None,
                            draft=fixed["drafter"] or None))
    start = preset.start if preset.start in [m.name for m in kept] else (kept[0].name if kept else None)
    slot = common.slot_dir
    slot = to_ini(slot) if slot and not writable(slot) else slot
    return Preset(kept, skipped, start), replace(common, slot_dir=slot if writable(slot) else "")


def preset_ini(preset: Preset, common: Common, words: PlatformWords = MACOS) -> str:
    """The presets INI text (a path the file cannot hold is never written: ini_safe leaves the model out)."""
    preset, common = ini_safe(preset, common)
    out = [header(words), "version = 1", "", "[*]"]
    shared: Dict[str, str] = {
        "jinja": "true", "reasoning-format": "deepseek", "chat-template-kwargs": '{"preserve_thinking":true}',
        # fit off: CARL sizes each model itself (plan_model); llama.cpp's own memory fitting only probes,
        # and its probe logs a false error for Gemma 4's MTP drafter
        "n-gpu-layers": "999", "flash-attn": "on", "fit": "off", "no-mmproj": "true", "metrics": "true",
        "batch-size": str(common.batch), "ubatch-size": str(common.ubatch),
        "ctx-checkpoints": str(common.ckpt), "checkpoint-min-step": str(common.ckpt_step),
        **({"slot-save-path": common.slot_dir} if common.slot_dir and writable(common.slot_dir)
           and "\n" not in common.slot_dir else {})}
    out += [f"{k} = {v}" for k, v in shared.items()]
    for m in preset.models:
        out += ["", f"; {m.label()}, needs {m.need / 2**30:.1f} GiB", f"[{m.name}]", f"model = {m.path}"]
        if m.devices:                                    # a PC: the devices, and over several the planned split
            out.append(f"device = {m.devices}")
            if m.tensor_split:
                out += [f"split-mode = {SPLIT_LAYER}", f"tensor-split = {m.tensor_split}"]
        if m.cpu_moe:                                    # the MoE experts of the first N layers stay in the RAM
            out.append(f"n-cpu-moe = {m.cpu_moe}")
        if m.threads and m.cpu_mask:                     # one thread per physical core, pinned (A54)
            out += [f"threads = {m.threads}", f"cpu-mask = {m.cpu_mask}"]
        if m.ub and m.ub != common.ubatch:               # the model's own -ub (llama.cpp: -b at least -ub)
            out += [f"ubatch-size = {m.ub}"] + ([f"batch-size = {m.ub}"] if m.ub > common.batch else [])
        if m.load == "none":                             # the weights read into the RAM, not mapped (A44, A45)
            out.append("load-mode = none")
        out += [f"ctx-size = {m.ctx * m.slots}", f"parallel = {m.slots}"]
        if m.slots > 1:
            out += ["kv-unified = true", f"kv-unified-per-slot = {m.ctx}", "cache-idle-slots = false",
                    f"slot-prompt-similarity = {_num(SLOT_SIMILARITY)}"]
        out += [f"cache-type-k = {m.kv}", f"cache-type-v = {m.kv}", f"cache-ram = {m.cache_mib}"]
        out += [f"{k} = {_num(v)}" for k, v in m.sampling]
        if m.spec != "none":
            out.append(f"spec-type = {m.spec}")
            if "draft-mtp" in m.spec:                   # MTP's guesses; n-gram does not read it (Phase 23.5.1.8)
                out.append(f"spec-draft-n-max = {m.spec_n}")
            if "ngram-mod" in m.spec:                   # n-gram's own settings
                out += [f"spec-ngram-mod-n-{k} = {v}" for k, v in NGRAM.items()]
            if m.draft and "draft-mtp" in m.spec:
                out.append(f"spec-draft-model = {m.draft}")
        if m.template:
            out.append(f"chat-template-file = {m.template}")
        if m.swa:
            out.append("swa-full = true")
        if m.name == preset.start:
            out.append("load-on-startup = true")
    if preset.skipped:
        out += ["", f"; left out (they don't fit {words.machine} with their settings):"]
        out += [f";   {n}: {why}" for n, why in preset.skipped]
    return "\n".join(out) + "\n"


def preset_answer(preset: Preset) -> str:
    """The presets in lines, as the start text reads them: "model NAME TEXT" for each model in the file, "skip NAME:
    WHY" for one left out, "start NAME" for the one loaded first (what the router-preset command prints)."""
    lines = [f"model {p.name} {p.describe()}" for p in preset.models]
    lines += [f"skip {n}: {why}" for n, why in preset.skipped]
    if preset.start:
        lines.append(f"start {preset.start}")
    return "\n".join(lines)
