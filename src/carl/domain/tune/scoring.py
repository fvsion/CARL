"""Auto-tune: measure one model on this Mac and choose its speculation, context and slots.

Steps (the model loads once per speculation mode):
  1. memory: the largest window that fits with 1 and 2 slots
  2. speculation: none, n-gram (n = 2; without MTP also n = 1), and (with an MTP head) MTP and
     MTP + n-gram at n = 1 and 2, or (with a separate MTP drafter, Gemma 4) at n = 1 to 4.
     Each runs prose, fresh code and a code re-emit, twice. Score = weighted geometric mean
     (prose 0.4, code 0.4, re-emit 0.2); a mode with drafting must beat a simpler one by 3%.
  3. prompt reading: cold reads at 8K, 32K and 64K tokens (quick: 8K and 32K; long: also
     128K and 192K, as far as the model's window fits) give this Mac's context zones: a cold
     re-read of a full window within 3 min = fast, 10 min = slow, beyond = very slow. Long
     also records the decode speed at each depth (the reply to the read).
Modes: quick (fewer speculation modes, shallower reads, ~5-10 min), default, long (deep reads:
a 192K cold read alone takes 10-40 min; the time is estimated before those reads start).
  3a. batch size (A45, A46, every OS and depth): each -ub of UB_CANDIDATES (512, 1024, 2048, 4096) that keeps the
     setup -ub 512 gets (Auto fit's order: 2 x 96K ... 1 x 48K, 2 x 128K ... on NVIDIA; see ub_setup) starts at that setup (its slots and
     context, so a MoE model keeps the experts in the RAM it will run with) and reads a prompt, then writes. The
     context comes first, then the write speed: a -ub whose write speed is more than UB_DECODE_NOISE under the best
     is out; of the rest the fastest read wins by UB_MARGIN over the default (2048 on a PC, 512 on a Mac), and the
     record keeps it (ub, ub_note).
  3b. MTP with experts in the RAM (A51): a model with an MTP head whose chosen setup keeps any expert layer in the
     RAM runs n-gram by default (MTP did not pay there, WCAL round 2). The best MTP mode and n-gram are measured at
     that setup at the chosen -ub (prose, code and an edit); MTP is kept in the record only when it beats n-gram by
     DRAFT_MARGIN, and the start follows the record.
  3c. threads with experts in the RAM (A54, A57): at that same setup the speculation and -ub chosen run with the
     threads pinned one per physical core and unpinned (llama.cpp's own, the start's default there), each measured on
     prose, code and an edit, PIN_ROUNDS times each, alternating (pinned, unpinned, unpinned, pinned: a drift in the
     machine favours neither), the speeds averaged. Pinned is kept only when it writes faster by DRAFT_MARGIN; the
     record keeps the verdict (pin, pin_note), and the start pins only when it says pinned. Only on a CPU whose
     topology is known.
  3d. the CPU alone (A71): llama.cpp reads prompts 2 to 3 times faster on the CPU with an f16 context memory than with
     q8_0 or q4_0, so a tune on the CPU alone measures the types that fit the RAM (f16, q8_0, q4_0: cpu_kv.covers) right
     after the memory step, at the model's -ub: a cold read of UB_PROMPT tokens, then the write speed. best_kv keeps the
     type that reads (or writes) faster by DRAFT_MARGIN without writing (or reading) more than UB_DECODE_NOISE slower
     than the one before it, f16 first; every later step runs with it. The read step on the CPU reads only up to
     CPU_READ_DEPTHS' 8K tokens (a 32K read took 35 min at q4_0) and gives the longer reads, as the zones need them, as
     an estimate from the slope of the shallow ones (estimate_words).
  4. result: the context memory type (kv q4_0; on the CPU alone the one measured), the best speculation, the context
     (see choose_ctx) and 2 slots when two windows fit, at the chosen -ub; the record also keeps the load mode the
     reads ran with (A44).
The rules and the scoring are here, pure; the run (AutoTuner, through the TuneServer port) is application/tune.py.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict, List, Literal, Mapping, Optional, Sequence, Tuple

from ..shared.constants import DEFAULT_CTX, DRAFT_MARGIN, TEMP, TOP_K, TOP_P, UB_CANDIDATES, UBATCH  # noqa: F401  (UB_CANDIDATES: its name here)
from ..shared.errors import ConfigError
from ..shared.platform_words import MACOS, PlatformWords
from ..memory.autofit import TIERS, Tier
from ..memory.cpu_kv import CPU_KV, name as kv_type_name
from ..memory.fit import NO_SPEC, WINDOW_STEP, Spec, need_bytes
from ..memory.gguf import ModelShape, ctx_train
from ..memory.shape import has_head
from ..catalog.drafts import TUNE_NS
from ..catalog.inventory import CTX_FLOOR
from ..hardware.cpu_topology import ThreadPin
from ..shared.types import CtxZones, JsonObject, KvResult, ModeResult

Mode = Tuple[str, int]                     # (speculation, draft n)
Read = Tuple[int, float]                   # (prompt tokens, cold read tok/s)
Depth = Literal["quick", "default", "long"]
DEPTHS: Tuple[Depth, ...] = ("quick", "default", "long")
TUNE_KV = "q4_0"
MIN_WINDOW = 16384                         # below this the model is not usable here
TEST_CTX, TEST_CTX_QUICK = 69632, 36864    # the window benchmarks run with (64K / 32K + room)
LONG_READ_CTX = 200704                     # long mode's reads: 192K + room
READ_ROOM = 2048                           # a read needs this much window beyond its prompt
# The CPU alone (A71) reads prompts slowly (55 tok/s at 8K with q4_0, 16 tok/s at 32K: a 32K read took 35 min), so its
# read step stops at 8K tokens, with shallow reads for the slope, and the longer reads are an estimate from it.
CPU_READ_DEPTHS = {"quick": [4096, 8192], "default": [2048, 4096, 8192], "long": [2048, 4096, 8192]}
CPU_READ_CTX = 8192 + READ_ROOM            # the window of the CPU's reads
LONG_DECODE_TOKENS = 64                    # long mode: tokens generated after each read (decode at depth)
PARALLEL_COUNTS = (1, 2, 3, 4)             # requests at once (default and long modes): up to CARL's 4 slots
PARALLEL_PROMPT, PARALLEL_GEN = 512, 96
# The prompt read at each batch size (-ub; W6.9, every OS since A45): a CUDA card reads prompts 2.5 times faster at
# 2048 than at 512 with a MoE model's experts in the RAM, Metal about the same. A -ub is measured only when it keeps
# the setup (the context first), and it wins only when it reads UB_MARGIN faster than the default; the model's record
# keeps it (a -ub typed in the server options wins; a saved choice or llama.ub is asked about, A51: domain/server/
# ub_order.py). UB_CANDIDATES: domain/shared/constants.py.
UB_PROMPT = 8192                        # a cold read of this many tokens at each -ub (several batches of the largest)
UB_DECODE = 128                         # then this many tokens written: the write speed at that -ub (A46)
UB_MARGIN = 1.05
# The write speed comes before the read (A46, the user: "if we have to drop to 1024, lose prompt speed (which is helped
# by caching anyway) and keep more generation speed"): a larger -ub has a larger compute buffer, which can leave more
# of a MoE model's experts in the RAM. A -ub that writes more than this under the best one is not chosen (the noise).
UB_DECODE_NOISE = 0.03
STANDARD_WINDOWS = (32768, 49152, 65536, DEFAULT_CTX, 131072, 163840)
FALLBACK_WINDOW = 32768
ZONE_SECONDS = {"good": 180, "slow": 600, "very_slow": 1200}   # cold read of a full window
ZONE_MAX = 262144
PIN_ROUNDS = 2                             # the threads step runs pinned and unpinned this many times each (A54)
WEIGHTS = {"prose": 0.4, "code": 0.4, "edit": 0.2}
RUNS_PER_WORKLOAD = 2
FILLER_WORDS = ("system memory cache token window model server request decode prompt layer tensor kernel metal quant "
                "expert router batch slot context speculative draft accept reject verify stream session agent tool").split()
EDIT_SOURCE_CHARS = 9000
GEN_TIMEOUT, READ_TIMEOUT = 1800.0, 3600.0     # seconds: a 64K cold read on a slow Mac takes minutes


def as_depth(v: object) -> Depth:
    """A tune mode from the command line or the dashboard (anything else: default)."""
    return "quick" if v == "quick" else "long" if v == "long" else "default"


def speculation_modes(has_mtp: bool, quick: bool, drafter: bool = False, kinds: Sequence[str] = ()) -> List[Mode]:
    """The modes to measure, simplest first (best_mode prefers earlier ones). drafter: the MTP
    speculation comes from a separate drafter file (Gemma 4), which accepts more draft tokens than
    a Qwen MTP head: n = 1 to 4 (quick: 1 and 2; on the E4B n = 2 was best, 76.0 tok/s vs 69.0 at
    n = 1 and 72.1 at n = 4). kinds: the --spec-type of each drafter file of another kind that is downloaded and that
    llama.cpp supports (DFlash, DSpark, EAGLE-3; WCATX, A74), each tried alone at the guesses of TUNE_NS (quick: the
    first), after the MTP modes."""
    # n-gram once: llama.cpp's ngram-mod does not read the guesses (--spec-draft-n-max is MTP's); its n=1 and n=2 rows
    # measured the same setup (Phase 23.5.1.8)
    modes: List[Mode] = [("none", 1), ("ngram-mod", 1)]
    if has_mtp or drafter:
        top = (2 if quick else 4) if drafter else (1 if quick else 2)
        for n in range(1, top + 1):
            modes += [("draft-mtp", n), ("draft-mtp,ngram-mod", n)]
    for kind in kinds:
        modes += [(kind, n) for n in TUNE_NS.get(kind, (2,))[:1 if quick else None]]
    return modes


def read_depths(depth: Depth, cpu: bool = False) -> List[int]:
    """The cold-read depths of a tune mode (tokens). cpu: the CPU alone reads up to 8K (CPU_READ_DEPTHS)."""
    if cpu:
        return list(CPU_READ_DEPTHS[depth])
    return {"quick": [8192, 32768], "default": [8192, 32768, 65536],
            "long": [8192, 32768, 65536, 131072, 196608]}[depth]


def read_ctx(depth: Depth, max_one_slot: int, cpu: bool = False) -> int:
    """The window the reading server runs with: the deepest read's, at most what fits."""
    want = (CPU_READ_CTX if cpu else LONG_READ_CTX if depth == "long" else TEST_CTX_QUICK if depth == "quick"
            else TEST_CTX)
    return min(want, max_one_slot)


