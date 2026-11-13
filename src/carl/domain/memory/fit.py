"""What fits in this Mac's GPU memory: the memory a model needs at a context window, the
largest window that fits, whether a start fits (the launcher refuses one that doesn't), slots,
the speculation and the RAM prompt cache. The best model for this Mac: autofit.py.

need = weights (the whole file, an MTP head too; a drafter file only when MTP runs)
       + KV cache (window x slots x bytes/token) + recurrent state per slot
       + compute buffers (by the ubatch and the KV pool)
       + with MTP: the draft context (its KV, two more compute buffers on Metal, about half of one on CUDA:
         calibration.py draft_compute; the recurrent state x (1 + guesses))
       + a margin of 0.75%
       + on other backends than Metal (calibration.py): the compute buffers scaled by the backend's factor, the weights
         without token_embd, and the backend's context
The context is per slot: the unified KV pool holds slots x ctx tokens, and each slot has
its own recurrent state. The rule and its error against the measured buffers: reference/memory.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable, List, Optional, Tuple

from ..catalog.drafts import FILE_SPECS
from ..shared.constants import DEFAULT_CTX, NGRAM, UBATCH  # noqa: F401  (DEFAULT_CTX, NGRAM: their names here too)
from ..shared.errors import ConfigError
from . import loaded
from .calibration import METAL, Calibration, ComputeParts
from .gguf import GIB, ModelShape, ctx_train, kv_bytes_per_token, swa_bytes_per_token
from .shape import has_head

WINDOW_STEP = 4096                    # windows are offered in 4K steps
MIB = 2 ** 20
CACHE_MIN_MIB, CACHE_MAX_MIB, CACHE_STEP_MIB = 1024, 8192, 256
RESERVE_GB, RESERVE_GB_WITH_VM = 6, 10      # RAM left for macOS and apps (+ the VM)
SWA_UBATCH = UBATCH                   # a window-only SWA cache holds window + one batch per slot
# The compute buffer of one context (measured, llama.cpp 0.6.0, Metal, flash attention; Phase 23.4.4):
#   KV pool x (one layer's K + V in f16 + ubatch x 2 bytes of mask; a second mask with the full SWA cache)
#   + ubatch x the widest activation x 8 bytes + 32 MiB
COMPUTE_FIXED = 32 * MIB
ACT_BYTES = 8
ACT_WIDTH_DEFAULT = 17408             # a header without the widths (a shape cached before 23.4.4): the 27B's FFN
# Allocator slack and the error of the rule (measured: within 10 MiB). Set between two observed starts
# (reference/memory.md): above 0.78% Saluki's 2 x 48K (measured 9.89 GiB) no longer fits a 16 GB Mac's
# 10.0 GiB; below 0.51% the 35B Q4 with MTP + n-gram at 2 x 96K fits the 32 GB M2 Max, where it paged and failed.
MARGIN = 0.0075
UNKNOWN_BUFFERS = 1.0 * GIB           # no header (offline): the buffers cannot be calculated, so 1 GiB


@dataclass(frozen=True)
class Spec:
    """A start's speculation as the memory sees it: kind = --spec-type (none, ngram-mod, draft-mtp,
    draft-mtp,ngram-mod, or a drafter file of another kind: draft-dflash, draft-dspark, draft-eagle3), n =
    --spec-draft-n-max, draft_bytes = the drafter file it loads (-md; 0 for a model with an MTP head). Give MTP only
    when the model has a head or a drafter (mtp_spec). kept: the MTP was chosen by the user (config.json, the
    environment) or by Auto-tune for this model; a start with any expert in the RAM turns MTP off by default
    (offload.ram_spec, A51), never a kept one. vision_bytes: the vision projector the start loads (--mmproj; WCATX),
    0 without it."""
    kind: str = "none"
    n: int = 1
    draft_bytes: int = 0
    kept: bool = field(default=False, compare=False)     # a person or a tune chose it: no default rule turns MTP off
    vision_bytes: int = 0

    @property
    def mtp(self) -> bool:
        """The MTP draft context runs."""
        return "draft-mtp" in self.kind.split(",")

    @property
    def drafted(self) -> bool:
        """A drafter file of another kind than MTP's (DFlash, DSpark, EAGLE-3) runs: its weights and a second context."""
        return any(k in FILE_SPECS for k in self.kind.split(","))

    def without_mtp(self) -> "Spec":
        """The speculation when MTP does not fit: n-gram (MTP + n-gram keeps its n-gram part)."""
        return Spec("ngram-mod", self.n, 0, vision_bytes=self.vision_bytes) if self.mtp else self

    def words(self) -> str:
        """The speculation in words: MTP + n-gram, n-gram, MTP, none."""
        return {"none": "none", "ngram-mod": "n-gram", "draft-mtp": "MTP", "draft-mtp,ngram-mod": "MTP + n-gram",
                "draft-dflash": "DFlash", "draft-dspark": "DSpark", "draft-eagle3": "EAGLE-3"}.get(self.kind, self.kind)


