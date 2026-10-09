"""What fits in this Mac's GPU memory: the memory a model needs at a context window, the
largest window that fits, whether a start fits (the launcher refuses one that doesn't), slots,
the speculation and the RAM prompt cache. The best model for this Mac: autofit.py.

need = weights (the whole file, an MTP head too; a drafter file only when MTP runs)
       + KV cache (window x slots x bytes/token) + recurrent state per slot
       + compute buffers (by the ubatch and the KV pool)
       + with MTP: the draft context (its KV, two more compute buffers; the recurrent state x (1 + guesses))
       + a margin of 0.75%
The context is per slot: the unified KV pool holds slots x ctx tokens, and each slot has
its own recurrent state. The rule and its error against the measured buffers: reference/memory.md.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import List, Optional, Tuple

from .errors import ConfigError
from .gguf import GIB, ModelShape, ctx_train, kv_bytes_per_token, swa_bytes_per_token

DEFAULT_CTX = 98304
WINDOW_STEP = 4096                    # windows are offered in 4K steps
MIB = 2 ** 20
CACHE_MIN_MIB, CACHE_MAX_MIB, CACHE_STEP_MIB = 1024, 8192, 256
RESERVE_GB, RESERVE_GB_WITH_VM = 6, 10      # RAM left for macOS and apps (+ the VM)
SWA_UBATCH = 512                      # a window-only SWA cache holds window + one batch per slot
UBATCH = 512                          # -ub, llama.ub's default
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
    draft-mtp,ngram-mod), n = --spec-draft-n-max, draft_bytes = the MTP drafter file it loads (-md;
    0 for a model with an MTP head). Give MTP only when the model has a head or a drafter (mtp_spec)."""
    kind: str = "none"
    n: int = 1
    draft_bytes: int = 0

    @property
    def mtp(self) -> bool:
        """The MTP draft context runs."""
        return "draft-mtp" in self.kind.split(",")

    def without_mtp(self) -> "Spec":
        """The speculation when MTP does not fit: n-gram (MTP + n-gram keeps its n-gram part)."""
        return Spec("ngram-mod", self.n, 0) if self.mtp else self

    def words(self) -> str:
        """The speculation in words: MTP + n-gram, n-gram, MTP, none."""
        return {"none": "none", "ngram-mod": "n-gram", "draft-mtp": "MTP",
                "draft-mtp,ngram-mod": "MTP + n-gram"}.get(self.kind, self.kind)


NO_SPEC = Spec()


def mtp_spec(kind: str, n: int, shape: Optional[ModelShape], draft_bytes: int = 0) -> Spec:
    """The speculation a start runs: MTP only with a head in the file or a drafter file (draft_bytes > 0),
    else n-gram (the launcher's own fallback). draft_bytes count only for a model without a head."""
    spec = Spec(kind or "none", max(int(n or 1), 1), 0)
    if not spec.mtp:
        return spec
    if shape is not None and shape.get("nextn", 0) > 0:
        return spec
    return replace(spec, draft_bytes=draft_bytes) if draft_bytes else spec.without_mtp()


def swa_tokens(shape: ModelShape, ctx: int, swa_full: bool) -> int:
    """Tokens per slot the sliding-window layers keep: all of them with --swa-full, else the window."""
    win = shape.get("swa_window", 0)
    return ctx if swa_full or not win else min(ctx, win + SWA_UBATCH)


def _attn_bytes(shape: ModelShape, swa: bool) -> float:
    """f16 bytes per token of the K and V of the widest full-attention layer (swa: sliding-window layer)."""
    elems = shape.get("attn_elems_swa" if swa else "attn_elems")
    if elems is None:
        elems = shape["kvh"] * (shape["kl"] + shape["vl"])
    return elems * 2


def compute_bytes(shape: ModelShape, ctx: int, slots: int = 1, ub: int = UBATCH, swa_full: bool = True) -> float:
    """The compute buffer of one context (the main context; each MTP draft context has one of the same size).
    Measured on Qwen3.8-27B (Saluki) and Gemma 4 E4B: within 10 MiB (reference/memory.md)."""
    pool = ctx * slots
    swa = bool(shape.get("swa_window", 0) and shape.get("kv_elems_per_token_swa", 0))
    if swa and swa_full:                       # the sliding-window layers attend over the whole pool: their own mask
        out = pool * (max(_attn_bytes(shape, False), _attn_bytes(shape, True)) + 2 * ub * 2)
    else:
        out = pool * (_attn_bytes(shape, False) + ub * 2)
        if swa:
            out += swa_tokens(shape, ctx, False) * slots * (_attn_bytes(shape, True) + ub * 2)
    return out + ub * (shape.get("act_width") or ACT_WIDTH_DEFAULT) * ACT_BYTES + COMPUTE_FIXED


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

    @property
    def total(self) -> float:
        return self.weights + self.drafter + self.context + self.state + self.compute + self.draft + self.margin

    @property
    def buffers(self) -> float:
        """Everything but the weights, the context memory and the recurrent state."""
        return self.compute + self.draft + self.margin