def read_seconds(reads: Sequence[Read], tokens: int) -> float:
    """Estimated seconds for a cold read of `tokens` (the fit of zones_from_reads)."""
    a, b = _fit(reads)
    return a * tokens + b * tokens ** 2 / 2


def workloads(edit_source: str) -> Dict[str, str]:
    """The three benchmark prompts. The re-emit prompt copies a code file back with one
    rename: the case n-gram drafting speeds up."""
    return {
        "prose": "Explain how a B-tree insertion works, in detail, with an example.",
        "code": "Write a complete Python module implementing an LRU cache with TTL expiry, thread safety, "
                "type hints and docstrings. Code only.",
        "edit": "Return the following file in full, unchanged except rename the function `call` to `request` "
                "everywhere. Output only the code.\n\n" + edit_source[:EDIT_SOURCE_CHARS],
    }


def weighted_score(speeds: Mapping[str, float]) -> float:
    """Weighted geometric mean of the tok/s per workload (a floor of 0.1 avoids log(0))."""
    return round(math.exp(sum(w * math.log(max(speeds[k], 0.1)) for k, w in WEIGHTS.items())), 2)


def mode_key(mode: Mode) -> str:
    return f"{mode[0]}:{mode[1]}"


def best_mode(results: Mapping[str, ModeResult]) -> Mode:
    """The best mode, in measuring order: a later mode wins only by beating the current
    best by DRAFT_MARGIN."""
    keys = list(results)
    if not keys:
        raise ConfigError("no speculation mode was measured")
    best = keys[0]
    for key in keys[1:]:
        if results[key]["score"] > results[best]["score"] * DRAFT_MARGIN:
            best = key
    spec, n = best.rsplit(":", 1)
    return spec, int(n)


