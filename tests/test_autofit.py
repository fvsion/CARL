"""Auto fit (domain): the pick per Mac size and goal with the real catalogue, the passes
(2 x 96K, 1 x 96K, the largest window >= 32K), the family fallback, scope, stock only,
and the reasons for every better-ranked model."""
from __future__ import annotations

import json
import os
import unittest
from dataclasses import replace
from typing import Dict, List, Optional, Tuple, cast

from support import GIB, shape, with_window
from carl_core.domain.autofit import (GOALS, SCOPES, Budget, Candidate, auto_fit, best_downloaded, candidate,
                                      plan_for, TIERS)
from carl_core.domain.fit import estimated_limit
from carl_core.domain.gguf import ModelShape
from carl_core.domain.settings import LLAMA_KEYS
from carl_core.domain.types import ModelInfo

CATALOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "host", "catalog.json")
# Header shapes of the two catalogue families (read from the GGUF headers, 2026-10-03):
# 35B-A3B: 10 attention layers x 2 KV heads x 512 = 10240 KV elements per token (5.6 KiB at q4_0)
# 27B:     16 attention layers x 4 KV heads x 512 = 32768 (18.0 KiB at q4_0)
SHAPES: Dict[str, ModelShape] = {"qwen3.6-35b-a3b": shape(experts=256, kv_elems=10240, rs_bytes=65863680),
          "qwen3.8-27b": shape(experts=0, kv_elems=32768, rs_bytes=156893184),
          "qwen3.8-9b": shape(experts=0, kv_elems=16384, rs_bytes=52690944),
          # Gemma 4 (2026-10-04): full-attention KV elements + sliding-window ones (window 512 / 1024)
          "gemma-4-e4b": with_window(shape(experts=0, kv_elems=8192, rs_bytes=0), 512, 20480),
          "gemma-4-12b": with_window(shape(experts=0, kv_elems=8192, rs_bytes=0), 1024, 163840),
          "gemma-4-26b-a4b": with_window(shape(experts=128, kv_elems=10240, rs_bytes=0), 1024, 102400),
          "gemma-4-31b": with_window(shape(experts=0, kv_elems=40960, rs_bytes=0), 1024, 409600)}
THIS_MAC_LIMIT = 26800603136                  # an M2 Max 32 GB: Metal's recommendedMaxWorkingSetSize (25.0 GiB)
RESERVE, RESERVE_VM = 6 * GIB, 10 * GIB


def real_catalogue(downloaded: Tuple[str, ...] = ()) -> List[Candidate]:
    """The catalogue's models (sizes, ranks, families, abliterated) with their family's shape."""
    with open(CATALOG, encoding="utf-8") as f:
        models = json.load(f)["models"]
    out = []
    for m in models:
        info = cast(ModelInfo, dict(m, bytes=m["hf"]["bytes"],
                                    status="downloaded" if m["name"] in downloaded else "missing"))
        out.append(candidate(info, SHAPES[m["family"]]))
    return out


def mac(ram_gb: int, vm: bool = False) -> Budget:
    """A Mac with ram_gb of RAM and the estimated GPU limit."""
    return Budget(estimated_limit(ram_gb * GIB)[0], ram_gb * GIB, RESERVE_VM if vm else RESERVE)


