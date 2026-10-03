"""Auto-tune: measure one model on this Mac and choose its speculation, context and slots.

Steps (the model loads once per speculation mode):
  1. memory: the largest window that fits with 1 and 2 slots
  2. speculation: none, n-gram, and (with an MTP head) MTP and MTP + n-gram at n = 1 and 2.
     Each runs prose, fresh code and a code re-emit, twice. Score = weighted geometric mean
     (prose 0.4, code 0.4, re-emit 0.2); a mode with drafting must beat a simpler one by 3%.
  3. prompt reading: cold reads at 8K, 32K and 64K tokens (quick: 8K and 32K; long: also
     128K and 192K, as far as the model's window fits) give this Mac's context zones: a cold
     re-read of a full window within 3 min = fast, 10 min = slow, beyond = very slow. Long
     also records the decode speed at each depth (the reply to the read).
Modes: quick (fewer speculation modes, shallower reads, ~4 min), default, long (deep reads:
a 192K cold read alone takes 10-40 min; the time is estimated before those reads start).
  4. result: kv q4_0, the best speculation, the context (see choose_ctx) and 2 slots when
     two windows fit.
"""
from __future__ import annotations

import math
import os
import random
import re
from dataclasses import dataclass
from typing import Dict, List, Literal, Mapping, Optional, Sequence, Tuple

from .errors import ConfigError
from .fit import max_ctx, need_bytes
from .gguf import ModelShape
from .models import CTX_FLOOR
from .ports import Progress, TuneServer
from .types import CtxZones, JsonObject, ModeResult, TuneRecord, TuneResults

Mode = Tuple[str, int]                     # (speculation, draft n)
Read = Tuple[int, float]                   # (prompt tokens, cold read tok/s)
Depth = Literal["quick", "default", "long"]
DEPTHS: Tuple[Depth, ...] = ("quick", "default", "long")
DEPTH_TEXT: Dict[Depth, str] = {
    "quick": "quick: the MTP modes with 1 draft, reads at 8K and 32K (~4 min)",
    "default": "default: every speculation mode, reads at 8K, 32K and 64K (~5-10 min)",
    "long": "long: the default, then reads at 128K and 192K and the decode speed at each depth (+10-40 min)"}

TUNE_KV = "q4_0"
MIN_WINDOW = 16384                         # below this the model is not usable here
TEST_CTX, TEST_CTX_QUICK = 69632, 36864    # the window benchmarks run with (64K / 32K + room)
LONG_READ_CTX = 200704                     # long mode's reads: 192K + room
READ_ROOM = 2048                           # a read needs this much window beyond its prompt
LONG_DECODE_TOKENS = 64                    # long mode: tokens generated after each read (decode at depth)
STANDARD_WINDOWS = (32768, 49152, 65536, 98304, 131072, 163840)
FALLBACK_WINDOW = 32768
ZONE_SECONDS = {"good": 180, "slow": 600, "very_slow": 1200}   # cold read of a full window
ZONE_MAX = 262144
DRAFT_MARGIN = 1.03                        # a drafting mode must win by 3% (simpler is steadier)
WEIGHTS = {"prose": 0.4, "code": 0.4, "edit": 0.2}
RUNS_PER_WORKLOAD = 2
FILLER_WORDS = ("system memory cache token window model server request decode prompt layer tensor kernel metal quant "
                "expert router batch slot context speculative draft accept reject verify stream session agent tool").split()
EDIT_SOURCE_CHARS = 9000
GEN_TIMEOUT, READ_TIMEOUT = 1800.0, 3600.0     # seconds: a 64K cold read on a slow Mac takes minutes


def as_depth(v: object) -> Depth:
    """A tune mode from the command line or the dashboard (anything else: default)."""
    return "quick" if v == "quick" else "long" if v == "long" else "default"


def speculation_modes(has_mtp: bool, quick: bool) -> List[Mode]:
    """The modes to measure, simplest first (best_mode prefers earlier ones)."""
    modes: List[Mode] = [("none", 1), ("ngram-mod", 2)]
    if has_mtp:
        modes += [("draft-mtp", 1), ("draft-mtp,ngram-mod", 1)]
        if not quick:
            modes += [("draft-mtp", 2), ("draft-mtp,ngram-mod", 2)]
    return modes