def _fit(reads: Sequence[Read]) -> Tuple[float, float]:
    """(a, b) of seconds per token t(n) = a + b*n, least squares over the reads (two reads:
    the line through them; one: no growth)."""
    if not reads or any(tps <= 0 for _, tps in reads):
        raise ConfigError("no usable prompt-reading measurement")
    pts = [(float(n), 1 / tps) for n, tps in reads]
    mx = sum(n for n, _ in pts) / len(pts)
    my = sum(t for _, t in pts) / len(pts)
    var = sum((n - mx) ** 2 for n, _ in pts)
    b = max(sum((n - mx) * (t - my) for n, t in pts) / var, 0.0) if var > 0 else 0.0
    return max(my - b * mx, 1e-6), b


def zones_from_reads(reads: Sequence[Read]) -> CtxZones:
    """This Mac's context zones from cold prompt reads. Seconds per token grow about
    linearly with depth, t(n) = a + b*n, so reading a full window W takes a*W + b*W^2/2."""
    a, b = _fit(reads)

    def largest(seconds: float) -> int:
        w = 4096
        while w < ZONE_MAX and a * (w + 4096) + b * (w + 4096) ** 2 / 2 <= seconds:
            w += 4096
        return w

    return {"good": largest(ZONE_SECONDS["good"]), "slow": largest(ZONE_SECONDS["slow"]),
            "very_slow": largest(ZONE_SECONDS["very_slow"])}