NO_SPEC = Spec()
# n-gram's own settings (llama.cpp's ngram-mod: --spec-ngram-mod-n-match / -n-min / -n-max; Phase 23.5.1.8): it drafts
# only when the last `match` tokens were seen before, a run of `min` to `max` tokens. Measured on the A3B IQ3 and the
# 27B IQ3: 32 / 16 / 96 (llama.cpp's own: 24 / 48 / 64): NGRAM, from domain/shared/constants.py. The launcher's
# defaults (host/serve-llama.sh NGRAM_*) are the same.


def mtp_spec(kind: str, n: int, shape: Optional[ModelShape], draft_bytes: int = 0, kept: bool = False,
             vision_bytes: int = 0) -> Spec:
    """The speculation a start runs: MTP only with a head in the file or a drafter file (draft_bytes > 0),
    else n-gram (the launcher's own fallback). draft_bytes count only for a model without a head. A drafter file of
    another kind (DFlash, DSpark, EAGLE-3) counts with its weights (draft_bytes), and without a file it is left out, as
    the launcher does. kept: see Spec. vision_bytes: the projector a start with vision loads."""
    spec = Spec(kind or "none", max(int(n or 1), 1), 0, kept, vision_bytes)
    if spec.drafted:
        if not draft_bytes:
            left = ",".join(k for k in spec.kind.split(",") if k not in FILE_SPECS)
            return replace(spec, kind=left or "none")
        spec = replace(spec, draft_bytes=draft_bytes)
    if not spec.mtp:
        return spec
    if shape is not None and has_head(shape):
        return spec
    return replace(spec, draft_bytes=draft_bytes) if draft_bytes else spec.without_mtp()


def swa_tokens(shape: ModelShape, ctx: int, swa_full: bool, ub: int = SWA_UBATCH) -> int:
    """Tokens per slot the sliding-window layers keep: all of them with --swa-full, else the window and one batch (-ub:
    measured on Gemma 4 26B-A4B, 264 / 292 / 349 / 461 MiB of KV cache at -ub 512 / 1024 / 2048 / 4096, W6.10)."""
    win = shape.get("swa_window", 0)
    return ctx if swa_full or not win else min(ctx, win + ub)


def _attn_bytes(shape: ModelShape, swa: bool) -> float:
    """f16 bytes per token of the K and V of the widest full-attention layer (swa: sliding-window layer)."""
    elems = shape.get("attn_elems_swa" if swa else "attn_elems")
    if elems is None:
        elems = shape["kvh"] * (shape["kl"] + shape["vl"])
    return elems * 2


