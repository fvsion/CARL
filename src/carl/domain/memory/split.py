"""The split of a model over the GPUs of a PC (W6.6, plan 5.5). Pure.

llama.cpp's layer split (--split-mode layer) puts the layers of a model on the devices one after the other, in the
order of -dev, in the proportions of -ts. CARL plans that split itself and passes -dev, -ts and --split-mode layer
explicitly, because llama.cpp's own split reads the wrong free VRAM on Windows (reference: windows-gpu report).

What a start puts on the devices (Load, from the same parts as the one-device need, domain/memory/fit.py need_parts):
  each layer      its weights (the experts of a MoE layer exactly, the rest evenly) and its share of the context
                  memory and the recurrent state (evenly over the layers that are not the MTP head)
  the last entry  the output layer: output.weight, and with MTP the drafter file and the draft context
  head            token_embd, charged to the first device that has a layer (llama.cpp keeps it in the RAM; the
                  one-device need counts it, so the sum of the devices' needs stays the one-device need). On a
                  calibrated backend (CUDA; calibration.py) the GPU does not hold it: no head, and the
                  output layer is a copy of token_embd where the model ties the two
  compute         one compute buffer on every device that has a layer (the backend's model of it; with experts in the
                  RAM it follows how many layers they are: compute_by_n, A60)
  overhead        the backend's own context, once on every device that has a layer
and every device adds the 0.75% margin to its parts. For one device the need is the one-device need to the byte.

The planner (plan_split) gives each device layers in proportion to its room after its fixed costs (the compute buffer
and, on the first one, the head), at layer granularity: the layers are cut in order so that every device is filled to
the same share of its room (the smallest share that holds all of them, found by bisection). A device that would get no
layer is left out, with the reason. When even a full share does not hold the layers, the split is refused with a line
for each device (how many layers it could hold).

One device is not a split: its start fits when its need is within that device's room (one_limit), so a model of 10 GiB
is refused on one 8 GiB card of two even though the two cards together could hold it.

-ts is the layer count of each device. llama.cpp reads it as the cut points of the n + 1 layers (the blocks, then the
output layer): layer i goes to the first device whose cumulative share is above i / (n + 1). With whole counts that
sum to n + 1 the cuts fall on the counts, so the devices get exactly the planned layers; the output layer is the last
one, on the last device that has any. Contract C-119 (the argv), C-104 (the plan).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, List, Optional, Sequence, Tuple

from ..hardware.devices import Gpu
from . import loaded
from .calibration import METAL, Calibration, for_devices
from .fit import MARGIN, NO_SPEC, UBATCH, WINDOW_STEP, Spec, compute_parts, need_parts
from .gguf import ModelShape, ctx_train

BISECT_STEPS = 40                       # the share of the room is found to better than a millionth of it


@dataclass(frozen=True)
class Slot:
    """A device a start may use: llama.cpp's name for it (CUDA0), the GPU, and the room a model may use there
    (the free memory less the part kept free; domain/memory/budget.py). shared: of the room, the part that is the
    RAM's (a GPU that shares the RAM). own: what CARL's own servers hold there, added back into the room (W1.6: the
    parts of the card, budget.card_parts)."""
    name: str
    gpu: Gpu
    room: int
    shared: int = 0
    own: int = 0

    def label(self) -> str:
        """GPU 1 (NVIDIA GeForce RTX 3060)"""
        return f"GPU {self.gpu.index} ({self.gpu.name})"


@dataclass(frozen=True)
class Load:
    """What a start puts on the devices, in bytes (module docstring): costs, one entry for each block of the model
    (the MTP head's too) and a last one for the output layer; head: token_embd; compute: one compute buffer;
    margin: the fraction every device adds to its parts; overhead: the backend's context on every device;
    compute_by_n: the compute buffer for n blocks with experts in the RAM (entry n; a MoE model on a backend whose
    buffer holds the staged copy of an expert layer; empty otherwise, and entry 0 is the worst case, compute)."""
    costs: Tuple[float, ...]
    head: float
    compute: float
    margin: float = MARGIN
    overhead: float = 0.0
    compute_by_n: Tuple[float, ...] = ()

    def compute_at(self, n: int) -> float:
        """The compute buffer with the experts of the blocks below n in the RAM (compute when it does not depend on n)."""
        if n <= 0 or not self.compute_by_n:
            return self.compute
        return self.compute_by_n[min(n, len(self.compute_by_n) - 1)]

    @property
    def layers(self) -> int:
        """The layers of the split: the blocks and the output layer (the n + 1 of llama.cpp's cut points)."""
        return len(self.costs)

    def need(self, first: int, last: int, head: bool) -> float:
        """The bytes a device needs for the layers first to last - 1 (a compute buffer, the margin; head: it has
        token_embd too)."""
        return ((sum(self.costs[first:last]) + (self.head if head else 0.0) + self.compute) * (1 + self.margin)
                + self.overhead)

    def total(self) -> float:
        """The need of the whole start on one device."""
        return self.need(0, self.layers, True)


def load_of(shape: ModelShape, weights: float, ctx: int, slots: int = 1, kv: str = "q4_0", swa_full: bool = True,
            spec: Spec = NO_SPEC, ub: int = UBATCH, calib: Calibration = METAL) -> Load:
    """The load of a start (module docstring), from the header shape and the parts of the one-device need."""
    parts = need_parts(shape, weights, ctx, slots, kv, swa_full, spec, ub, calib)
    blocks = max(int(shape.get("blocks", 0)), 0)
    main = max(blocks - int(shape.get("nextn", 0)), 0)
    idle = loaded.idle(shape, spec.mtp, calib)     # the MTP head's blocks do not load: no cost, and no weights (A52)
    experts = loaded.experts(shape, spec.mtp, calib)
    embd, out = float(shape.get("embd_bytes", 0)), float(shape.get("output_bytes", 0))
    rest = max(weights - loaded.head_bytes(shape, weights, spec.mtp, calib) - sum(experts) - embd - out
               - calib.ram_bytes(shape), 0.0)
    tail = calib.output(embd, out) + parts.drafter + parts.draft + parts.vision
    head = calib.head(embd)
    if blocks < 1:                                   # a header without its block count: one layer, nothing to split
        return Load((rest + parts.context + parts.state + tail,), head, parts.compute, MARGIN, parts.overhead)
    base = rest / (main if idle else blocks)
    kept = (parts.context + parts.state) / max(main, 1)
    costs = [(base + (experts[i] if i < len(experts) else 0.0) + (kept if i < main else 0.0)) if i < main or not idle
             else 0.0 for i in range(blocks)]
    costs.append(tail)
    return Load(tuple(costs), head, parts.compute, MARGIN, parts.overhead,
                _compute_by_n(shape, ctx, slots, swa_full, ub, calib, parts.compute))


def _compute_by_n(shape: ModelShape, ctx: int, slots: int, swa_full: bool, ub: int, calib: Calibration,
                  worst: float) -> Tuple[float, ...]:
    """The main compute buffer for 0 to len(experts) blocks with experts in the RAM (Load.compute_by_n): the experts' copy
    (calibration.stage_of) is that of the blocks below n, so it grows with n. Empty where the backend has no staged copy (Metal) or the
    model no experts."""
    experts = shape.get("expert_bytes") or []
    if not calib.gpu_weights or calib.compute is None or not isinstance(experts, list) or not any(experts):
        return ()
    parts = compute_parts(shape, ctx, slots, ub, swa_full)
    return (worst,) + tuple(calib.compute_buffer(parts, shape, ub, n) for n in range(1, len(experts) + 1))


@dataclass(frozen=True)
class Part:
    """The share of one device: its layers (the output layer counts as one) and the bytes it needs for them."""
    slot: Slot
    layers: int
    need: float


@dataclass(frozen=True)
class Split:
    """A plan of the layers over the devices. parts: the devices that get layers, in -dev order (empty when the start
    does not fit); left: the devices that get none and why; holds: how many layers each device of the slots could
    hold at most (the first device's head counted), for the refusal; need: the bytes the plan needs over all its
    devices (a refusal: all of the slots'); room: the rooms of the slots added up."""
    parts: Tuple[Part, ...]
    left: Tuple[Tuple[Slot, str], ...]
    layers: int
    holds: Tuple[int, ...]
    need: float
    room: float

    @property
    def fits(self) -> bool:
        return bool(self.parts)

    def devices(self) -> str:
        """-dev's value: the names of the devices that get layers, in order (CUDA0,CUDA1)."""
        return ",".join(p.slot.name for p in self.parts)

    def tensor_split(self) -> str:
        """-ts's value: the layers of each device (21,20); empty for one device (no split to pass)."""
        return ",".join(str(p.layers) for p in self.parts) if len(self.parts) > 1 else ""


def _fill(avail: Sequence[float], costs: Sequence[float], share: float) -> Tuple[List[int], bool]:
    """The layers each device takes, in order, when it may fill `share` of its room (avail); whether all of them fit.
    Taking as many as fit in each device in turn finds a fit whenever one exists."""
    counts = [0] * len(avail)
    at = 0
    for d, room in enumerate(avail):
        used = 0.0
        while at < len(costs) and used + costs[at] <= room * share:
            used += costs[at]
            counts[d] += 1
            at += 1
    return counts, at == len(costs)


def _avail(slots: Sequence[Slot], load: Load) -> List[float]:
    """What each device has for layers: its room less the margin, the compute buffer and, on the first, the head."""
    keep = 1 + load.margin
    return [max((s.room - load.overhead) / keep - load.compute - (load.head if d == 0 else 0.0), 0.0)
            for d, s in enumerate(slots)]


def plan_split(slots: Sequence[Slot], load: Load) -> Split:
    """The layers of a start over the slots (module docstring)."""
    active = list(slots)
    left: List[Tuple[Slot, str]] = []
    room = float(sum(s.room for s in slots))
    while active:
        avail = _avail(active, load)
        counts, whole = _fill(avail, load.costs, 1.0)
        if not whole:
            break
        lo, hi = 0.0, 1.0
        for _ in range(BISECT_STEPS):
            mid = (lo + hi) / 2
            if _fill(avail, load.costs, mid)[1]:
                hi = mid
            else:
                lo = mid
        counts, _ = _fill(avail, load.costs, hi)
        idle = [d for d, c in enumerate(counts) if c == 0]
        if not idle:
            parts, at = [], 0
            for d, c in enumerate(counts):
                parts.append(Part(active[d], c, load.need(at, at + c, d == 0)))
                at += c
            return Split(tuple(parts), tuple(left), load.layers, tuple(counts), sum(p.need for p in parts), room)
        d = idle[0]
        left.append((active[d], f"it can hold too little: {_why_idle(active[d], load)}"))
        del active[d]
    return _refused(slots, load, room)


def _why_idle(slot: Slot, load: Load) -> str:
    """Why a device gets no layer, in words without the last full stop."""
    gib = 2 ** 30
    return (f"a model can use {slot.room / gib:.1f} GiB of it, and the compute buffer takes {load.compute / gib:.1f} "
            f"GiB of that")


def _refused(slots: Sequence[Slot], load: Load, room: float) -> Split:
    """The split of a start that does not fit: nothing planned, and the layers each device could hold at most."""
    holds = _fill(_avail(slots, load), load.costs, 1.0)[0] if slots else []
    return Split((), (), load.layers, tuple(holds), load.total() + max(len(slots) - 1, 0) * (
        load.compute * (1 + load.margin) + load.overhead), room)


def calib_of(slots: Sequence[Slot]) -> Calibration:
    """The calibration of a start on these devices (domain/memory/calibration.py): the backend in the name of the first
    one (CUDA0). No device (a Mac, the CPU): METAL, the Mac's formula."""
    return for_devices([s.name for s in slots])


def one_limit(slots: Sequence[Slot], limit: float) -> float:
    """The most a start may use when it works on these devices and is not a split: with one device, that device's own
    room, not `limit`, which counts every GPU of the PC (--devices or LLAMA_DEVICES names one of several: the other
    GPUs' room is not that device's). The limit as it is for no device (a Mac) and for several (the split decides)."""
    return min(limit, slots[0].room) if len(slots) == 1 else limit


def fits(slots: Sequence[Slot], load: Load) -> bool:
    """Does the start fit the slots?"""
    return plan_split(slots, load).fits


def max_ctx(shape: ModelShape, weights: float, slots: Sequence[Slot], nslots: int = 1, kv: str = "q4_0",
            swa_full: bool = True, spec: Spec = NO_SPEC, ub: int = UBATCH, calib: Calibration = METAL) -> int:
    """The largest window per slot (a 4K multiple, at most the trained length) that fits the devices; 0 when not even 4K
    does (domain/memory/fit.py max_ctx for one device)."""
    def ok(c: int) -> bool:
        return fits(slots, load_of(shape, weights, c, nslots, kv, swa_full, spec, ub, calib))

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


def ok_for(shape: ModelShape, weights: float, ctx: int, kv: str, ub: int, slots: Sequence[Slot],
           calib: Calibration = METAL) -> Callable[[int, bool, Spec], bool]:
    """The test start_plan (domain/memory/fit.py) takes for several devices: does a start with this many slots, the
    sliding-window cache full or not, and this speculation fit them?"""
    def ok(n: int, swa_full: bool, spec: Spec) -> bool:
        return fits(slots, load_of(shape, weights, ctx, n, kv, swa_full, spec, ub, calib))
    return ok


def trim(slots: Sequence[Slot], allowed: float) -> List[Slot]:
    """The slots with their rooms added up to at most `allowed`: the excess comes off the part of the rooms that is the
    RAM's (a GPU that shares the RAM; the Budget caps that part by the RAM less the reserve), evenly by its size. The
    slots as they are when they fit."""
    excess = sum(s.room for s in slots) - allowed
    pool = sum(s.shared for s in slots)
    if excess <= 0 or pool <= 0:
        return list(slots)
    cut = min(excess, pool)
    return [replace(s, room=max(s.room - int(cut * s.shared / pool), 0)) if s.shared else s for s in slots]


def only(slots: Sequence[Slot], names: Optional[Sequence[str]]) -> List[Slot]:
    """The slots named (llama.cpp's names, in the order of the slots); all of them for no names. Raises KeyError with the
    first name that is no slot's."""
    if not names:
        return list(slots)
    known = {s.name.lower() for s in slots}
    for n in names:
        if n.lower() not in known:
            raise KeyError(n)
    want = {n.lower() for n in names}
    return [s for s in slots if s.name.lower() in want]