def choose_ctx(base: int, zones: CtxZones, max_one_slot: int) -> int:
    """The context per slot to save. When 96K fits it is the floor; above it, keep the
    catalogue's window while a cold read of it is no worse than "slow" here and it fits,
    else the largest standard window in the fast zone. When 96K doesn't fit: the largest
    standard window that does."""
    if max_one_slot < CTX_FLOOR:
        return max([w for w in STANDARD_WINDOWS if w <= max_one_slot] or [min(FALLBACK_WINDOW, max_one_slot)])
    if base <= zones["slow"] and base <= max_one_slot:
        ctx = base
    else:
        fast = max(zones["good"], FALLBACK_WINDOW)
        ctx = max([w for w in STANDARD_WINDOWS if w <= fast and w <= max_one_slot]
                  or [min(FALLBACK_WINDOW, max_one_slot)])
    return max(ctx, CTX_FLOOR)



def chat_body(prompt: str, max_tokens: int) -> JsonObject:
    return {"model": "x", "max_tokens": max_tokens, "temperature": TEMP, "top_p": TOP_P, "top_k": TOP_K,
            "chat_template_kwargs": {"enable_thinking": False}, "cache_prompt": False,
            "messages": [{"role": "user", "content": prompt}]}


def read_body(text: str, max_tokens: int = 1) -> JsonObject:
    return {"model": "x", "max_tokens": max_tokens, "cache_prompt": False,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": text + ("\n\nReply with OK." if max_tokens == 1 else
                                                             "\n\nSummarise these records in a long paragraph.")}]}