def compute_parts(shape: ModelShape, ctx: int, slots: int = 1, ub: int = UBATCH, swa_full: bool = True) -> ComputeParts:
    """The compute buffer of one context in its physical parts (A57; the main context; each MTP draft context has one of
    the same size): act grows with -ub alone (the widest activation, 8 bytes each, and a fixed 32 MiB); mask grows with
    -ub x pool (2 bytes of attention mask per -ub row and token of the pool, a second mask with the full SWA cache);
    kv grows with the pool alone (one layer's K and V in f16, the widest layer, per token). Their sum is the Mac's formula
    (measured on Qwen3.8-27B (Saluki) and Gemma 4 E4B, within 10 MiB: reference/memory.md)."""
    pool = ctx * slots
    swa = bool(shape.get("swa_window", 0) and shape.get("kv_elems_per_token_swa", 0))
    act = ub * (shape.get("act_width") or ACT_WIDTH_DEFAULT) * ACT_BYTES + COMPUTE_FIXED
    if swa and swa_full:                       # the sliding-window layers attend over the whole pool: their own mask
        kv = pool * max(_attn_bytes(shape, False), _attn_bytes(shape, True))
        return ComputeParts(act, pool * 2 * ub * 2, kv, view=kv)
    mask, kv = pool * ub * 2, pool * _attn_bytes(shape, False)
    view = kv
    if swa:
        window = swa_tokens(shape, ctx, False, ub) * slots
        mask += window * ub * 2
        kv += window * _attn_bytes(shape, True)
        view = max(view, window * _attn_bytes(shape, True))
    return ComputeParts(act, mask, kv, swa, view)


def compute_bytes(shape: ModelShape, ctx: int, slots: int = 1, ub: int = UBATCH, swa_full: bool = True) -> float:
    """The compute buffer of one context by the Mac's formula: the sum of compute_parts."""
    return compute_parts(shape, ctx, slots, ub, swa_full).total


@dataclass(frozen=True)
class Need:
    """The memory a start needs, in parts (bytes)."""
    weights: float          # the model file (an MTP head included: llama.cpp loads the whole file)
    drafter: float          # an MTP drafter file (only when MTP runs)
    context: float          # the KV cache: the full-attention layers and the sliding-window layers
    state: float            # the recurrent state: per slot, x (1 + guesses) with MTP
    compute: float          # the main context's compute buffer
    draft: float            # the MTP draft context: its KV (f16, the head's layers) and its two compute buffers
    margin: float
    overhead: float = 0.0   # the backend's own context beyond llama.cpp's buffers (calibration.py; 0 on Metal)
    vision: float = 0.0     # the vision projector (--mmproj): its file, about 1 GB (WCATX)

    @property
    def total(self) -> float:
        return (self.weights + self.drafter + self.context + self.state + self.compute + self.draft + self.margin
                + self.overhead + self.vision)

    @property
    def buffers(self) -> float:
        """Everything but the weights, the context memory and the recurrent state."""
        return self.compute + self.draft + self.margin + self.overhead + self.vision


def need_parts(shape: ModelShape, weights: float, ctx: int, slots: int = 1, kv: str = "q4_0",
               swa_full: bool = True, spec: Spec = NO_SPEC, ub: int = UBATCH, calib: Calibration = METAL,
               n_ram: Optional[int] = None) -> Need:
    """The memory a start needs, in parts (module docstring). weights: the model file only; spec: the
    speculation (a drafter's weights are in it); calib: the backend's constants (METAL: the Mac's, unchanged); n_ram: the
    experts of the blocks below it live in the RAM (--n-cpu-moe), which sets the staged copy in the compute buffer (A60);
    None or 0: the worst case, the model's largest expert layer."""
    context = (kv_bytes_per_token(shape, kv) * ctx * slots
               + swa_bytes_per_token(shape, kv) * swa_tokens(shape, ctx, swa_full, ub) * slots)
    state = shape["rs_bytes"] * slots * ((1 + spec.n) if spec.mtp else 1)
    compute = calib.compute_buffer(compute_parts(shape, ctx, slots, ub, swa_full), shape, ub, n_ram, weights)
    drafter, draft = 0.0, 0.0
    if spec.mtp:
        drafter = spec.draft_bytes
        head_kv = shape["kv_elems_per_token_mtp"] * 2 * ctx * slots if shape["nextn"] else 0   # a drafter shares the KV
        draft = head_kv + calib.draft_compute * compute       # Metal: two compute buffers; CUDA: about half of one
    elif spec.drafted:                                        # a DFlash, DSpark or EAGLE-3 file: its weights and a context
        drafter = spec.draft_bytes                            # (its own KV is small beside the main one: not measured)
        draft = calib.draft_compute * compute
    vision = float(spec.vision_bytes)                         # the projector's weights; its compute buffer is in the margin
    weights = calib.weights(shape, weights - loaded.head_bytes(shape, weights, spec.mtp, calib))
    parts = weights + drafter + context + state + compute + draft + vision
    return Need(weights, drafter, context, state, compute, draft, parts * MARGIN, calib.context, vision)