class RealCatalogueTest(unittest.TestCase):
    """The picks agreed for each Mac size (CARL prioritises speed: everyday = the 35B-A3B MoE)."""

    # RAM GB -> (everyday pick, hard-code pick); None = nothing fits. All at 2 x 96K (q4_0).
    EXPECTED: Dict[int, Tuple[Optional[str], Optional[str]]] = {
        16: ("gemma-4-e4b", "gemma-4-12b"),                    # 10.0 GiB: the E4B is fast (everyday), the 12B better
        24: ("qwen3.6-35b-a3b-iq3", "qwen3.8-27b-iq3"),        # 16.0 GiB
        32: ("qwen3.6-35b-a3b", "qwen3.8-27b"),                # 24.0 GiB (estimate, 3/4 like a real M2 Max): Q4 needs 23.3
        36: ("qwen3.6-35b-a3b", "qwen3.8-27b"),                # 27.0 GiB
        48: ("qwen3.6-35b-a3b", "qwen3.8-27b"),
        64: ("qwen3.6-35b-a3b", "qwen3.8-27b"),
        128: ("qwen3.6-35b-a3b", "qwen3.8-27b"),
    }

    def test_picks_per_mac(self) -> None:
        cands = real_catalogue()
        for ram, (everyday, hard) in self.EXPECTED.items():
            for goal, want in zip(GOALS, (everyday, hard)):            # GOALS: everyday, hard-code
                with self.subTest(ram=ram, goal=goal):
                    fit = auto_fit(cands, mac(ram), goal)
                    self.assertEqual(fit.name, want)
                    if want:
                        self.assertIsNotNone(fit.plan)
                        self.assertEqual((fit.plan.slots, fit.plan.ctx, fit.plan.kv) if fit.plan else None,
                                         (2, 98304, "q4_0"))
                        self.assertFalse(fit.fallback)

    def test_this_mac_32gb_with_its_real_limit(self) -> None:
        """25.0 GiB holds the Q4 35B-A3B with two 96K windows (23.3 GiB); with the VM up the
        10 GiB reserve leaves 22.0 GiB and the IQ3 build is the pick."""
        cands = real_catalogue()
        here = Budget(THIS_MAC_LIMIT, 32 * GIB, RESERVE)
        fit = auto_fit(cands, here, "everyday")
        self.assertEqual(fit.name, "qwen3.6-35b-a3b")
        self.assertAlmostEqual((fit.plan.need if fit.plan else 0) / GIB, 23.3, places=1)
        self.assertEqual(auto_fit(cands, here, "hard-code").name, "qwen3.8-27b")
        vm = auto_fit(cands, Budget(THIS_MAC_LIMIT, 32 * GIB, RESERVE_VM), "everyday")
        self.assertEqual(vm.name, "qwen3.6-35b-a3b-iq3")
        self.assertIn("qwen3.6-35b-a3b (rank 3): the weights and buffers alone use 22.2 GiB (the limit on this Mac is 22.0 GiB)",
                      [r.line() for r in vm.rejected])

    def test_a_fast_small_dense_model_joins_the_everyday_family(self) -> None:
        """Without the fast mark the E4B is only a dense fallback, and the better-ranked 12B wins everyday too."""
        cands = real_catalogue()
        self.assertTrue(next(c for c in cands if c.name == "gemma-4-e4b").fast)
        slow = [replace(c, fast=False) for c in cands]
        self.assertEqual(auto_fit(slow, mac(16), "everyday").name, "gemma-4-12b")
        fit = auto_fit(cands, mac(16), "everyday")
        self.assertFalse(fit.fallback)
        self.assertIn("best-ranked stock small dense build", fit.because())
        self.assertIn("gemma-4-12b (rank 8): dense: for the hard-code goal (slower)", [r.line() for r in fit.rejected])

    def test_never_an_abliterated_model(self) -> None:
        cands = real_catalogue(downloaded=("orcarouter-27b-iq3", "heretic-35b-a3b-iq3"))
        for ram in self.EXPECTED:
            for goal in GOALS:
                for scope in SCOPES:
                    fit = auto_fit(cands, mac(ram), goal, scope)
                    with self.subTest(ram=ram, goal=goal, scope=scope):
                        self.assertFalse(fit.pick and fit.pick.abliterated)
                        self.assertFalse(any(r.name.startswith(("orcarouter", "heretic")) for r in fit.rejected))

    def test_reasons_name_the_numbers(self) -> None:
        fit = auto_fit(real_catalogue(), mac(24), "hard-code")
        self.assertEqual([r.line() for r in fit.rejected], [
            "qwen3.8-27b (rank 1): the weights and buffers alone use 16.5 GiB (the limit on this Mac is 16.0 GiB)",
            "qwen3.8-27b-q3 (rank 2): 2 × 96K uses 16.9 GiB (the limit on this Mac is 16.0 GiB)",
            "qwen3.6-35b-a3b (rank 3): MoE: for the everyday goal (faster)"])
        self.assertIn("two 96K windows", fit.because())

    def test_downloaded_scope(self) -> None:
        cands = real_catalogue(downloaded=("qwen3.6-35b-a3b-iq3", "orcarouter-27b-iq3", "heretic-35b-a3b-iq3"))
        here = Budget(THIS_MAC_LIMIT, 32 * GIB, RESERVE)
        best = auto_fit(cands, here, "everyday", "catalogue")
        start = best_downloaded(best, cands)
        self.assertEqual((best.name, start.name), ("qwen3.6-35b-a3b", "qwen3.6-35b-a3b-iq3"))
        self.assertEqual(start.scope, "downloaded")
        self.assertIn("qwen3.6-35b-a3b (rank 3): not downloaded", [r.line() for r in start.rejected])
        self.assertIs(best_downloaded(start, cands), start)            # downloaded already: itself

    def test_16gb_the_9b_only_when_it_is_the_one_downloaded(self) -> None:
        fit = auto_fit(real_catalogue(), mac(16), "everyday")
        self.assertEqual((fit.name, fit.fallback), ("gemma-4-e4b", False))   # fast family: the E4B fits
        self.assertEqual((fit.plan.slots, fit.plan.ctx) if fit.plan else None, (2, 98304))
        only_9b = [replace(c, downloaded=c.name == "qwen3.8-9b") for c in real_catalogue()]
        start = auto_fit(only_9b, mac(16), "everyday", "downloaded")
        self.assertEqual((start.name, start.fallback), ("qwen3.8-9b", True))    # no fast build downloaded
        self.assertIn("gemma-4-e4b (rank 9): not downloaded", [r.line() for r in start.rejected])

    def test_nothing_fits_lists_every_candidate(self) -> None:
        fit = auto_fit(real_catalogue(), mac(8), "everyday")
        self.assertIsNone(fit.pick)
        self.assertEqual([r.name for r in fit.rejected],
                         ["qwen3.8-27b", "qwen3.8-27b-q3", "qwen3.6-35b-a3b", "qwen3.8-27b-iq3", "qwen3.6-35b-a3b-iq3",
                          "gemma-4-31b", "gemma-4-26b-a4b", "gemma-4-12b", "gemma-4-e4b", "qwen3.8-9b"])
        self.assertIn("no ranked stock model fits", fit.because())
        self.assertIn("nothing fits", fit.summary())