@dataclass(frozen=True)
class TunePlan:
    """What a tune needs to know about the model and this Mac."""
    shape: ModelShape
    weights: int
    limit: int
    base_ctx: int          # the catalogue/header window (no config.json)
    depth: Depth           # quick | default | long
    date: str
    machine: str
    llama_cpp: str
    edit_source: str
    drafter: bool = False  # MTP from a separate drafter file (downloaded; its weights are in `weights`)
    kinds: Tuple[str, ...] = ()     # the --spec-type of each downloaded drafter file of another kind the engine supports
    unused: Tuple[str, ...] = ()    # ...and the words of each downloaded one it does not (A74: said in the report)
    kind_bytes: Tuple[Tuple[str, int], ...] = ()    # the size of the file of each of `kinds`: the memory counts it
    ubs: Tuple[int, ...] = ()   # the batch sizes (-ub) to measure the prompt read at (A45: UB_CANDIDATES); () none
    ub_default: int = UBATCH    # the -ub before a tune: 2048 on a PC, 512 on a Mac (settings.default_ub)
    # does a start of ctx tokens x slots at this -ub and speculation fit this computer? None: its need within `limit`
    # (a Mac); a PC with GPUs: the start's own plan, the experts in the RAM included (FitService.tune_room)
    room: Optional[Callable[[int, int, int, Spec], bool]] = None
    # the expert layers a start of ctx tokens x slots at this -ub and speculation keeps in the RAM (0: none; the start's
    # own plan, FitService.tune_ram). None: a Mac and a computer with no GPU that CARL can name (no experts in the RAM)
    ram_layers: Optional[Callable[[int, int, int, Spec], int]] = None
    # the threads this CPU can pin, one per physical core (cpu_topology.pin_for; None: unknown CPU, or a Mac)
    pin: Optional[ThreadPin] = None
    # Auto fit's order on this machine (Budget.tiers; A53: NVIDIA starts at 128K): the setup the batch size step keeps
    tiers: Tuple[Tier, ...] = TIERS
    # the context memory type the memory is judged with (TUNE_KV; on the CPU alone the one the tune chose, A71)
    kv: str = TUNE_KV
    # the CPU alone (A71): does a start of ctx tokens x slots at this -ub and speculation fit the RAM with this context
    # memory type (FitService.tune_kv)? None: not the CPU alone, and the tune keeps q4_0
    kv_room: Optional[Callable[[str, int, int, int, Spec], bool]] = None
    # the backend this tune runs on (FitService.start_backend: cpu, cuda-13, metal, ...; "" not known); the record keeps
    # it, and a later start on another backend sees the result as an estimate (A72, provenance.stale)
    backend: str = ""

    @property
    def has_mtp(self) -> bool:
        return has_head(self.shape)

    def draft_bytes_of(self, spec: str) -> int:
        """The size of the drafter file that a speculation of `kinds` loads (0: it loads none, or an MTP drafter, whose
        weights are in `weights`)."""
        sizes = dict(self.kind_bytes)
        return next((sizes[s] for s in spec.split(",") if s in sizes), 0)

    @property
    def quick(self) -> bool:
        return self.depth == "quick"

    def modes(self) -> List[Mode]:
        return speculation_modes(self.has_mtp, self.quick, self.drafter and not self.has_mtp, self.kinds)

    def ram_check(self, ub: int = 0) -> bool:
        """Auto-tune measures MTP against n-gram at the chosen setup (3b): the model has an MTP head and a start at this
        -ub keeps any expert layer in the RAM. A larger -ub can put experts in the RAM that a smaller one keeps on the GPUs
        (its compute buffer is bigger), so the answer depends on the -ub: ask with the one the batch size step chose.
        ub 0 (not chosen yet, for the step count): whether any size of the step does."""
        return self.has_mtp and self.experts_in_ram(ub)

    def experts_in_ram(self, ub: int = 0) -> bool:
        """A start at the setup this -ub allows keeps any expert layer in the RAM (ub 0: at any size of the batch size
        step)."""
        if self.ram_layers is None:
            return False
        ctx, slots = self.ub_rung(UBATCH)
        sizes = [ub] if ub else sorted({UBATCH, *self.ubs})
        return any(self.ram_layers(ctx, slots, u, Spec("ngram-mod", 1, 0, True)) > 0 for u in sizes)

    def pin_check(self, ub: int = 0) -> bool:
        """Auto-tune measures the threads pinned against unpinned at the chosen setup (3c): the CPU's topology is known
        and a start at this -ub keeps any expert layer in the RAM (ub 0: at any size, for the step count)."""
        return self.pin is not None and self.experts_in_ram(ub)

    @property
    def cpu(self) -> bool:
        """The tune is of a start on the CPU alone (A71): it measures the context memory types and reads up to 8K."""
        return self.kv_room is not None

    def kv_check(self) -> bool:
        """Auto-tune measures the context memory types (3d): the CPU alone."""
        return self.cpu

    def steps(self) -> int:
        # memory, [context memory type], modes, [batch size], [MTP with experts in the RAM], [threads with experts in
        # the RAM], reading, [parallel], result
        return (1 + (1 if self.kv_check() else 0) + len(self.modes()) + (1 if self.ubs else 0)
                + (1 if self.ram_check() else 0) + (1 if self.pin_check() else 0) + 2 + (0 if self.quick else 1))

    def fits(self, ctx: int, slots: int, ub: int, spec: Spec = NO_SPEC) -> bool:
        """Does a start of ctx tokens x slots at this -ub fit (room, else the need within limit)?"""
        if self.room is not None:
            return self.room(ctx, slots, ub, spec)
        return need_bytes(self.shape, self.weights, ctx, slots, self.kv, spec=spec, ub=ub) <= self.limit

    def kv_types(self, ctx: int, ub: int) -> List[str]:
        """The context memory types a start of one slot of ctx tokens at this -ub can use on the CPU alone: those the RAM
        covers (kv_room), fastest first; [] when this is not the CPU alone."""
        if self.kv_room is None:
            return []
        return [kv for kv in CPU_KV if self.kv_room(kv, ctx, 1, ub, NO_SPEC)]

    def largest(self, slots: int, ub: int) -> int:
        """The largest window per slot that fits at this -ub (a 4K multiple, at most the trained length; 0: not 4K)."""
        top = ctx_train(self.shape)
        if self.fits(top, slots, ub):
            return top
        lo, hi = 0, top // WINDOW_STEP
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.fits(mid * WINDOW_STEP, slots, ub):
                lo = mid
            else:
                hi = mid - 1
        return lo * WINDOW_STEP

    def ub_rung(self, ub: int) -> Tuple[int, int]:
        """(context per slot, slots) of the setup this -ub allows (ub_setup): the batch size step starts there."""
        top = ctx_train(self.shape)
        for tier in self.tiers:
            if tier.ctx <= top and self.fits(tier.ctx, tier.slots, ub):
                return tier.ctx, tier.slots
        return self.largest(1, ub), 1

    def ub_setup(self, ub: int) -> Tuple[Tuple[int, int], str]:
        """The setup this -ub allows: the first of Auto fit's order (2 x 96K, 2 x 64K, 2 x 48K, 1 x 96K, 1 x 64K,
        1 x 48K; on NVIDIA from 128K; within the model's trained length) that fits, else the largest window of one
        slot. (a key that compares them, the setup in words)."""
        top = ctx_train(self.shape)
        for i, tier in enumerate(self.tiers):
            if tier.ctx <= top and self.fits(tier.ctx, tier.slots, ub):
                return (i, 0), tier.label()
        one = self.largest(1, ub)
        return (len(self.tiers), one), f"1 slot × {one // 1024}K tokens"