def need_parts(shape: ModelShape, weights: float, ctx: int, slots: int = 1, kv: str = "q4_0",
               swa_full: bool = True, spec: Spec = NO_SPEC, ub: int = UBATCH) -> Need:
    """The memory a start needs, in parts (module docstring). weights: the model file only; spec: the
    speculation (a drafter's weights are in it)."""
    context = (kv_bytes_per_token(shape, kv) * ctx * slots
               + swa_bytes_per_token(shape, kv) * swa_tokens(shape, ctx, swa_full) * slots)
    state = shape["rs_bytes"] * slots * ((1 + spec.n) if spec.mtp else 1)
    compute = compute_bytes(shape, ctx, slots, ub, swa_full)
    drafter, draft = 0.0, 0.0
    if spec.mtp:
        drafter = spec.draft_bytes
        head_kv = shape["kv_elems_per_token_mtp"] * 2 * ctx * slots if shape["nextn"] else 0   # a drafter shares the KV
        draft = head_kv + 2 * compute
    parts = weights + drafter + context + state + compute + draft
    return Need(weights, drafter, context, state, compute, draft, parts * MARGIN)


def need_bytes(shape: ModelShape, weights: float, ctx: int, slots: int = 1, kv: str = "q4_0",
               swa_full: bool = True, spec: Spec = NO_SPEC, ub: int = UBATCH) -> float:
    """GPU memory a model needs at `ctx` tokens per slot (swa_full: its sliding-window layers, if
    any, at full length too; spec: the speculation, MTP's draft context counted)."""
    return need_parts(shape, weights, ctx, slots, kv, swa_full, spec, ub).total


def max_ctx(shape: ModelShape, weights: float, limit: float, slots: int = 1, kv: str = "q4_0",
            swa_full: bool = True, spec: Spec = NO_SPEC, ub: int = UBATCH) -> int:
    """The largest window per slot (a 4K multiple, at most the trained length) that fits; 0
    when not even 4K does."""
    def fits(c: int) -> bool:
        return need_bytes(shape, weights, c, slots, kv, swa_full, spec, ub) <= limit

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
                swa_full: bool = True, spec: Spec = NO_SPEC, ub: int = UBATCH) -> StartCheck:
    """The memory a start needs against the GPU limit: a start over the limit won't work
    (it fails to load, or swaps the Mac to a crawl), so the launcher refuses it."""
    slots = max(slots, 1)
    return StartCheck(need_bytes(shape, weights, ctx, slots, kv, swa_full, spec, ub), limit, ctx, slots, kv,
                      max_ctx(shape, weights, limit, slots, kv, swa_full, spec, ub))


@dataclass(frozen=True)
class StartPlan:
    """How a start runs at its window: slots, the sliding-window cache (None: the model has no
    sliding-window layers) and the speculation; dropped = MTP does not fit, so n-gram only."""
    slots: int
    swa_full: Optional[bool]
    spec: Spec
    dropped: bool = False


def start_plan(mode: str, shape: ModelShape, weights: float, ctx: int, want_slots: str, kv: str, limit: float,
               spec: Spec = NO_SPEC, ub: int = UBATCH) -> StartPlan:
    """Slots, cache and speculation for a start at a fixed window (Auto fit's order, Phase 23.4.4): slots
    auto = 2 when two windows fit; MTP goes (n-gram stays) before a slot does. Nothing fits: the asked
    slots (auto: 1) and speculation, for the check to refuse. For a model with sliding-window layers
    (cache.swa: full; window; auto = full when it fits with those slots and that speculation: saved
    prompt states restore only then) auto prefers a second slot and MTP over the full cache."""
    swa = bool(shape.get("swa_window", 0))
    plan_full = mode == "full" or not swa
    counts = [2, 1] if want_slots == "auto" else [plan_slots(want_slots, False)]
    specs = [spec] if not spec.mtp else [spec, spec.without_mtp()]
    tries: List[Tuple[int, Spec]] = [(n, s) for n in counts for s in specs]
    slots, use = next(((n, s) for n, s in tries
                       if need_bytes(shape, weights, ctx, n, kv, plan_full, s, ub) <= limit), (counts[-1], spec))
    if not swa:
        full: Optional[bool] = None
    elif mode in ("full", "window"):
        full = mode == "full"
    else:
        full = need_bytes(shape, weights, ctx, slots, kv, True, use, ub) <= limit
    return StartPlan(slots, full, use, use != spec)


def swa_plan(mode: str, shape: ModelShape, weights: float, ctx: int, want_slots: str, kv: str,
             limit: float, spec: Spec = NO_SPEC, ub: int = UBATCH) -> Tuple[int, Optional[bool]]:
    """(slots, swa_full) of start_plan: slots auto = 2 when two windows fit; for a model with
    sliding-window layers whether they keep full length (None for other models)."""
    p = start_plan(mode, shape, weights, ctx, want_slots, kv, limit, spec, ub)
    return p.slots, p.swa_full


def window_label(n: int) -> str:
    """98304 -> "96K"; 0 -> "–"."""
    return f"{n // 1024}K" if n else "–"