def need_bytes(shape: ModelShape, weights: float, ctx: int, slots: int = 1, kv: str = "q4_0",
               swa_full: bool = True, spec: Spec = NO_SPEC, ub: int = UBATCH, calib: Calibration = METAL) -> float:
    """GPU memory a model needs at `ctx` tokens per slot (swa_full: its sliding-window layers, if
    any, at full length too; spec: the speculation, MTP's draft context counted; calib: the backend)."""
    return need_parts(shape, weights, ctx, slots, kv, swa_full, spec, ub, calib).total


def max_ctx(shape: ModelShape, weights: float, limit: float, slots: int = 1, kv: str = "q4_0",
            swa_full: bool = True, spec: Spec = NO_SPEC, ub: int = UBATCH, calib: Calibration = METAL) -> int:
    """The largest window per slot (a 4K multiple, at most the trained length) that fits; 0
    when not even 4K does."""
    def fits(c: int) -> bool:
        return need_bytes(shape, weights, c, slots, kv, swa_full, spec, ub, calib) <= limit

    top = ctx_train(shape)
    if fits(top):
        return top
    lo, hi = 0, top // WINDOW_STEP               # the need grows with the window: the last step that fits
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fits(mid * WINDOW_STEP):
            lo = mid
        else:
            hi = mid - 1
    return lo * WINDOW_STEP


def estimated_limit(ram_bytes: float) -> Tuple[int, float]:
    """(GPU limit, fraction of RAM) macOS gives the GPU when nothing better is known (only for
    --ram previews; a real Mac reports its own limit): ~2/3 of RAM below 32 GB, ~3/4 from 32 GB
    up (a 32 GB M2 Max reports 25.0 GiB, 78%)."""
    frac = 2 / 3 if ram_bytes < 32 * GIB else 3 / 4
    return int(ram_bytes * frac), frac


def plan_slots(want: str, two_fit: bool) -> int:
    """Slots for a start: a number as asked, or auto = 2 when two full windows fit, else 1."""
    if want == "auto":
        return 2 if two_fit else 1
    try:
        slots = int(want)
    except ValueError:
        raise ConfigError(f"slots: {want!r} is not auto or a number") from None
    if slots < 1:
        raise ConfigError(f"slots: {slots} is less than 1")
    return slots


def reserve_bytes(reserve_gb: Optional[float], vm_network_up: bool) -> float:
    """RAM kept free for macOS and apps: an explicit reserve, else 10 GiB with the VM up, else 6."""
    gb = reserve_gb if reserve_gb is not None else (RESERVE_GB_WITH_VM if vm_network_up else RESERVE_GB)
    return gb * GIB