def read_depths(depth: Depth) -> List[int]:
    """The cold-read depths of a tune mode (tokens)."""
    return {"quick": [8192, 32768], "default": [8192, 32768, 65536],
            "long": [8192, 32768, 65536, 131072, 196608]}[depth]


def read_ctx(depth: Depth, max_one_slot: int) -> int:
    """The window the reading server runs with: the deepest read's, at most what fits."""
    want = LONG_READ_CTX if depth == "long" else TEST_CTX_QUICK if depth == "quick" else TEST_CTX
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


def filler_text(tokens: int) -> str:
    """A prompt of roughly `tokens` tokens that no cache has seen (fixed seed: repeatable)."""
    rnd = random.Random(tokens)
    return "\n".join(f"Record {i}: " + " ".join(rnd.choice(FILLER_WORDS) for _ in range(12))
                     + f" value={rnd.randint(0, 10 ** 6)}." for i in range(tokens // 18))


def chat_body(prompt: str, max_tokens: int) -> JsonObject:
    return {"model": "x", "max_tokens": max_tokens, "temperature": 1.0, "top_p": 0.95, "top_k": 20,
            "chat_template_kwargs": {"enable_thinking": False}, "cache_prompt": False,
            "messages": [{"role": "user", "content": prompt}]}


def read_body(text: str, max_tokens: int = 1) -> JsonObject:
    return {"model": "x", "max_tokens": max_tokens, "cache_prompt": False,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": text + ("\n\nReply with OK." if max_tokens == 1 else
                                                             "\n\nSummarise these records in a long paragraph.")}]}



def bench_mode(server: TuneServer, prompts: Mapping[str, str]) -> ModeResult:
    """Decode speed of the running server on each workload (after a warm-up)."""
    server.timings(chat_body("Say hello.", 16), GEN_TIMEOUT)
    speeds: Dict[str, float] = {}
    for name, prompt in prompts.items():
        vals = [server.timings(chat_body(prompt, 256), GEN_TIMEOUT).get("predicted_per_second", 0.0)
                for _ in range(RUNS_PER_WORKLOAD)]
        speeds[name] = round(sum(vals) / len(vals), 1)
    return {"prose": speeds["prose"], "code": speeds["code"], "edit": speeds["edit"], "score": weighted_score(speeds)}


def cold_read(server: TuneServer, tokens: int, decode: int = 0) -> Tuple[Read, Optional[float]]:
    """((tokens read, tok/s), decode tok/s at that depth or None) of a cold read of a prompt
    of about `tokens` tokens; decode > 0 also generates that many tokens after it."""
    text = filler_text(tokens)
    n = server.count_tokens(text)
    if n and n > tokens:
        text = text[: int(len(text) * tokens / n)]
    t = server.timings(read_body(text, max(decode, 1)), READ_TIMEOUT * (3 if tokens > 65536 else 1))
    if "prompt_n" not in t or "prompt_per_second" not in t:
        raise ConfigError("the server's answer has no prompt timings")
    tg = t.get("predicted_per_second") if decode and t.get("predicted_n", 0) >= decode // 2 else None
    return (int(t["prompt_n"]), t["prompt_per_second"]), (round(tg, 1) if tg else None)


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

    @property
    def has_mtp(self) -> bool:
        return bool(self.shape["nextn"])

    @property
    def quick(self) -> bool:
        return self.depth == "quick"

    def modes(self) -> List[Mode]:
        return speculation_modes(self.has_mtp, self.quick)

    def steps(self) -> int:
        return 1 + len(self.modes()) + 2            # memory, each mode, prompt reading, result