def write_ok(decode: Mapping[int, float]) -> List[int]:
    """The batch sizes whose write speed is within UB_DECODE_NOISE of the best one (A46); [] when none was measured."""
    if not decode:
        return []
    top = max(decode.values())
    return sorted(ub for ub, tg in decode.items() if tg >= top * (1 - UB_DECODE_NOISE))


def best_ub(speeds: Mapping[int, float], base: int = UBATCH, decode: Optional[Mapping[int, float]] = None) -> int:
    """The batch size from the prompt read at each (tok/s) and, when measured, the write speed at each (decode). The
    context came first (every size measured keeps the setup). A size that writes more than UB_DECODE_NOISE under the
    best one is out (A46); of the rest the fastest read wins when it beats `base` (the default) by UB_MARGIN, else
    base. When base is out or was not measured, the size that writes fastest (else reads fastest) takes its place."""
    if not speeds:
        return base
    tg = decode or {}
    ok = [ub for ub in write_ok(tg) if ub in speeds] or list(speeds)
    if base not in ok:
        base = max(ok, key=lambda ub: tg.get(ub, 0.0)) if tg else max(ok, key=lambda ub: speeds[ub])
    fast = max(ok, key=lambda ub: speeds[ub])
    return fast if speeds[fast] > speeds[base] * UB_MARGIN else base


def ram_spec_words(ngram: float, mtp: float, mode: Mode, kept: bool, ctx: int, slots: int, layers: int) -> str:
    """The MTP-with-experts-in-the-RAM step's note (the record's spec_note, one line): both write speeds at the chosen
    setup, the verdict and why. mode: the MTP mode measured; kept: it won."""
    setup = (f"{slots} slot{'s' if slots != 1 else ''} × {ctx // 1024}K tokens with the experts of {layers} "
             f"layer{'s' if layers != 1 else ''} in the RAM")
    head = f"At {setup}: n-gram {ngram} tok/s, {spec_words(*mode)} {mtp} tok/s. "
    if kept:
        return head + f"MTP is kept for this model: it writes {round((mtp / ngram - 1) * 100)}% faster."
    return head + (f"MTP is off for this model: it must write {round((DRAFT_MARGIN - 1) * 100)}% faster than n-gram to "
                   f"pay for its draft context.")


def mean_result(results: Sequence[ModeResult]) -> ModeResult:
    """The average of several runs of one mode: each workload's mean speed, and the weighted score of those."""
    speeds = {k: round(sum(r[k] for r in results) / len(results), 1)  # type: ignore[literal-required]
              for k in WEIGHTS}
    return {"prose": speeds["prose"], "code": speeds["code"], "edit": speeds["edit"],
            "score": weighted_score(speeds)}


