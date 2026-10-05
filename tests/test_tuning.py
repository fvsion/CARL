"""Auto-tune: scoring, best mode, context zones, the 96K floor and a whole run with a fake server."""
from __future__ import annotations

import unittest
from typing import Dict, List, Optional, Sequence, Tuple

from support import GIB, shape
from carl_core.domain import tuning as t
from carl_core.domain.errors import ConfigError
from carl_core.domain.types import CtxZones, JsonObject, ModeResult

ZONES: CtxZones = {"good": 65536, "slow": 151552, "very_slow": 233472}


def result(score: float) -> ModeResult:
    return {"prose": score, "code": score, "edit": score, "score": score}


class ScoringTest(unittest.TestCase):
    def test_weighted_geometric_mean(self) -> None:
        self.assertEqual(t.weighted_score({"prose": 50, "code": 50, "edit": 50}), 50.0)
        self.assertEqual(t.weighted_score({"prose": 54.0, "code": 48.1, "edit": 157.3}), 63.85)  # a real run
        self.assertEqual(t.weighted_score({"prose": 0, "code": 0, "edit": 0}), 0.1)

    def test_drafting_must_win_by_three_percent(self) -> None:
        self.assertEqual(t.best_mode({"none:1": result(44.0), "ngram-mod:2": result(45.0)}), ("none", 1))
        self.assertEqual(t.best_mode({"none:1": result(44.0), "ngram-mod:2": result(46.0),
                                      "draft-mtp,ngram-mod:1": result(47.0)}), ("ngram-mod", 2))
        self.assertEqual(t.best_mode({"none:1": result(44.13), "ngram-mod:2": result(60.82),
                                      "draft-mtp:1": result(54.58), "draft-mtp,ngram-mod:1": result(63.85)}),
                         ("draft-mtp,ngram-mod", 1))
        with self.assertRaises(ConfigError):
            t.best_mode({})

    def test_n1_is_never_dropped(self) -> None:
        """One guess (n = 1) is always measured: it sometimes wins (user, 2026-10-04). MTP and MTP +
        n-gram at n = 1 for an MTP head and for a drafter, quick and default; n-gram at n = 1 without MTP."""
        for has_mtp, drafter in ((True, False), (False, True), (True, True)):
            for quick in (True, False):
                modes = t.speculation_modes(has_mtp, quick, drafter)
                with self.subTest(has_mtp=has_mtp, drafter=drafter, quick=quick):
                    self.assertIn(("draft-mtp", 1), modes)
                    self.assertIn(("draft-mtp,ngram-mod", 1), modes)
        for quick in (True, False):
            self.assertIn(("ngram-mod", 1), t.speculation_modes(False, quick, False))

    def test_modes_and_depths(self) -> None:
        self.assertEqual(t.speculation_modes(False, False), [("none", 1), ("ngram-mod", 2), ("ngram-mod", 1)])
        self.assertEqual(t.speculation_modes(False, True), [("none", 1), ("ngram-mod", 2), ("ngram-mod", 1)])
        self.assertEqual(len(t.speculation_modes(True, True)), 4)
        self.assertEqual(t.speculation_modes(True, False)[-1], ("draft-mtp,ngram-mod", 2))
        self.assertEqual(t.read_depths("quick"), [8192, 32768])


