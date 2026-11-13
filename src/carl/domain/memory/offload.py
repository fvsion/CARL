"""MoE experts in the RAM (--n-cpu-moe), over the split (W6.7, plan 5.6). Pure.

llama.cpp's --n-cpu-moe N keeps the MoE experts of the blocks 0 to N-1 in the RAM (the tensors of gguf.py EXPERTS_RE:
not the shared expert, not the router). With -ngl 999 the rest of every layer stays on the GPUs. The experts are most
of a MoE model's weights and only a few of them work on each token, so a MoE model that does not fit the GPUs whole
can still run on them, a little slower. The planner finds the smallest N such that

  the GPU side  the start's load (split.py load_of) less the experts of the blocks below N, split over every device
                by split.plan_split, each device within its room (its free memory less the part kept free), and
  the RAM side  the experts of the blocks below N, token_embd and Gemma's per-layer embeddings where the backend
                keeps them in the RAM (calibration.py), and one compute buffer for the CPU's share of the work, with the smallest RAM
                prompt cache (fit.py CACHE_MIN_MIB) still free

fits the RAM less the reserve, with PLAN_SLACK of each device's room to spare (the free VRAM moves a little before the
check, which judges the full room). The smallest N keeps the most experts on the GPUs, which is the fastest start. With one
GPU this is the one-device case. A dense model (no experts) is never offloaded, and neither is a GPU that shares the
RAM (the experts would only move within the same memory). A setting (llama.n_cpu_moe) gives N; it is checked the same
way. The RAM prompt cache of an offloaded start is what the RAM leaves after the RAM side (fit.py prompt_cache_mib).

The RAM side's compute buffer is PROVISIONAL: one compute buffer of the GPU's size, until W6.10's measurements.
Contracts C-104 (the plan), C-119 (the argv).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, List, Optional, Sequence

from . import loaded
from .calibration import METAL, Calibration
from .fit import CACHE_MIN_MIB, MIB, NO_SPEC, UBATCH, WINDOW_STEP, Spec
from .gguf import GIB, ModelShape, ctx_train
from .split import Load, Slot, Split, load_of, plan_split

CACHE_MIN = CACHE_MIN_MIB * MIB          # the smallest RAM prompt cache: the RAM side leaves at least this free
# The plan's N leaves this much of each device's room free: the free VRAM moves between the plan and the start's check
# (the desktop's own use of the GPU; measured on the Windows test PC, 2026-10-27: 40 MiB in seconds), and a GPU side planned to the
# last MiB would be refused by the check. 128 MiB is three times the drift measured (decision A43; it was 256 MiB).
PLAN_SLACK = 128 * MIB


def ram_spec(spec: Spec) -> Spec:
    """The speculation of a start with any expert layer in the RAM (decision A51): n-gram, MTP off. MTP did not pay on
    a card that keeps experts in the RAM (WCAL round 2, mtp.md: the same write speed within the noise, prompts 5% to 6%
    slower, a draft context of the card's memory). A speculation a person or Auto-tune chose (Spec.kept) stays; Auto-tune
    measures MTP against n-gram at the setup it chooses (application/tune.py) and keeps MTP only if it wins."""
    return spec if spec.kept or not spec.mtp else spec.without_mtp()


def experts_of(shape: ModelShape) -> List[float]:
    """The bytes of the MoE experts of each block, by blk index ([] for a dense model or a header without them)."""
    return [float(e) for e in (shape.get("expert_bytes") or [])]


def is_moe(shape: ModelShape) -> bool:
    """Does the model have experts that --n-cpu-moe can keep in the RAM?"""
    return any(e > 0 for e in experts_of(shape))


def offloads(slots: Sequence[Slot], shape: ModelShape) -> bool:
    """Can a start of this model keep experts in the RAM on these devices? A MoE model on discrete GPUs only: not on a
    Mac or the CPU (no devices), not with a GPU that shares the RAM."""
    return bool(slots) and all(not s.shared for s in slots) and is_moe(shape)


@dataclass(frozen=True)
class Offload:
    """A start with the experts of the blocks below n in the RAM. layers: the blocks with experts; split: the GPU side
    over the devices; experts: the bytes of the experts in the RAM; ram: the whole RAM side (the experts, token_embd
    where the backend keeps it there, the CPU's compute buffer); room: the RAM less the reserve (0: not known)."""
    n: int
    layers: int
    split: Split
    experts: float
    ram: float
    room: float

    @property
    def gpu_fits(self) -> bool:
        return self.split.fits

    @property
    def ram_fits(self) -> bool:
        """The RAM side and the smallest RAM prompt cache fit the RAM less the reserve (an unknown RAM: yes)."""
        return self.room <= 0 or self.ram + CACHE_MIN <= self.room

    @property
    def fits(self) -> bool:
        return self.gpu_fits and self.ram_fits

    def words(self) -> str:
        """the experts of 12 of the 41 layers in the RAM"""
        return f"the experts of {self.n} of the {self.layers} layers in the RAM"


def gpu_load(load: Load, experts: Sequence[float], n: int) -> Load:
    """The load on the GPUs with the experts of the blocks below n in the RAM: their weights are off the GPU, and the
    compute buffer holds the staged copy of the largest of them (calibration.stage_of)."""
    costs = [c - (experts[i] if i < n and i < len(experts) else 0.0) for i, c in enumerate(load.costs)]
    return replace(load, costs=tuple(costs), compute=load.compute_at(n))


def ram_side(shape: ModelShape, load: Load, n: int, calib: Calibration = METAL) -> float:
    """The RAM a start needs with the experts of the blocks below n there (module docstring)."""
    if n <= 0:
        return 0.0
    embd = float(shape.get("embd_bytes", 0) or 0) if calib.gpu_weights else 0.0
    return sum(experts_of(shape)[:n]) + embd + calib.ram_bytes(shape) + load.compute


def at(slots: Sequence[Slot], load: Load, shape: ModelShape, n: int, ram: float, reserve: float,
       calib: Calibration = METAL) -> Offload:
    """The start with the experts of the blocks below n in the RAM (n within 0 and the blocks with experts)."""
    experts = experts_of(shape)
    n = max(0, min(n, len(experts)))
    room = max(ram - reserve, 0.0) if ram > 0 else 0.0
    return Offload(n, len(experts), plan_split(slots, gpu_load(load, experts, n)), sum(experts[:n]),
                   ram_side(shape, load, n, calib), room)


def plan_offload(slots: Sequence[Slot], load: Load, shape: ModelShape, ram: float, reserve: float,
                 calib: Calibration = METAL, n: Optional[int] = None) -> Optional[Offload]:
    """The smallest N whose GPU side fits the devices (n: the setting's N instead), with its RAM side; None when the
    start cannot keep experts in the RAM (offloads). When even every block's experts in the RAM leave the GPU side too
    big, the answer is that one, which does not fit."""
    if not offloads(slots, shape):
        return None
    if n is not None:
        return at(slots, load, shape, n, ram, reserve, calib)
    layers = len(experts_of(shape))
    lo, hi = 1, layers                       # the GPU side shrinks as N grows: the smallest N that fits it
    if not at(slots, load, shape, hi, ram, reserve, calib).gpu_fits:
        return at(slots, load, shape, hi, ram, reserve, calib)
    spare = [replace(s, room=max(s.room - PLAN_SLACK, 0)) for s in slots]
    if not at(spare, load, shape, hi, ram, reserve, calib).gpu_fits:
        return at(slots, load, shape, hi, ram, reserve, calib)
    while lo < hi:
        mid = (lo + hi) // 2
        if at(spare, load, shape, mid, ram, reserve, calib).gpu_fits:
            hi = mid
        else:
            lo = mid + 1
    return at(slots, load, shape, lo, ram, reserve, calib)


def plan_of(shape: ModelShape, weights: float, ctx: int, nslots: int, kv: str, swa_full: bool, spec: Spec, ub: int,
            slots: Sequence[Slot], ram: float, reserve: float, calib: Calibration = METAL,
            n: Optional[int] = None) -> Optional[Offload]:
    """plan_offload for a start: the load from the header shape and the parts of the one-device need. Without MTP the
    experts of the MTP head's blocks are not there to move (loaded.py, A52): N counts the blocks that load."""
    if not offloads(slots, shape):
        return None
    return plan_offload(slots, load_of(shape, weights, ctx, nslots, kv, swa_full, spec, ub, calib),
                        loaded.trimmed(shape, spec.mtp, calib), ram, reserve, calib, n)


def max_ctx(shape: ModelShape, weights: float, slots: Sequence[Slot], ram: float, reserve: float, nslots: int = 1,
            kv: str = "q4_0", swa_full: bool = True, spec: Spec = NO_SPEC, ub: int = UBATCH,
            calib: Calibration = METAL) -> int:
    """The largest window per slot (a 4K multiple, at most the trained length) that fits with the experts of some
    blocks in the RAM: the GPU side fits the devices and the RAM side fits the RAM less the reserve (plan_of, the
    smallest N for each window). 0 when not even 4K does, and for a start that cannot keep experts in the RAM
    (offloads). The table of `carl fit` shows it for a MoE model on discrete GPUs (decision A48)."""
    if not offloads(slots, shape):
        return 0

    def ok(c: int) -> bool:
        off = plan_of(shape, weights, c, nslots, kv, swa_full, spec, ub, slots, ram, reserve, calib)
        return off is not None and off.fits

    top = ctx_train(shape)
    if ok(top):
        return top
    lo, hi = 0, top // WINDOW_STEP
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if ok(mid * WINDOW_STEP):
            lo = mid
        else:
            hi = mid - 1
    return lo * WINDOW_STEP


def ok_for(shape: ModelShape, weights: float, ctx: int, kv: str, ub: int, slots: Sequence[Slot], ram: float,
           reserve: float, calib: Calibration = METAL, n: Optional[int] = None) -> Callable[[int, bool, Spec], bool]:
    """The test fit.start_plan takes for a start with experts in the RAM: does a start with this many slots, the
    sliding-window cache full or not, and this speculation fit with some N (or the setting's n)?"""
    def ok(nslots: int, swa_full: bool, spec: Spec) -> bool:
        off = plan_of(shape, weights, ctx, nslots, kv, swa_full, spec, ub, slots, ram, reserve, calib, n)
        return off is not None and off.fits
    return ok


def ram_line(off: Offload, os_and_apps: str) -> str:
    """The plan's line about the RAM side: RAM: the experts of 12 of the 41 layers (3.3 GiB) and the CPU's compute
    buffer, 4.1 GiB in all, of the 120.0 GiB that the RAM has free for a model."""
    return (f"RAM: the experts of {off.n} of the {off.layers} layers ({off.experts / GIB:.1f} GiB) and the CPU's compute "
            f"buffer, {off.ram / GIB:.1f} GiB in all" + (
                f", of the {off.room / GIB:.1f} GiB that the RAM has free after the part kept for {os_and_apps}."
                if off.room > 0 else "."))