def pin_words(pinned: float, unpinned: float, kept: bool, threads: int, ctx: int, slots: int, layers: int) -> str:
    """The threads step's note (the record's pin_note, one line): both write speeds at the chosen setup, the verdict and
    why. kept: the pinned threads are kept (pinned wrote faster by DRAFT_MARGIN)."""
    setup = (f"{slots} slot{'s' if slots != 1 else ''} × {ctx // 1024}K tokens with the experts of {layers} "
             f"layer{'s' if layers != 1 else ''} in the RAM")
    head = (f"At {setup}: {threads} threads pinned one per core {pinned} tok/s, unpinned {unpinned} tok/s "
            f"(the mean of {PIN_ROUNDS} runs each). ")
    if kept:
        return head + f"Pinned is kept for this model: it writes {round((pinned / unpinned - 1) * 100)}% faster."
    if unpinned > pinned:
        return head + f"Unpinned is kept for this model: it writes {round((unpinned / pinned - 1) * 100)}% faster."
    return head + (f"Unpinned is kept for this model: pinned must write {round((DRAFT_MARGIN - 1) * 100)}% faster "
                   "to be worth it.")


def ub_words(speeds: Mapping[int, float], chosen: int, base: int, held: str = "",
             decode: Optional[Mapping[int, float]] = None, default: Optional[int] = None) -> str:
    """The batch size step's note in one line (the record's ub_note): each size's read speed (and write speed), the
    -ub saved for the model and why; held: why larger sizes were not tried (they cost the setup). default: this
    computer's default -ub (None: base is it); a base below it (lowered for the setup) is not called the default."""
    tg = decode or {}
    named = f"the default {base}" if default in (None, base) else f"-ub {base}"
    reads = ", ".join(f"-ub {ub} {tps:.0f}" + (f" / {tg[ub]:.1f}" if ub in tg else "") + " tok/s"
                      for ub, tps in sorted(speeds.items()))
    head = f"{'Prompt reading / writing' if tg else 'Prompt reading'} by batch size: {reads}. " if reads else ""
    ok = write_ok(tg)
    slow = [ub for ub in sorted(tg) if ub not in ok]
    out = (f"-ub {', '.join(str(ub) for ub in slow)} {'writes' if len(slow) == 1 else 'write'} more than "
           f"{round(UB_DECODE_NOISE * 100)}% slower than the best: not chosen. " if slow else "")
    if chosen != base and base in slow:
        why = f"-ub {chosen} is saved for this model: it writes about as fast as the best and reads fastest of those."
    elif chosen != base and speeds.get(base):
        gain = round((speeds[chosen] / speeds[base] - 1) * 100)
        why = f"-ub {chosen} is saved for this model: it reads {gain}% faster than {named}."
    elif len(speeds) > 1:
        why = (f"-ub {chosen} is saved for this model: no size reads {round((UB_MARGIN - 1) * 100)}% faster than "
               f"{named if chosen == base else chosen}.")
    else:
        why = f"-ub {chosen} is saved for this model."
    return head + out + why + (f" {held}" if held else "")


def best_kv(reads: Mapping[str, float], writes: Mapping[str, float]) -> str:
    """The context memory type for a start on the CPU alone from the prompt read and the write speed of each type
    measured (tok/s; A71). The types go in the order of CPU_KV, f16 first, the best quality and on the CPU the fastest to
    read: a later type replaces the one before it only when it reads or writes faster by DRAFT_MARGIN, the usual margin,
    and does not read or write more than UB_DECODE_NOISE slower. Nothing measured: TUNE_KV."""
    order = [kv for kv in CPU_KV if kv in reads]
    if not order:
        return TUNE_KV
    best = order[0]
    for kv in order[1:]:
        r = reads[kv] / reads[best] if reads[best] > 0 else 1.0
        w = writes[kv] / writes[best] if writes.get(kv) and writes.get(best) else 1.0
        if (r > DRAFT_MARGIN or w > DRAFT_MARGIN) and min(r, w) >= 1 - UB_DECODE_NOISE:
            best = kv
    return best


def kv_results(reads: Mapping[str, float], writes: Mapping[str, float]) -> Dict[str, KvResult]:
    """The record's results.kv: the prompt read and the write speed of each type measured (0 write: not measured)."""
    return {kv: {"read": round(reads[kv], 1), "write": round(writes.get(kv, 0.0), 1)} for kv in CPU_KV if kv in reads}