def prompt_cache_mib(ram_bytes: int, need: float, reserve: float) -> int:
    """The RAM prompt cache (parked conversations): what RAM allows after the model and the
    reserve, clamped to 1-8 GiB in 256 MiB steps."""
    cache = (ram_bytes - need - reserve) / MIB
    return int(max(CACHE_MIN_MIB, min(CACHE_MAX_MIB, cache)) // CACHE_STEP_MIB * CACHE_STEP_MIB)


def offline_default(default: str, default_small: Optional[str], weights: Optional[float], limit: float) -> str:
    """This Mac's default when auto fit can't size the candidates (their headers can't be
    read, e.g. offline): the catalogue default, or default_small when the default's weights
    alone don't fit (weights unknown: keep the default)."""
    if weights is not None and weights + UNKNOWN_BUFFERS > limit and default_small:
        return default_small
    return default


@dataclass(frozen=True)
class StartCheck:
    """Does a start fit the GPU limit? need and limit in bytes; largest = the largest window
    per slot that fits with these slots (0: the weights alone don't fit)."""
    need: float
    limit: float
    ctx: int
    slots: int
    kv: str
    largest: int

    @property
    def fits(self) -> bool:
        return self.need <= self.limit

    def setup(self) -> str:
        """The setup in words: 4 slots × 256K tokens (q8)."""
        kv = {"q4_0": "q4", "q8_0": "q8"}.get(self.kv, self.kv)
        return f"{self.slots} slot{'s' if self.slots != 1 else ''} × {window_label(self.ctx)} tokens ({kv})"


def check_start(shape: ModelShape, weights: float, ctx: int, slots: int, kv: str, limit: float,
                swa_full: bool = True, spec: Spec = NO_SPEC, ub: int = UBATCH,
                calib: Calibration = METAL) -> StartCheck:
    """The memory a start needs against the GPU limit: a start over the limit won't work
    (it fails to load, or swaps the Mac to a crawl), so the launcher refuses it."""
    slots = max(slots, 1)
    return StartCheck(need_bytes(shape, weights, ctx, slots, kv, swa_full, spec, ub, calib), limit, ctx, slots, kv,
                      max_ctx(shape, weights, limit, slots, kv, swa_full, spec, ub, calib))


@dataclass(frozen=True)
class StartPlan:
    """How a start runs at its window: slots, the sliding-window cache (None: the model has no
    sliding-window layers) and the speculation; dropped = MTP does not fit, so n-gram only; ram_off = MTP is off by
    default because experts are in the RAM."""
    slots: int
    swa_full: Optional[bool]
    spec: Spec
    dropped: bool = False
    ram_off: bool = False       # MTP is off because experts are in the RAM (offload.ram_spec, A51), not for lack of room


def start_plan(mode: str, shape: ModelShape, weights: float, ctx: int, want_slots: str, kv: str, limit: float,
               spec: Spec = NO_SPEC, ub: int = UBATCH,
               ok: Optional[Callable[[int, bool, Spec], bool]] = None, calib: Calibration = METAL) -> StartPlan:
    """Slots, cache and speculation for a start at a fixed window (Auto fit's order, Phase 23.4.4): slots
    auto = 2 when two windows fit; MTP goes (n-gram stays) before a slot does. Nothing fits: the asked
    slots (auto: 1) and speculation, for the check to refuse. For a model with sliding-window layers
    (cache.swa: full; window; auto = full when it fits with those slots and that speculation: saved
    prompt states restore only then) auto prefers a second slot and MTP over the full cache.
    ok: the test for several GPUs (split.ok_for: slots, full cache, speculation -> does it fit the devices); without
    it, a start fits when its need is within limit. calib: the backend's constants (calibration.py)."""
    swa = bool(shape.get("swa_window", 0))
    plan_full = mode == "full" or not swa

    def fits(n: int, full_cache: bool, s: Spec) -> bool:
        return ok(n, full_cache, s) if ok else need_bytes(shape, weights, ctx, n, kv, full_cache, s, ub, calib) <= limit
    counts = [2, 1] if want_slots == "auto" else [plan_slots(want_slots, False)]
    specs = [spec] if not spec.mtp else [spec, spec.without_mtp()]
    tries: List[Tuple[int, Spec]] = [(n, s) for n in counts for s in specs]
    slots, use = next(((n, s) for n, s in tries if fits(n, plan_full, s)), (counts[-1], spec))
    if not swa:
        full: Optional[bool] = None
    elif mode in ("full", "window"):
        full = mode == "full"
    else:
        full = fits(slots, True, use)
    return StartPlan(slots, full, use, use != spec)


def swa_plan(mode: str, shape: ModelShape, weights: float, ctx: int, want_slots: str, kv: str,
             limit: float, spec: Spec = NO_SPEC, ub: int = UBATCH,
             calib: Calibration = METAL) -> Tuple[int, Optional[bool]]:
    """(slots, swa_full) of start_plan: slots auto = 2 when two windows fit; for a model with
    sliding-window layers whether they keep full length (None for other models)."""
    p = start_plan(mode, shape, weights, ctx, want_slots, kv, limit, spec, ub, None, calib)
    return p.slots, p.swa_full


def window_label(n: int) -> str:
    """98304 -> "96K"; 0 -> "–"."""
    return f"{n // 1024}K" if n else "–"


# ---------------------------------------------------------------- the batch size and the load mode (A44, A45)
UB_LOWER = (1024, 512)                # the -ub a start tries below its own when that one would cost the setup


@dataclass(frozen=True)
class UbChoice:
    """The -ub of a start: ub, the one it uses; want, the one it was given (the model's Auto-tune record, else the
    default); why: one line when ub is lower than want ("" when want is kept)."""
    ub: int
    want: int
    why: str = ""


def ub_choice(want: int, setup: Callable[[int], Tuple[object, str]]) -> UbChoice:
    """The context comes first (A45): a start keeps `want` when its setup is the one the smallest -ub (UBATCH) gets;
    else it takes the largest of UB_LOWER below `want` that keeps that setup (UBATCH always does). setup(ub): a key
    that compares the setups (the slots, the context, the speculation, whether it fits) and the setup in words. So a
    larger -ub never costs a slot or context: 2 x 96K never becomes 2 x 64K or 1 x 96K, nothing goes below 48K."""
    if want <= UBATCH:
        return UbChoice(want, want)
    ref, ref_words = setup(UBATCH)
    got, got_words = setup(want)
    if got == ref:
        return UbChoice(want, want)
    ub = next((u for u in UB_LOWER if UBATCH <= u < want and setup(u)[0] == ref), UBATCH)
    return UbChoice(ub, want, f"Batch size -ub {ub}, not {want}: with -ub {want} the setup would be {got_words}, "
                              f"not {ref_words}.")


@dataclass(frozen=True)
class LoadMode:
    """How llama.cpp loads the weights (A44, A45): load mode none (read into the RAM, not mapped from the file) when
    the free RAM covers what the server holds in the RAM under it and the load reserve; else the mapped load
    (llama.cpp's default), which lets a model larger than that start. need: the server's RAM under load mode none (a
    Mac and a GPU that shares the RAM: the whole start; a PC with a discrete GPU: the part kept in the RAM); free: the
    RAM free when the start looks (0: not known, and the start says nothing and passes nothing); reserve: the load
    reserve (llama.load_reserve_gib)."""
    need: float
    free: float
    reserve: float

    @property
    def mode(self) -> str:
        """none, mapped, or "" when the free RAM is not known."""
        if self.free <= 0:
            return ""
        return "none" if self.need + self.reserve <= self.free else "mapped"

    def line(self) -> str:
        """The start's line about it ("" when the free RAM is not known)."""
        need, free, reserve = (f"{x / GIB:.1f} GiB" for x in (self.need, self.free, self.reserve))
        if self.mode == "none":
            return (f"Load: the weights are read into the RAM (--load-mode none): the server needs {need} there, and "
                    f"{free} are free, more than that and the load reserve of {reserve}.")
        if self.mode == "mapped":
            return (f"Load: the weights stay mapped from the file: the server would need {need} in the RAM, and the "
                    f"{free} free do not cover that and the load reserve of {reserve} (llama.load_reserve_gib).")
        return ""
