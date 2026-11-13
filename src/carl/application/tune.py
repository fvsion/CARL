"""Auto-tune's run: measure one model on this computer through a TuneServer and choose its speculation, context
and slots (the steps and the scoring: domain/tune/scoring.py). carl tune (cli/commands/tune.py) runs it with the
llama.cpp adapter (adapters/llama/tune_control.py); the tests run it with a fake server.
"""
from __future__ import annotations

import random
from dataclasses import replace
from typing import Dict, List, Mapping, Optional, Tuple

from ..domain.catalog.inventory import pin_kept
from ..domain.memory.fit import Spec
from ..domain.shared.constants import UBATCH
from ..domain.shared.contracts import ensure, require
from ..domain.shared.errors import ConfigError
from ..domain.shared.platform_words import MACOS, PlatformWords
from ..domain.memory.cpu_kv import CPU_KV
from ..domain.shared.types import KvResult, ModeResult, TuneRecord, TuneResults
from ..domain.tune.scoring import (FILLER_WORDS, GEN_TIMEOUT, KV_WORDS, LONG_DECODE_TOKENS, MIN_WINDOW, no_fit,
                                   PARALLEL_COUNTS, PARALLEL_GEN, PARALLEL_PROMPT, READ_ROOM, READ_TIMEOUT,
                                   RUNS_PER_WORKLOAD, TEST_CTX, TEST_CTX_QUICK, UB_DECODE, UB_PROMPT, Read,
                                   DRAFT_MARGIN, TunePlan, best_kv, best_mode, best_ub, chat_body, choose_ctx,
                                   estimate_words, kv_results, kv_words, mode_key, PIN_ROUNDS, mean_result,
                                   not_measured_note, pin_words, ram_spec_words, read_body, read_ctx, read_depths,
                                   read_estimates, read_seconds, spec_words, ub_words, weighted_score, workloads,
                                   zones_from_reads)
from ..ports import Progress, TuneServer