class ZonesTest(unittest.TestCase):
    def test_zones_from_real_reads(self) -> None:
        zones = t.zones_from_reads([(8175, 555.81425564641), (32502, 393.8196034073502)])
        self.assertEqual(zones, {"good": 65536, "slow": 151552, "very_slow": 233472})   # the saved M2 Max result

    def test_flat_speed_and_bad_input(self) -> None:
        self.assertEqual(t.zones_from_reads([(8192, 1000.0)])["good"], 180000 // 4096 * 4096)
        with self.assertRaises(ConfigError):
            t.zones_from_reads([])
        with self.assertRaises(ConfigError):
            t.zones_from_reads([(8192, 0.0)])


class ChooseCtxTest(unittest.TestCase):
    def test_96k_floor_when_it_fits(self) -> None:
        self.assertEqual(t.choose_ctx(65536, ZONES, 262144), 98304)        # catalogue 64K, measured fast to 64K
        self.assertEqual(t.choose_ctx(98304, ZONES, 98304), 98304)

    def test_above_the_floor_the_catalogue_window_while_not_too_slow(self) -> None:
        self.assertEqual(t.choose_ctx(131072, ZONES, 262144), 131072)
        self.assertEqual(t.choose_ctx(163840, ZONES, 262144), 98304)        # past "slow": fast-zone window, floored
        fast: CtxZones = {"good": 140000, "slow": 150000, "very_slow": 200000}
        self.assertEqual(t.choose_ctx(163840, fast, 262144), 131072)
        self.assertEqual(t.choose_ctx(163840, ZONES, 120000), 98304)        # doesn't fit: never below 96K

    def test_less_when_96k_does_not_fit(self) -> None:
        self.assertEqual(t.choose_ctx(98304, ZONES, 81920), 65536)
        self.assertEqual(t.choose_ctx(98304, ZONES, 40960), 32768)
        self.assertEqual(t.choose_ctx(98304, ZONES, 20480), 20480)


class FakeServer:
    """Answers like llama-server: decode speed by mode and workload, prompt reads that slow with depth."""

    def __init__(self, speeds: Dict[str, float]) -> None:
        self.speeds = speeds
        self.mode = ""
        self.started: List[Tuple[str, int, int]] = []
        self.stops = 0

    def start(self, spec: str, n: int, ctx: int) -> float:
        self.mode = f"{spec}:{n}"
        self.started.append((spec, n, ctx))
        return 12.0

    def stop(self) -> None:
        self.stops += 1

    @staticmethod
    def content(body: JsonObject) -> str:
        messages = body.get("messages")
        first = messages[0] if isinstance(messages, list) and messages else None
        return str(first.get("content", "")) if isinstance(first, dict) else ""

    def timings(self, body: JsonObject, timeout: float) -> Dict[str, float]:
        prompt = self.content(body)
        if body.get("max_tokens") == 1 or "Summarise these records" in prompt:     # a cold prompt read
            n = len(prompt) // 4
            out = {"prompt_n": float(n), "prompt_per_second": 600.0 - n / 400}
            gen = body.get("max_tokens")
            if isinstance(gen, int) and gen > 1:            # long mode: the decode speed at that depth
                out.update(predicted_n=float(gen), predicted_per_second=50.0 - n / 10000)
            return out
        boost = 3.0 if "ngram" in self.mode and "Return the following file" in prompt else 1.0
        return {"predicted_per_second": self.speeds[self.mode] * boost}

    def count_tokens(self, text: str) -> Optional[int]:
        return len(text) // 4

    def parallel(self, counts: Sequence[int], prompt: int, gen: int, kv: str) -> Dict[int, Tuple[float, float]]:
        self.stops += 1
        return {n: (25.0 * (1 + 0.1 * (n - 1)), 480.0 / (1 if n == 1 else 1.6)) for n in counts}


class Steps:
    def __init__(self) -> None:
        self.lines: List[str] = []

    def step(self, text: str) -> None:
        self.lines.append("STEP " + text)

    def note(self, text: str) -> None:
        self.lines.append("  " + text)


def plan(limit: int, depth: t.Depth = "quick", base_ctx: int = 98304, nextn: int = 1) -> t.TunePlan:
    return t.TunePlan(shape=shape(experts=256, nextn=nextn, kv_elems=10240), weights=14 * GIB, limit=limit,
                      base_ctx=base_ctx, depth=depth, date="2026-10-03", machine="Apple M2 Max 32 GB",
                      llama_cpp="version: test", edit_source="def call(): pass\n")


class AutoTunerTest(unittest.TestCase):
    SPEEDS = {"none:1": 44.0, "ngram-mod:2": 46.0, "draft-mtp:1": 54.0, "draft-mtp,ngram-mod:1": 55.0,
              "draft-mtp:2": 50.0, "draft-mtp,ngram-mod:2": 51.0}

    def test_full_run(self) -> None:
        server, steps = FakeServer(self.SPEEDS), Steps()
        rec = t.AutoTuner(server, steps).run(plan(limit=21 * GIB))
        self.assertEqual(rec["settings"], {"kv": "q4_0", "spec": "draft-mtp,ngram-mod", "spec_n": 1, "ctx": 98304,
                                           "slots": "2"})
        step_lines = [x for x in steps.lines if x.startswith("STEP")]
        self.assertEqual(step_lines[0], "STEP 1/7 memory")
        self.assertEqual(step_lines[-1], "STEP 7/7 result")
        self.assertEqual(len(rec["results"]["speculation"]), 4)
        reads = rec["results"]["prompt_read"]                 # cold reads at about 8K and 32K tokens
        self.assertEqual(len(reads), 2)
        for (got, _), want in zip(reads, (8192, 32768)):
            self.assertAlmostEqual(got, want, delta=16)
        self.assertEqual(server.started[-1], ("draft-mtp,ngram-mod", 1, 36864))
        self.assertGreaterEqual(server.stops, 1)
        self.assertEqual(set(rec), {"date", "machine", "llama_cpp", "settings", "ctx_zones", "results", "max_ctx", "depth"})
        self.assertNotIn("decode_at_depth", rec["results"])
        self.assertNotIn("parallel", rec["results"])                                   # quick: skipped

    def test_picks_less_than_96k_when_it_does_not_fit(self) -> None:
        rec = t.AutoTuner(FakeServer(self.SPEEDS), Steps()).run(plan(limit=int(15.45 * GIB)))
        self.assertEqual(rec["max_ctx"]["1"], 81920)
        self.assertEqual(rec["settings"]["ctx"], 65536)
        self.assertEqual(rec["settings"]["slots"], "1")

    def test_refuses_without_a_16k_window(self) -> None:
        with self.assertRaisesRegex(ConfigError, "a context of 16K tokens"):
            t.AutoTuner(FakeServer(self.SPEEDS), Steps()).run(plan(limit=15 * GIB))

    def test_no_mtp_head_measures_ngram_with_1_and_2_drafts(self) -> None:
        server = FakeServer(dict(self.SPEEDS, **{"ngram-mod:1": 48.0}))
        rec = t.AutoTuner(server, Steps()).run(plan(limit=21 * GIB, nextn=0))
        self.assertEqual(list(rec["results"]["speculation"]), ["none:1", "ngram-mod:2", "ngram-mod:1"])
        self.assertEqual((rec["settings"]["spec"], rec["settings"]["spec_n"]), ("ngram-mod", 1))   # it won here


class LongTest(unittest.TestCase):
    SPEEDS = AutoTunerTest.SPEEDS

    def test_long_reads_to_192k_with_decode_at_each_depth(self) -> None:
        server, steps = FakeServer(self.SPEEDS), Steps()
        rec = t.AutoTuner(server, steps).run(plan(limit=24 * GIB, depth="long"))
        reads = rec["results"]["prompt_read"]
        self.assertEqual(len(reads), 5)
        for (got, _), want in zip(reads, (8192, 32768, 65536, 131072, 196608)):
            self.assertAlmostEqual(got, want, delta=64)
        self.assertEqual(len(rec["results"]["decode_at_depth"]), 5)
        self.assertEqual([p[0] for p in rec["results"]["parallel"]], [1, 2, 3, 4])         # not quick: measured
        self.assertTrue(any("Write speed in total: 1 at the same time 25 tok/s" in x for x in steps.lines))
        self.assertEqual(server.started[-1][2], t.LONG_READ_CTX)            # the reading server holds 192K + room
        self.assertTrue(any("The long reads (128K, 192K) take about" in x for x in steps.lines))
        self.assertEqual((rec["depth"], len(rec["results"]["speculation"])), ("long", 6))   # the default modes too

    def test_long_stops_at_the_largest_window_that_fits(self) -> None:
        steps = Steps()
        rec = t.AutoTuner(FakeServer(self.SPEEDS), steps).run(plan(limit=int(15.9 * GIB), depth="long"))
        m1 = rec["max_ctx"]["1"]
        self.assertLess(m1, 196608)
        self.assertTrue(all(g + t.READ_ROOM <= m1 for g, _ in rec["results"]["prompt_read"]))
        self.assertTrue(any("not measured. The largest context that fits" in x for x in steps.lines))

    def test_depths_and_the_fit(self) -> None:
        self.assertEqual(t.read_depths("quick"), [8192, 32768])
        self.assertEqual(t.read_depths("default"), [8192, 32768, 65536])
        self.assertEqual(t.read_ctx("long", 120000), 120000)
        self.assertEqual((t.as_depth("long"), t.as_depth("x")), ("long", "default"))
        two = [(8192, 500.0), (65536, 250.0)]                               # two reads: the line through them
        self.assertEqual(t.zones_from_reads(two), t.zones_from_reads([(8192, 500.0), (65536, 250.0)]))
        a, b = t._fit(two)
        self.assertAlmostEqual(a + b * 8192, 1 / 500.0)
        self.assertAlmostEqual(a + b * 65536, 1 / 250.0)
        self.assertAlmostEqual(t.read_seconds(two, 8192), a * 8192 + b * 8192 ** 2 / 2)


class GuardTest(unittest.TestCase):
    def test_blocking_processes(self) -> None:
        procs = [t.ProcessInfo(1, 9 * 2 ** 20, "/usr/bin/big"), t.ProcessInfo(2, 1000, "/opt/bin/llama-server"),
                 t.ProcessInfo(3, 1000, "zsh"), t.ProcessInfo(4, 9 * 2 ** 20, "/me")]
        self.assertEqual(t.blocking_processes(procs, 8 * 2 ** 20, own_pid=4),
                         ["pid 1 (big, 9.0 GiB)", "pid 2 (llama-server, 0.0 GiB)"])


if __name__ == "__main__":
    unittest.main()
