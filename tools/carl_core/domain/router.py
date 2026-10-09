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
speculation comes from a separate drafter (Gemma 4) gets it as spec-draft-model (-md). Pure.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .errors import ConfigError
from .fit import Spec, check_start, mtp_spec, prompt_cache_mib, start_plan, window_label
from .gguf import ModelShape
from .types import Settings

HEADER = ("; CARL's llama.cpp router presets: written at every router start from config.json, Auto-tune and\n"
          "; the catalogue (./carl.sh config show). Edits here are lost: change the settings instead.\n")
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

    def label(self) -> str:
        return f"{self.slots} × {window_label(self.ctx)} {self.kv}"

    def describe(self) -> str:
        """The setup in words (the router's start lines): 2 slots × 96K tokens, q4, window cache."""
        kv = {"q4_0": "q4", "q8_0": "q8"}.get(self.kv, self.kv)
        cache = ", full cache" if self.swa else ", window cache" if self.window else ""
        return f"{self.slots} slot{'s' if self.slots != 1 else ''} × {window_label(self.ctx)} tokens, {kv}{cache}"


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
               draft: Optional[str] = None, draft_bytes: int = 0) -> Tuple[Optional[ModelPlan], str]:
    """(the model's section, "") or (None, why it is left out): its effective settings, slots
    auto = 2 when two windows fit, MTP dropped (n-gram kept) before a slot when it does not fit, the
    same memory check a start makes. weights: the model file; draft: the MTP drafter file its
    speculation loads, draft_bytes its size."""
    ctx, kv = int(str(vals["ctx"])), str(vals["kv"])
    kind, n = str(vals["spec"]), int(str(vals["spec_n"]))
    spec = Spec(kind, n, draft_bytes) if draft else mtp_spec(kind, n, shape)
    sp = start_plan(common.swa_mode, shape, weights, ctx, str(vals["slots"]), kv, limit, spec, common.ubatch)
    slots, swa_full = sp.slots, sp.swa_full
    chk = check_start(shape, weights, ctx, slots, kv, limit, swa_full is not False, sp.spec, common.ubatch)
    if not chk.fits:
        largest = (f"The largest context that fits is {window_label(chk.largest)} tokens per slot."
                   if chk.largest else "The weights alone do not fit.")
        return None, (f"It needs {chk.need / 2**30:.1f} GiB for {slots} slot{'s' if slots != 1 else ''} × "
                      f"{window_label(ctx)} tokens, more than the GPU memory limit "
                      f"({limit / 2**30:.1f} GiB). {largest}")
    for ch in "\r\n":
        if ch in path or (template and ch in template) or (draft and ch in draft):
            raise ConfigError(f"{name}: a file path with a line break can't go into the presets file")
    cache = common.cache_ram if common.cache_ram is not None else prompt_cache_mib(ram, chk.need, reserve)
    sampling = tuple((key, float(str(vals[k]))) for k, key in SAMPLING_KEYS)
    return ModelPlan(name, path, ctx, slots, kv, sp.spec.kind, int(str(vals["spec_n"])), sampling, cache,
                     template, chk.need, bool(swa_full), draft if sp.spec.mtp else None, swa_full is False), ""


def _num(v: float) -> str:
    return f"{v:g}"


def preset_ini(preset: Preset, common: Common) -> str:
    """The presets INI text."""
    out = [HEADER, "version = 1", "", "[*]"]
    shared: Dict[str, str] = {
        "jinja": "true", "reasoning-format": "deepseek", "chat-template-kwargs": '{"preserve_thinking":true}',
        # fit off: CARL sizes each model itself (plan_model); llama.cpp's own memory fitting only probes,
        # and its probe logs a false error for Gemma 4's MTP drafter
        "n-gpu-layers": "999", "flash-attn": "on", "fit": "off", "no-mmproj": "true", "metrics": "true",
        "batch-size": str(common.batch), "ubatch-size": str(common.ubatch),
        "ctx-checkpoints": str(common.ckpt), "checkpoint-min-step": str(common.ckpt_step),
        **({"slot-save-path": common.slot_dir} if common.slot_dir and "\n" not in common.slot_dir else {})}
    out += [f"{k} = {v}" for k, v in shared.items()]
    for m in preset.models:
        out += ["", f"; {m.label()}, needs {m.need / 2**30:.1f} GiB", f"[{m.name}]", f"model = {m.path}",
                f"ctx-size = {m.ctx * m.slots}", f"parallel = {m.slots}"]
        if m.slots > 1:
            out += ["kv-unified = true", f"kv-unified-per-slot = {m.ctx}", "cache-idle-slots = false",
                    f"slot-prompt-similarity = {_num(SLOT_SIMILARITY)}"]
        out += [f"cache-type-k = {m.kv}", f"cache-type-v = {m.kv}", f"cache-ram = {m.cache_mib}"]
        out += [f"{k} = {_num(v)}" for k, v in m.sampling]
        if m.spec != "none":
            out += [f"spec-type = {m.spec}", f"spec-draft-n-max = {m.spec_n}"]
            if m.draft and "draft-mtp" in m.spec:
                out.append(f"spec-draft-model = {m.draft}")
        if m.template:
            out.append(f"chat-template-file = {m.template}")
        if m.swa:
            out.append("swa-full = true")
        if m.name == preset.start:
            out.append("load-on-startup = true")
    if preset.skipped:
        out += ["", "; left out (they don't fit this Mac with their settings):"]
        out += [f";   {n}: {why}" for n, why in preset.skipped]
    return "\n".join(out) + "\n"