def cand(name: str, arch: str, rank: Optional[int], weights_gib: float, kv_elems: int = 8192,
         abliterated: bool = False, downloaded: bool = True, known: bool = True) -> Candidate:
    return Candidate(name, arch, rank, abliterated, downloaded, int(weights_gib * GIB),
                     shape(kv_elems=kv_elems) if known else None)


class PassesTest(unittest.TestCase):
    """Synthetic shapes: 8192 KV elements per token = 4.5 KiB at q4_0, so 96K costs 0.42 GiB."""

    def test_two_slots_win_over_a_better_rank_with_one(self) -> None:
        """Pass 1 (2 x 96K) is tried on every model of the family before pass 2."""
        big = cand("big", "moe", 1, 13.0, kv_elems=65536)       # 96K = 3.4 GiB per slot
        small = cand("small", "moe", 2, 8.0, kv_elems=65536)
        fit = auto_fit([big, small], Budget(18 * GIB, 0, 0), "everyday")
        self.assertEqual((fit.name, fit.plan.slots if fit.plan else 0, fit.tier), ("small", 2, 0))
        self.assertEqual(fit.rejected[0].reason, "2 × 96K uses 20.8 GiB (the limit on this Mac is 18.0 GiB)")

    def test_sliding_window_models_plan_as_cache_swa_says(self) -> None:
        """A sliding-window model: planned with the window cache (auto, window), with every layer at full
        length only with cache.swa = full."""
        g = Candidate("g", "moe", 1, False, True, 13 * GIB,
                      {**shape(kv_elems=8192), "swa_window": 1024, "kv_elems_per_token_swa": 65536})
        auto = auto_fit([g], Budget(18 * GIB, 0, 0), "everyday")
        self.assertEqual((auto.name, auto.plan.slots if auto.plan else 0), ("g", 2))
        full = auto_fit([g], Budget(18 * GIB, 0, 0, swa="full"), "everyday")
        self.assertEqual(full.plan.slots if full.plan else 0, 1)

    def test_one_slot_then_the_largest_window(self) -> None:
        m = cand("m", "moe", 1, 13.0, kv_elems=65536)
        one = auto_fit([m], Budget(17.5 * GIB, 0, 0), "everyday")
        self.assertEqual((one.plan.slots, one.plan.ctx, one.tier) if one.plan else None, (1, 98304, 1))
        self.assertIn("one 96K window", one.because())
        small = auto_fit([m], Budget(16 * GIB, 0, 0), "everyday")
        self.assertEqual((small.plan.slots, small.tier) if small.plan else None, (1, 2))
        self.assertTrue(32768 <= (small.plan.ctx if small.plan else 0) < 98304)
        self.assertIn("the largest that fits", small.because())
        none = auto_fit([m], Budget(14.5 * GIB, 0, 0), "everyday")
        self.assertIsNone(none.pick)
        self.assertIn("the largest window that fits is", none.rejected[0].reason)

    def test_goal_family_first_then_fallback(self) -> None:
        dense = cand("dense", "dense", 1, 10.0)
        moe = cand("moe", "moe", 2, 30.0)
        fit = auto_fit([dense, moe], Budget(20 * GIB, 0, 0), "everyday")
        self.assertEqual((fit.name, fit.fallback), ("dense", True))
        self.assertIn("no fast (MoE or small dense) build fits", fit.because())
        self.assertEqual([r.name for r in fit.rejected], ["moe"])
        hard = auto_fit([dense, moe], Budget(40 * GIB, 0, 0), "hard-code")
        self.assertEqual((hard.name, hard.fallback), ("dense", False))
        everyday = auto_fit([dense, moe], Budget(40 * GIB, 0, 0), "everyday")
        self.assertEqual(everyday.name, "moe")
        self.assertEqual(everyday.rejected[0].line(), "dense (rank 1): dense: for the hard-code goal (slower)")

    def test_only_ranked_stock_models(self) -> None:
        ablit = cand("ablit", "moe", 1, 5.0, abliterated=True)
        custom = cand("custom", "moe", None, 5.0)                    # no rank yet (Phase 5: user-set)
        stock = cand("stock", "moe", 3, 5.0)
        fit = auto_fit([ablit, custom, stock], Budget(20 * GIB, 0, 0))
        self.assertEqual(fit.name, "stock")
        self.assertEqual(fit.rejected, ())
        self.assertFalse(ablit.eligible or custom.eligible)
        self.assertIsNone(auto_fit([ablit, custom], Budget(20 * GIB, 0, 0)).pick)

    def test_unknown_header_and_ties(self) -> None:
        unknown = cand("a", "moe", 1, 5.0, known=False)
        b, c = cand("b", "moe", 2, 5.0), cand("c", "moe", 2, 5.0)
        fit = auto_fit([c, unknown, b], Budget(20 * GIB, 0, 0))
        self.assertEqual(fit.name, "b")                              # same rank: by name
        self.assertEqual(fit.rejected[0].reason, "size unknown: CARL cannot read its GGUF header (no network?)")
        self.assertIsNone(plan_for(unknown, TIERS[0], 20 * GIB))

    def test_window_beyond_the_trained_length(self) -> None:
        short = Candidate("s", "moe", 1, False, True, GIB, shape(ctx_train=65536))
        self.assertIsNone(plan_for(short, TIERS[0], 40 * GIB))
        fit = auto_fit([short], Budget(40 * GIB, 0, 0))
        self.assertEqual(fit.plan.ctx if fit.plan else 0, 65536)

    def test_budget(self) -> None:
        self.assertEqual(Budget(25 * GIB, 32 * GIB, 6 * GIB).allowed, 25 * GIB)
        self.assertEqual(Budget(25 * GIB, 32 * GIB, 10 * GIB).allowed, 22 * GIB)
        self.assertEqual(Budget(25 * GIB, 0, 10 * GIB).allowed, 25 * GIB)      # RAM unknown: the GPU limit
        self.assertIn("minus 10.0 GiB for macOS and apps", Budget(25 * GIB, 32 * GIB, 10 * GIB).describe())
        self.assertEqual(Budget(25 * GIB, 32 * GIB, 6 * GIB).describe(), "25.0 GiB (the GPU limit)")

    def test_candidate_from_a_record(self) -> None:
        m = cast(ModelInfo, {"name": "x", "arch": "MoE", "rank": 2, "abliterated": False, "status": "downloaded",
                             "bytes": 7, "tune": {"kv": "q8_0"}})
        c = candidate(m, None)
        self.assertEqual((c.arch, c.rank, c.downloaded, c.weights, c.kv), ("moe", 2, True, 7, "q8_0"))
        self.assertIsNone(candidate(cast(ModelInfo, {"name": "y", "rank": True}), None).rank)

    def test_config_choices_match_the_domain(self) -> None:
        self.assertEqual(LLAMA_KEYS["auto_goal"].choices, GOALS)
        self.assertEqual(set(LLAMA_KEYS["auto_fit"].choices), set(SCOPES))
        self.assertEqual(LLAMA_KEYS["auto_fit"].default, "catalogue")


if __name__ == "__main__":
    unittest.main()