class AutoTuner:
    """Runs a tune through a TuneServer and reports progress; returns the record to save."""

    def __init__(self, server: TuneServer, progress: Progress) -> None:
        self.server = server
        self.progress = progress
        self._step = 0
        self._steps = 0

    def _next(self, text: str) -> None:
        self._step += 1
        self.progress.step(f"{self._step}/{self._steps} {text}")

    def run(self, plan: TunePlan) -> TuneRecord:
        self._step, self._steps = 0, plan.steps()
        shape, weights, limit = plan.shape, plan.weights, plan.limit

        self._next("memory")
        m1, m2 = max_ctx(shape, weights, limit, 1, TUNE_KV), max_ctx(shape, weights, limit, 2, TUNE_KV)
        self.progress.note(f"GPU limit {limit / 2 ** 30:.1f} GiB; largest window per slot: "
                           f"1 slot {m1 // 1024}K, 2 slots {m2 // 1024}K")
        if m1 < MIN_WINDOW:
            raise ConfigError("the model doesn't fit this Mac's GPU memory with a 16K window")

        test_ctx = min(TEST_CTX_QUICK if plan.quick else TEST_CTX, m1)
        prompts = workloads(plan.edit_source)
        results: Dict[str, ModeResult] = {}
        for mode in plan.modes():
            self._next(f"speculation {mode[0]} n={mode[1]}")
            load = self.server.start(mode[0], mode[1], test_ctx)
            r = results[mode_key(mode)] = bench_mode(self.server, prompts)
            self.progress.note(f"loaded in {load:.0f}s · prose {r['prose']} · code {r['code']} · "
                               f"re-emit {r['edit']} tok/s · score {r['score']}")
        spec, n = best = best_mode(results)
        first = next(iter(results.values()))
        self.progress.note(f"best: {spec} n={n} (score {results[mode_key(best)]['score']} vs none {first['score']})")

        self._next("prompt reading")
        rctx = read_ctx(plan.depth, m1)
        self.server.start(spec, n, rctx)
        reads: List[Read] = []
        decodes: List[Tuple[int, float]] = []
        long = plan.depth == "long"
        depths = read_depths(plan.depth)
        for i, depth in enumerate(depths):
            if depth + READ_ROOM > rctx:
                self.progress.note(f"{depth // 1024}K and deeper: skipped (the largest window that fits this Mac "
                                   f"is {m1 // 1024}K)")
                break
            if long and depth > 65536 and depth == depths[3] and len(reads) >= 2:
                left = sum(read_seconds(reads, d) for d in depths[i:] if d + READ_ROOM <= rctx)
                self.progress.note(f"the deep reads ({', '.join(f'{d // 1024}K' for d in depths[i:] if d + READ_ROOM <= rctx)}) "
                                   f"take about {left / 60:.0f} min (estimated from the reads so far)")
            (got, tps), tg = cold_read(self.server, depth, LONG_DECODE_TOKENS if long else 0)
            reads.append((got, tps))
            if tg is not None:
                decodes.append((got, tg))
            self.progress.note(f"{got} tokens read cold at {tps:.0f} tok/s ({got / tps:.0f} s)"
                               + (f" · then decodes at {tg} tok/s" if tg is not None else ""))
        self.server.stop()
        zones = zones_from_reads(reads)
        self.progress.note(f"context zones (cold read of a full window): fast to {zones['good'] // 1024}K (3 min), "
                           f"slow to {zones['slow'] // 1024}K (10 min), very slow to {zones['very_slow'] // 1024}K (20 min)")

        ctx = choose_ctx(plan.base_ctx, zones, m1)
        slots = "2" if need_bytes(shape, weights, ctx, 2, TUNE_KV) <= limit else "1"
        self._next("result")
        self.progress.note(f"kv {TUNE_KV} · speculation {spec} n={n} · context {ctx // 1024}K per slot · {slots} slot(s)")
        results_out: TuneResults = {"speculation": results, "prompt_read": [[g, t] for g, t in reads]}
        if decodes:
            results_out["decode_at_depth"] = [[g, t] for g, t in decodes]
        return {"date": plan.date, "machine": plan.machine, "llama_cpp": plan.llama_cpp,
                "settings": {"kv": TUNE_KV, "spec": spec, "spec_n": n, "ctx": ctx, "slots": slots},
                "ctx_zones": zones,
                "results": results_out,
                "depth": plan.depth,
                "max_ctx": {"1": m1, "2": m2}}


# ---------------------------------------------------------------- the GPU-to-itself guard
# mtplx stays although CARL no longer starts it: a leftover MTPLX server still holds a model.
_MODEL_SERVER = re.compile(r"(^|/)(llama-server|mtplx|ollama|LM Studio)")


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    rss_kib: int
    command: str


def blocking_processes(procs: Sequence[ProcessInfo], big_kib: int, own_pid: int) -> List[str]:
    """Processes that probably hold a model (over big_kib resident, or a known model
    server): a second model can crash the Mac. Like host/common.sh guard_other_models."""
    return [f"pid {p.pid} ({os.path.basename(p.command)}, {p.rss_kib / 2 ** 20:.1f} GB)"
            for p in procs if p.pid != own_pid and (p.rss_kib > big_kib or _MODEL_SERVER.search(p.command))]