def kv_words(reads: Mapping[str, float], writes: Mapping[str, float], chosen: str, skipped: Sequence[str] = (),
             failed: Sequence[str] = ()) -> str:
    """The context memory type step's note (the record's kv_note, one line): each type's read and write speed on the
    CPU, the types the RAM did not cover, the types the server did not start with, the type saved for the model and why.
    With no type measured (one type fits the RAM, or none started) nothing is saved as a choice: a start picks the type
    itself (the record has no results.kv, so FitService.cpu_kv_open leaves it open)."""
    shown = [kv for kv in CPU_KV if kv in reads]
    rows = ", ".join(f"{kv_type_name(kv)} {reads[kv]:.0f}" + (f" / {writes[kv]:.1f}" if writes.get(kv) else "") + " tok/s"
                     for kv in shown)
    head = f"Prompt reading / writing by context memory type: {rows}. " if rows else ""
    head += (f"Not tried, the RAM does not cover {', '.join(kv_type_name(kv) for kv in skipped)}. " if skipped else "")
    name = kv_type_name(chosen)
    if failed:
        head += f"The server did not start with {', '.join(kv_type_name(kv) for kv in failed)}. "
    if not shown:
        if failed:
            return head + f"Nothing was measured: {name} is kept for this tune, and a start picks the type itself."
        return head + f"{name} is the only type that fits the RAM, so nothing was measured: a start picks the type itself."
    if len(shown) < 2:
        return head + f"{name} is saved for this model: it is the only type that was measured."
    first = shown[0]
    if chosen == first:
        return head + (f"{name} is saved for this model: no other type reads or writes "
                       f"{round((DRAFT_MARGIN - 1) * 100)}% faster.")
    gain = round((reads[chosen] / reads[first] - 1) * 100) if reads[first] > 0 else 0
    if reads[chosen] > reads[first] * DRAFT_MARGIN:
        return head + f"{name} is saved for this model: it reads {gain}% faster than {kv_type_name(first)}."
    gain = round((writes[chosen] / writes[first] - 1) * 100) if writes.get(first) else 0
    return head + f"{name} is saved for this model: it writes {gain}% faster than {kv_type_name(first)}."


def read_estimates(reads: Sequence[Read], depths: Sequence[int]) -> List[Read]:
    """The cold reads at these depths (tokens) as the slope of the measured ones gives them: (tokens, tok/s over the
    whole read; the fit of zones_from_reads)."""
    return [(d, d / read_seconds(reads, d)) for d in depths]


def estimate_words(measured: Sequence[Read], estimates: Sequence[Read]) -> str:
    """The CPU's read step note (the record's read_note): the reads stop at the deepest measured one, and the longer ones
    are an estimate, said as one."""
    top = round(max(n for n, _ in measured) / 1024)

    def took(seconds: float) -> str:
        return f"{seconds:.0f} s" if seconds < 120 else f"{seconds / 60:.0f} min"
    if not estimates:
        return f"The CPU reads up to {top}K tokens here."
    longer = ", ".join(f"{d // 1024}K tokens {tps:.0f} tok/s ({took(d / tps)})" for d, tps in estimates)
    return (f"The CPU reads up to {top}K tokens here. The longer reads are an estimate from the slope of these: "
            f"{longer}.")


KV_WORDS = {"q4_0": "q4", "q8_0": "q8"}
_SPEC_WORDS = {"none": "none", "ngram-mod": "n-gram", "draft-mtp": "MTP", "draft-mtp,ngram-mod": "MTP + n-gram",
               "draft-dflash": "DFlash", "draft-dspark": "DSpark", "draft-eagle3": "EAGLE-3",
               "draft-dflash,ngram-mod": "DFlash + n-gram", "draft-dspark,ngram-mod": "DSpark + n-gram",
               "draft-eagle3,ngram-mod": "EAGLE-3 + n-gram"}


# The run's texts that name the computer take the platform's words.
def no_fit(words: PlatformWords = MACOS, cpu: bool = False) -> str:
    """The error when not even a 16K context fits."""
    if cpu:
        return f"the model does not fit the RAM of {words.machine} with a context of 16K tokens"
    return f"the model does not fit the GPU memory of {words.machine} with a context of 16K tokens"


def not_measured_note(depth: int, max_one_slot: int, words: PlatformWords = MACOS) -> str:
    """The note when the reads stop at a depth the largest context does not reach."""
    return (f"{depth // 1024}K tokens and more: not measured. The largest context that fits "
            f"{words.machine} is {max_one_slot // 1024}K tokens.")


def spec_words(spec: str, n: int) -> str:
    """A speculation mode in the glossary's words: MTP + n-gram, 2 guesses (the guesses are the drafter's: n-gram has
    none)."""
    name = _SPEC_WORDS.get(spec, spec)
    return name if "draft-" not in spec else f"{name}, {n} guess{'' if n == 1 else 'es'}"
