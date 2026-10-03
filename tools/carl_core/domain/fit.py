"""What fits in this Mac's GPU memory: the memory a model needs at a context window, the
largest window that fits, whether a start fits (the launcher refuses one that doesn't), slots
and the RAM prompt cache. The best model for this Mac: autofit.py.

need = weights + KV cache (window x slots x bytes/token) + recurrent state per slot
       + ~1 GiB of compute buffers
The context is per slot: the unified KV pool holds slots x ctx tokens, and each slot has
its own recurrent state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from .errors import ConfigError
from .gguf import GIB, OVERHEAD, ModelShape, ctx_train, kv_bytes_per_token

DEFAULT_CTX = 98304
WINDOW_STEP = 4096                    # windows are offered in 4K steps
MIB = 2 ** 20
CACHE_MIN_MIB, CACHE_MAX_MIB, CACHE_STEP_MIB = 1024, 8192, 256
RESERVE_GB, RESERVE_GB_WITH_VM = 6, 10      # RAM left for macOS and apps (+ the VM)


def need_bytes(shape: ModelShape, weights: int, ctx: int, slots: int = 1, kv: str = "q4_0") -> float:
    """GPU memory a model needs at `ctx` tokens per slot."""
    return weights + kv_bytes_per_token(shape, kv) * ctx * slots + shape["rs_bytes"] * slots + OVERHEAD


def max_ctx(shape: ModelShape, weights: int, limit: float, slots: int = 1, kv: str = "q4_0") -> int:
    """The largest window per slot (a 4K multiple, at most the trained length) that fits; 0
    when the weights alone don't."""
    room = limit - weights - shape["rs_bytes"] * slots - OVERHEAD
    if room <= 0:
        return 0
    per_token = kv_bytes_per_token(shape, kv) * slots
    if per_token <= 0:                        # no KV cache at all: only the trained length limits it
        return ctx_train(shape)
    return min(int(room // per_token) // WINDOW_STEP * WINDOW_STEP, ctx_train(shape))


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
    if weights is not None and weights + OVERHEAD > limit and default_small:
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
        per = f" x {self.slots} slots" if self.slots > 1 else ""
        return f"--ctx {window_label(self.ctx)}{per} ({self.kv} KV)"


def check_start(shape: ModelShape, weights: int, ctx: int, slots: int, kv: str, limit: float) -> StartCheck:
    """The memory a start needs against the GPU limit: a start over the limit won't work
    (it fails to load, or swaps the Mac to a crawl), so the launcher refuses it."""
    slots = max(slots, 1)
    return StartCheck(need_bytes(shape, weights, ctx, slots, kv), limit, ctx, slots, kv,
                      max_ctx(shape, weights, limit, slots, kv))


def human_gb(n: float) -> str:
    """Bytes as decimal GB, the way download sizes are quoted."""
    return f"{n / 1e9:.1f} GB"


def window_label(n: int) -> str:
    """98304 -> "96K"; 0 -> "–"."""
    return f"{n // 1024}K" if n else "–"
