"""The memory a model may use on discrete GPUs (W6.1): the GPU settings, the room on each GPU, and their sum. Pure.

The memory model is unified (a Mac, or a PC with no GPU: domain/memory/autofit.py Budget) or discrete devices + RAM:
each GPU used gives
  room = its free VRAM + what CARL's own server holds on it - gpu.vram_keep (at most its total)
and a model may use the rooms added up (llama.cpp splits a model over every GPU by layers; domain/memory/split.py
plans the split itself, with the compute buffer on each device: a start fits when the split fits, which the rooms
added up only bound). The RAM does not cap it.

A GPU that shares the RAM (an APU; WAPU, decision A37) has its own rule: its room is
  room = its carve-out (Gpu.total) + the system memory the driver maps (Gpu.shared: the driver's GTT, Windows' shared
         system memory; half the RAM when the driver did not say)
or the setting gpu.shared_limit_gib when it is set. The part kept free on a discrete GPU does not apply (the RAM less the reserve is
the desktop's room), and CARL's own server is not added back (the totals count, not the free memory). The part of the
room that is the RAM's (Room.shared) is capped by the RAM less the reserve where the reserve is known: Budget
(domain/memory/autofit.py), so a model may use the smaller of the shared part and the RAM less the reserve, and the
carve-out on top. An APU beside a discrete GPU counts both, each by its own rule.

The settings (gpu.vram_keep, gpu.backend, gpu.devices, gpu.shared_limit_gib; domain/config/settings.py): the
environment wins over config.json, which wins over the default. A value that is not valid is left out with a note,
so a bad setting never stops a command (`carl config show` and `carl gpu` say so).

`carl fit --vram GIB[,GIB...]` previews other GPUs: each card's whole memory is free, less the keep.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Mapping, Optional, Sequence, Tuple

from ..config.settings import GPU_KEYS, SettingSpec, coerce
from ..hardware.devices import GIB, MIB, Gpu, shares_ram, vram_limit
from ..shared.errors import ConfigError
from ..shared.types import SettingValue
from .split import Slot


@dataclass(frozen=True)
class GpuSettings:
    """The GPU settings in force: keep (bytes kept free on each GPU), backend (auto, cuda, cpu), devices
    (CARL's numbers of the GPUs to use; empty: every GPU), notes (the values left out, in words) and shared_limit (bytes
    a model may use on a GPU that shares the RAM; 0: worked out from the driver's figures)."""
    keep: int = GIB
    backend: str = "auto"
    devices: Tuple[int, ...] = ()
    notes: Tuple[str, ...] = field(default=(), compare=False)
    shared_limit: int = 0


def _value(key: str, spec: SettingSpec, env: Mapping[str, str], section: Mapping[str, object],
           notes: List[str]) -> SettingValue:
    """One setting: its variable, else config.json's gpu.KEY, else the default; a bad value gives a note."""
    for where, raw in ((spec.env or "", env.get(spec.env or "")), (f"gpu.{key}", section.get(key))):
        if raw is None or raw == "" or not where:
            continue
        try:
            return coerce(spec, raw, where)
        except ConfigError as e:
            notes.append(f"{e} (CARL uses the next value)")
    return spec.default


def gpu_settings(env: Mapping[str, str], section: Optional[Mapping[str, object]] = None) -> GpuSettings:
    """The GPU settings from the environment (CARL_VRAM_KEEP_GIB, CARL_GPU_BACKEND, CARL_GPU_DEVICES,
    CARL_SHARED_LIMIT_GIB) and config.json's gpu section (section: the raw object; anything but an object is no
    section)."""
    sec: Mapping[str, object] = section if isinstance(section, dict) else {}
    notes: List[str] = []
    vals = {k: _value(k, s, env, sec, notes) for k, s in GPU_KEYS.items()}
    keep, devs, shared = vals["vram_keep"], vals["devices"], vals["shared_limit_gib"]
    return GpuSettings(int(float(keep) * GIB) if isinstance(keep, (int, float)) else GIB, str(vals["backend"]),
                       tuple(int(d) for d in devs) if isinstance(devs, list) else (), tuple(notes),
                       int(float(shared) * GIB) if isinstance(shared, (int, float)) else 0)


@dataclass(frozen=True)
class Room:
    """One GPU a model uses: the GPU, what CARL's own server holds there (added back) and the room left for a
    model (vram_limit). shared: the part of the room that is the RAM's (a GPU that shares the RAM; 0 on a discrete
    GPU), which Budget caps by the RAM less the reserve."""
    gpu: Gpu
    own: int
    limit: int
    shared: int = 0


def shared_room(gpu: Gpu, override: int = 0, ram: int = 0) -> Room:
    """The room on a GPU that shares the RAM: the setting gpu.shared_limit_gib (override, bytes) when it is set, else
    its carve-out and the system memory the driver maps (half the RAM when the driver did not say). The carve-out is
    the GPU's own; the rest is the RAM's."""
    limit = override if override > 0 else gpu.total + (gpu.shared or ram // 2)
    return Room(gpu, 0, limit, max(limit - gpu.total, 0))


def rooms(gpus: Sequence[Gpu], keep: int, own: Optional[Mapping[int, int]] = None, shared_limit: int = 0,
          ram: int = 0) -> List[Room]:
    """The room on each GPU. own: what CARL's own server holds, by the GPU's index (CARL's number); shared_limit and
    ram: for a GPU that shares the RAM (gpu.shared_limit_gib; the installed RAM)."""
    own = own or {}
    return [shared_room(g, shared_limit, ram) if shares_ram(g) else
            Room(g, own.get(g.index, 0), vram_limit(g, keep, own.get(g.index, 0))) for g in gpus]


def total(found: Sequence[Room]) -> int:
    """What a model may use over every GPU."""
    return sum(r.limit for r in found)


def dedicated(found: Sequence[Room]) -> int:
    """The part of total(found) that is the GPUs' own memory (VRAM, an APU's carve-out): the RAM does not cap it."""
    return sum(r.limit - r.shared for r in found)


def cap(limit: float, dedicated: float, ram: float, reserve: float) -> float:
    """What a model may use when a GPU shares the RAM: the GPUs' own memory (dedicated: VRAM, an APU's carve-out) and,
    of the rest of limit (the shared part, the RAM's), the smaller of it and the RAM less the reserve."""
    return dedicated + max(0.0, min(limit - dedicated, ram - reserve))


def size_text(n: int) -> str:
    """12.0 GiB, or 512 MiB below 1 GiB."""
    return f"{n / GIB:.1f} GiB" if n >= GIB else f"{n // MIB} MiB"


def keep_text(keep: int) -> str:
    """1.00 GiB"""
    return f"{keep / GIB:.2f} GiB"


def how(found: Sequence[Room], keep: int, source: str, shared_limit: int = 0) -> str:
    """How the limit was found, in words (carl fit's memory line). shared_limit: gpu.shared_limit_gib in bytes."""
    if any(shares_ram(r.gpu) for r in found):
        return how_shared(found, keep, source, shared_limit)
    if len(found) == 1:
        g = found[0]
        back = " and what CARL's server holds there" if g.own else ""
        return f"the free GPU memory of {g.gpu.name}{back} ({source}) less {keep_text(keep)}"
    back = ", what CARL's server holds there" if any(r.own for r in found) else ""
    return f"the free memory of {len(found)} GPUs{back} ({source}), each less {keep_text(keep)}"


def how_shared(found: Sequence[Room], keep: int, source: str, shared_limit: int = 0) -> str:
    """How the limit was found when a GPU shares the RAM: its carve-out and the RAM it may map (or your setting); a
    discrete GPU beside it by its own rule."""
    apus = [r for r in found if shares_ram(r.gpu)]
    names = " and ".join(r.gpu.name for r in apus)
    if shared_limit > 0:
        part = f"your setting gpu.shared_limit_gib, {size_text(shared_limit)}, for {names}, which shares the RAM"
    else:
        r = apus[0]
        part = (f"{names} shares the RAM: {size_text(r.gpu.total)} of its own and up to {size_text(r.shared)} of the "
                f"RAM (the memory the driver maps)")
    if len(apus) == len(found):
        return f"{part} ({source})"
    others = len(found) - len(apus)
    word = "the free GPU memory of the discrete GPU" if others == 1 else f"the free memory of {others} discrete GPUs"
    return f"{part}, and {word} less {keep_text(keep)} ({source})"


# ---------------------------------------------------------------- fit --vram
def parse_vram(text: str) -> List[float]:
    """--vram GIB[,GIB...]: the memory of each GPU to preview, in GiB (ConfigError when it is not that)."""
    try:
        sizes = [float(x) for x in text.split(",")]
    except ValueError:
        sizes = []
    if not sizes or any(not 0 < s <= 1024 for s in sizes):
        raise ConfigError(f"--vram: {text!r} is not a list of GPU sizes in GiB (for example 8 or 12,8)")
    return sizes


def preview(sizes: Sequence[float]) -> List[Gpu]:
    """The GPUs of a preview: each one's whole memory is free."""
    return [Gpu(f"a {s:g} GiB GPU", int(s * GIB), int(s * GIB), index=i) for i, s in enumerate(sizes)]


def preview_slots(sizes: Sequence[float], keep: int) -> List[Slot]:
    """The devices of a preview, for the split (domain/memory/split.py): NVIDIA cards, CUDA0, CUDA1, ... in order."""
    return [Slot(f"CUDA{r.gpu.index}", r.gpu, r.limit) for r in rooms(preview(sizes), keep)]


def preview_limit(sizes: Sequence[float], keep: int) -> Tuple[int, str]:
    """(limit, how) of a preview of GPUs with these sizes, each less keep."""
    found = rooms(preview(sizes), keep)
    cards = " and ".join(f"{s:g}" for s in sizes)
    word = "a GPU" if len(sizes) == 1 else f"{len(sizes)} GPUs"
    return total(found), f"a preview of {word} with {cards} GiB, each less {keep_text(keep)} kept free"
