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
and sets host, port, the API key and the alias (= the section name) itself. Pure.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .errors import ConfigError
from .fit import check_start, need_bytes, plan_slots, prompt_cache_mib, window_label
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
    slot_dir: str = ""          # --slot-save-path: the saved prompt prefixes (tools/monitor/prefix.py)


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

    def label(self) -> str:
        return f"{self.slots} × {window_label(self.ctx)} {self.kv}"


@dataclass
class Preset:
    """The models the router offers, the ones left out (name, why) and the one it loads at start."""
    models: List[ModelPlan] = field(default_factory=list)
    skipped: List[Tuple[str, str]] = field(default_factory=list)
    start: Optional[str] = None


SAMPLING_KEYS = (("temp", "temp"), ("top_p", "top-p"), ("top_k", "top-k"), ("min_p", "min-p"),
                 ("presence", "presence-penalty"), ("repeat", "repeat-penalty"))


def plan_model(name: str, path: str, vals: Settings, shape: ModelShape, weights: int, limit: float, ram: int,
               reserve: float, common: Common, template: Optional[str]) -> Tuple[Optional[ModelPlan], str]:
    """(the model's section, "") or (None, why it is left out): its effective settings, slots
    auto = 2 when two windows fit, the same memory check a start makes."""
    ctx, kv = int(str(vals["ctx"])), str(vals["kv"])
    slots = plan_slots(str(vals["slots"]), need_bytes(shape, weights, ctx, 2, kv) <= limit)
    chk = check_start(shape, weights, ctx, slots, kv, limit)
    if not chk.fits:
        largest = f"largest window {window_label(chk.largest)}" if chk.largest else "the weights alone don't fit"
        return None, f"needs {chk.need / 2**30:.1f} GiB for {slots} × {window_label(ctx)} {kv} ({largest})"
    for ch in "\r\n":
        if ch in path or (template and ch in template):
            raise ConfigError(f"{name}: a file path with a line break can't go into the presets file")
    cache = common.cache_ram if common.cache_ram is not None else prompt_cache_mib(ram, chk.need, reserve)
    sampling = tuple((key, float(str(vals[k]))) for k, key in SAMPLING_KEYS)
    return ModelPlan(name, path, ctx, slots, kv, str(vals["spec"]), int(str(vals["spec_n"])), sampling, cache,
                     template, chk.need), ""


def _num(v: float) -> str:
    return f"{v:g}"


def preset_ini(preset: Preset, common: Common) -> str:
    """The presets INI text."""
    out = [HEADER, "version = 1", "", "[*]"]
    shared: Dict[str, str] = {
        "jinja": "true", "reasoning-format": "deepseek", "chat-template-kwargs": '{"preserve_thinking":true}',
        "n-gpu-layers": "999", "flash-attn": "on", "no-mmproj": "true", "metrics": "true",
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
        if m.template:
            out.append(f"chat-template-file = {m.template}")
        if m.name == preset.start:
            out.append("load-on-startup = true")
    if preset.skipped:
        out += ["", "; left out (they don't fit this Mac with their settings):"]
        out += [f";   {n}: {why}" for n, why in preset.skipped]
    return "\n".join(out) + "\n"