def filler_text(tokens: int) -> str:
    """A prompt of roughly `tokens` tokens that no cache has seen (fixed seed: repeatable)."""
    rnd = random.Random(tokens)
    return "\n".join(f"Record {i}: " + " ".join(rnd.choice(FILLER_WORDS) for _ in range(12))
                     + f" value={rnd.randint(0, 10 ** 6)}." for i in range(tokens // 18))


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


class AutoTuner:
    """Runs a tune through a TuneServer and reports progress; returns the record to save. Contract C-112."""

    def _ram_speculation(self, plan: TunePlan, prompts: Mapping[str, str], results: Mapping[str, ModeResult],
                         best: Tuple[str, int], ub: int) -> Tuple[str, int, Dict[str, ModeResult], str]:
        """A model with an MTP head and experts in the RAM at the chosen setup (decision A51): n-gram and the best MTP
        mode, measured there at the chosen -ub (prose, code, an edit). MTP is kept only when it beats n-gram by
        DRAFT_MARGIN, the usual margin; otherwise the best speculation without MTP is. -> (the speculation, its n, the
        two results, the record's note)."""
        self._next("speculation with the experts in the RAM")
        ctx, slots = plan.ub_rung(UBATCH)
        ram = plan.ram_layers
        assert ram is not None
        layers = ram(ctx, slots, ub, Spec("ngram-mod", 1, 0, True))
        mtp_keys = [k for k in results if "draft-mtp" in k]
        top = max(mtp_keys, key=lambda k: results[k]["score"])
        mtp = (top.rsplit(":", 1)[0], int(top.rsplit(":", 1)[1]))
        gram = ("ngram-mod", 1)
        got: Dict[str, ModeResult] = {}
        for mode in (gram, mtp):
            self.server.start(mode[0], mode[1], ctx, ub, slots, kv=plan.kv)
            got[mode_key(mode)] = bench_mode(self.server, prompts)
        g, m = got[mode_key(gram)]["score"], got[mode_key(mtp)]["score"]
        win = m > g * DRAFT_MARGIN
        plain = {k: v for k, v in results.items() if k not in mtp_keys}
        spec, n = mtp if win else best_mode(plain) if "draft-mtp" in best[0] else best
        note = ram_spec_words(g, m, mtp, win, ctx, slots, layers)
        self.progress.note(note)
        return spec, n, got, note

    def _threads(self, plan: TunePlan, prompts: Mapping[str, str], spec: str, n: int, ub: int
                 ) -> Tuple[bool, Dict[str, ModeResult], str]:
        """The threads with experts in the RAM at the chosen setup (decision A54): the speculation and -ub chosen, run
        pinned one per physical core and unpinned, each measured on prose, code and an edit, PIN_ROUNDS times and
        alternating (pinned, unpinned, unpinned, pinned: one run is noisy, and a drift in the machine favours neither
        side). Pinned is kept only when it writes faster by DRAFT_MARGIN, the usual margin: the start pins only when
        the record says so (A57). -> (pinned kept, the two averaged results, the record's note)."""
        self._next("threads with the experts in the RAM")
        ctx, slots = plan.ub_rung(UBATCH)
        ram, pin = plan.ram_layers, plan.pin
        assert ram is not None and pin is not None
        layers = ram(ctx, slots, ub, Spec("ngram-mod", 1, 0, True))
        runs: Dict[str, List[ModeResult]] = {"pinned": [], "unpinned": []}
        for r in range(PIN_ROUNDS):
            for pinned in (True, False) if r % 2 == 0 else (False, True):
                self.server.start(spec, n, ctx, ub, slots, pin=pinned, kv=plan.kv)
                runs["pinned" if pinned else "unpinned"].append(bench_mode(self.server, prompts))
        got = {k: mean_result(v) for k, v in runs.items()}
        p, u = got["pinned"]["score"], got["unpinned"]["score"]
        kept = pin_kept(p, u)
        note = pin_words(p, u, kept, pin.threads, ctx, slots, layers)
        self.progress.note(note)
        return kept, got, note

    def _kv(self, plan: TunePlan) -> Tuple[TunePlan, Dict[str, KvResult], str]:
        """The context memory type on the CPU alone (A71): each type the RAM covers (f16, q8_0, q4_0) starts the model at its
        -ub and window with no speculation, reads a prompt cold, then writes: the type that reads or writes faster by
        DRAFT_MARGIN without losing on the other (scoring.best_kv) is kept, and every later step runs with it. One type
        that fits is taken as it is. -> (the plan with the type, the results of each type measured, the record's note)."""
        self._next("context memory type")
        ub = plan.ub_default
        ctx = min(TEST_CTX_QUICK if plan.quick else TEST_CTX, plan.largest(1, ub))
        fit = plan.kv_types(ctx, ub)
        skipped = [kv for kv in CPU_KV if kv not in fit]
        if not fit:                                  # the RAM covers none of them: the memory step's q4_0 stands
            note = (f"Not tried: the RAM does not cover a start of {ctx // 1024}K tokens with any context memory type. "
                    f"{KV_WORDS.get(plan.kv, plan.kv)} is kept.")
            self.progress.note(note)
            return plan, {}, note
        reads: Dict[str, float] = {}
        writes: Dict[str, float] = {}
        failed: List[str] = []
        for kv in fit if len(fit) > 1 else []:
            try:
                self.server.start("none", 1, ctx, ub, kv=kv)
            except ConfigError as e:                 # a type the server cannot start with: the others still count
                self.progress.note(f"{KV_WORDS.get(kv, kv)}: the server did not start ({e}).")
                failed.append(kv)
                continue
            (_, tps), tg = cold_read(self.server, UB_PROMPT, UB_DECODE)
            reads[kv] = tps
            if tg is not None:
                writes[kv] = tg
        # nothing measured: the one type that fits, or (every start failed) the memory step's type, never an untried f16
        kv = best_kv(reads, writes) if reads else plan.kv if failed else fit[0]
        note = kv_words(reads, writes, kv, skipped, failed)
        self.progress.note(note)
        chosen = replace(plan, kv=kv)
        if kv != plan.kv:                            # the window the later steps can use changes with the type
            self.progress.note(f"With {KV_WORDS.get(kv, kv)} the largest context per slot: "
                               f"{chosen.largest(1, UBATCH) // 1024}K tokens with 1 slot, "
                               f"{chosen.largest(2, UBATCH) // 1024}K tokens with 2 slots.")
        return chosen, kv_results(reads, writes), note

    def __init__(self, server: TuneServer, progress: Progress, words: PlatformWords = MACOS) -> None:
        self.server = server
        self.progress = progress
        self.words = words                  # the platform words of the notes (the composition's)
        self._step = 0
        self._steps = 0

    def _next(self, text: str) -> None:
        self._step += 1
        self.progress.step(f"{self._step}/{self._steps} {text}")

    def run(self, plan: TunePlan) -> TuneRecord:
        require(plan.depth in ("quick", "default", "long"), "C-112", "the depth is quick, default or long")
        self._step, self._steps = 0, plan.steps()

        self._next("memory")
        # the room at the smallest -ub (the most): the setup every -ub of the batch size step has to keep (A45)
        m1, m2 = plan.largest(1, UBATCH), plan.largest(2, UBATCH)
        self.progress.note(f"The {'RAM' if plan.cpu else 'GPU memory'} limit is {plan.limit / 2 ** 30:.1f} GiB. The largest "
                           f"context per slot: {m1 // 1024}K tokens with 1 slot, {m2 // 1024}K tokens with 2 slots.")
        for note in plan.unused:                     # a downloaded drafter whose kind the engine does not support (A74)
            self.progress.note(note)
        if m1 < MIN_WINDOW:
            raise ConfigError(no_fit(self.words, plan.cpu))
        kv_res: Dict[str, KvResult] = {}
        kv_note = ""
        if plan.kv_check():                          # the CPU alone: the context memory type, for every step after
            plan, kv_res, kv_note = self._kv(plan)
            m1, m2 = plan.largest(1, UBATCH), plan.largest(2, UBATCH)
        kv = plan.kv
        ref, ref_words = plan.ub_setup(UBATCH)
        sizes = sorted(set(plan.ubs))
        holding = [ub for ub in sizes if ub <= UBATCH or plan.ub_setup(ub)[0] == ref]
        lost = [ub for ub in sizes if ub not in holding]
        base = max([ub for ub in holding if ub <= plan.ub_default] or [min(holding or [UBATCH])])
        held = (f"-ub {', '.join(str(ub) for ub in lost)} would make the setup {plan.ub_setup(lost[0])[1]}, not "
                f"{ref_words}: not tried." if lost else "")

        test_ctx = min(TEST_CTX_QUICK if plan.quick else TEST_CTX, m1)
        prompts = workloads(plan.edit_source)
        results: Dict[str, ModeResult] = {}
        for mode in plan.modes():
            self._next(f"speculation {spec_words(mode[0], mode[1])}")
            load = self.server.start(mode[0], mode[1], test_ctx, base, kv=kv)
            r = results[mode_key(mode)] = bench_mode(self.server, prompts)
            self.progress.note(f"Loaded in {load:.0f} s. Write speed: prose {r['prose']}, code {r['code']}, "
                               f"edit {r['edit']} tok/s. Weighted speed: {r['score']} tok/s.")
        spec, n = best = best_mode(results)
        first = next(iter(results.values()))
        self.progress.note(f"The best speculation is {spec_words(spec, n)}: {results[mode_key(best)]['score']} tok/s "
                           f"(none: {first['score']} tok/s).")

        by_ub: List[List[float]] = []
        ub = base
        if plan.ubs:
            self._next("batch size")
            # each size starts at the setup they all keep (A46: its slots and context, so a MoE model has the experts
            # in the RAM it will run with), reads a prompt, then writes: the write speed first, then the read
            rung_ctx, rung_slots = plan.ub_rung(UBATCH)
            speeds: Dict[int, float] = {}
            writes: Dict[int, float] = {}
            for size in holding if len(holding) > 1 else []:
                try:
                    self.server.start(spec, n, rung_ctx, size, rung_slots, kv=kv)
                except ConfigError as e:             # a size the server cannot start with: the others still count
                    self.progress.note(f"-ub {size}: the server did not start ({e}).")
                    continue
                (_, tps), tg = cold_read(self.server, UB_PROMPT, UB_DECODE)
                speeds[size] = tps
                if tg is not None:
                    writes[size] = tg
                by_ub.append([size, round(tps, 1), tg or 0.0])
            ub = best_ub(speeds, base, writes)
            ub_note = ub_words(speeds, ub, base, held, writes, plan.ub_default)
            self.progress.note(ub_note)
        else:
            ub_note = ""

        ram_results: Dict[str, ModeResult] = {}
        spec_note = ""
        if plan.ram_check(ub):
            spec, n, ram_results, spec_note = self._ram_speculation(plan, prompts, results, (spec, n), ub)
        elif plan.ram_check():
            self._steps -= 1                    # a size the step did not choose would have kept experts in the RAM

        pinned: Optional[bool] = None       # the threads the later starts use (None: the start's own rule)
        pin_results: Dict[str, ModeResult] = {}
        pin_note = ""
        if plan.pin_check(ub):
            pinned, pin_results, pin_note = self._threads(plan, prompts, spec, n, ub)
        elif plan.pin_check():
            self._steps -= 1                    # a size the step did not choose would have kept experts in the RAM

        self._next("read speed")
        rctx = read_ctx(plan.depth, plan.largest(1, ub), plan.cpu)
        self.server.start(spec, n, rctx, ub, pin=pinned, kv=kv)
        load_mode = self.server.load_mode()
        reads: List[Read] = []
        decodes: List[Tuple[int, float]] = []
        long = plan.depth == "long"
        depths = read_depths(plan.depth, plan.cpu)
        for i, depth in enumerate(depths):
            if depth + READ_ROOM > rctx:
                self.progress.note(not_measured_note(depth, m1, self.words))
                break
            if long and depth > 65536 and depth == depths[3] and len(reads) >= 2:
                left = sum(read_seconds(reads, d) for d in depths[i:] if d + READ_ROOM <= rctx)
                self.progress.note(f"The long reads ({', '.join(f'{d // 1024}K' for d in depths[i:] if d + READ_ROOM <= rctx)}) "
                                   f"take about {left / 60:.0f} min (an estimate from the reads so far).")
            (got, tps), tg = cold_read(self.server, depth, LONG_DECODE_TOKENS if long else 0)
            reads.append((got, tps))
            if tg is not None:
                decodes.append((got, tg))
            self.progress.note(f"Read {got} new tokens at {tps:.0f} tok/s ({got / tps:.0f} s)."
                               + (f" Then wrote at {tg} tok/s." if tg is not None else ""))
        self.server.stop()
        estimates: List[Read] = []
        read_note = ""
        if plan.cpu and reads:                       # the CPU stops at 8K: the longer reads are an estimate from the slope
            deepest, room = max(read_depths(plan.depth, True)), plan.largest(1, ub)
            beyond = [d for d in read_depths(plan.depth) if d > deepest and d + READ_ROOM <= room]
            estimates = read_estimates(reads, beyond)
            read_note = estimate_words(reads, estimates)
            self.progress.note(read_note)
        zones = zones_from_reads(reads)
        self.progress.note(f"Context zones (the time to read a full context again): fast to {zones['good'] // 1024}K "
                           f"(3 min), slow to {zones['slow'] // 1024}K (10 min), very slow to "
                           f"{zones['very_slow'] // 1024}K (20 min).")

        parallel: List[List[float]] = []
        if not plan.quick:
            self._next("parallel requests")
            got_par = self.server.parallel(PARALLEL_COUNTS, PARALLEL_PROMPT, PARALLEL_GEN, kv, ub)
            parallel = [[n, round(tg, 1), round(pp, 1)] for n, (tg, pp) in sorted(got_par.items())]
            if parallel:
                one = parallel[0][1] or 1.0
                self.progress.note("Write speed in total: " + ", ".join(
                    f"{int(n)} at the same time {tg:.0f} tok/s ({tg / n:.1f} each)" for n, tg, _ in parallel)
                    + f". That is {parallel[-1][1] / one:.1f} times the speed of one, with {int(parallel[-1][0])}.")
            else:
                self.progress.note("Parallel requests: not measured (llama-batched-bench did not run).")

        m1, m2 = plan.largest(1, ub), plan.largest(2, ub)
        ctx = choose_ctx(plan.base_ctx, zones, m1)
        # the speculation that won counts (MTP: its draft context; an MTP drafter's weights are in `weights` already, a
        # DFlash, DSpark or EAGLE-3 file's are in the speculation)
        slots = "2" if plan.fits(ctx, 2, ub, Spec(spec, n, plan.draft_bytes_of(spec))) else "1"
        self._next("result")
        self.progress.note(f"Context memory type {KV_WORDS.get(kv, kv)}. Speculation {spec_words(spec, n)}. "
                           f"Context {ctx // 1024}K tokens per slot. Slots: {slots}. Batch size: -ub {ub}.")
        ensure(self._step == self._steps, "C-112", "the progress counted every step it announced")
        results_out: TuneResults = {"speculation": results, "prompt_read": [[g, t] for g, t in reads]}
        if decodes:
            results_out["decode_at_depth"] = [[g, t] for g, t in decodes]
        if parallel:
            results_out["parallel"] = parallel
        if by_ub:
            results_out["ub"] = by_ub
        if kv_res:
            results_out["kv"] = kv_res
        if estimates:
            results_out["prompt_read_estimate"] = [[d, round(t, 1)] for d, t in estimates]
        if ram_results:
            results_out["speculation_ram"] = ram_results
        if pin_results:
            results_out["threads_ram"] = pin_results
        record: TuneRecord = {"date": plan.date, "machine": plan.machine, "llama_cpp": plan.llama_cpp,
                              "settings": {"kv": kv, "spec": spec, "spec_n": n, "ctx": ctx, "slots": slots},
                              "ctx_zones": zones,
                              "results": results_out,
                              "depth": plan.depth,
                              "max_ctx": {"1": m1, "2": m2},
                              "ub": ub}
        if plan.backend:
            record["backend"] = plan.backend
        if kv_note:
            record["kv_note"] = kv_note
        if read_note:
            record["read_note"] = read_note
        if ub_note:
            record["ub_note"] = ub_note
        if spec_note:
            record["spec_note"] = spec_note
        if load_mode:
            record["load_mode"] = load_mode
        if pin_results:
            record["pin"] = "pinned" if pinned else "unpinned"
            record["pin_note"] = pin_note
        return record
