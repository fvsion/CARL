"""Auto fit (domain): the pick per Mac size and goal with the real catalogue, the passes
(2 x 96K, 2 x 64K, 2 x 48K, 1 x 96K, 1 x 64K, 1 x 48K; MTP goes before a pass does), the family
fallback, scope, stock only, and the reasons for every better-ranked model."""
from __future__ import annotations

import json
import os
import unittest
from dataclasses import replace
from typing import Dict, List, Optional, Tuple, cast

from support import GIB, shape, with_window
from carl_core.domain.autofit import (GOALS, SCOPES, Budget, Candidate, auto_fit, best_downloaded, candidate,
                                      model_plan, order_note, plan_for, TIERS)
from carl_core.domain.fit import Spec, estimated_limit, need_bytes
from carl_core.domain.gguf import ModelShape
from carl_core.domain.settings import LLAMA_KEYS
from carl_core.domain.types import ModelInfo

CATALOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "host", "catalog.json")


def header(base: ModelShape, kvh: int, mtp: int, attn: int, act: int, attn_swa: int = 0) -> ModelShape:
    """base with the rest of a real header: KV heads, the MTP head's KV elements per token (0: no head), the
    widest layer's K + V elements (full attention, sliding window) and the widest activation."""
    out = base.copy()
    out.update(kvh=kvh, kv_elems_per_token_mtp=mtp, nextn=1 if mtp else 0, attn_elems=attn, attn_elems_swa=attn_swa,
               act_width=act)
    return out


# Header shapes of the catalogue families (read from the GGUF headers, 2026-10-03; the widths 2026-10-09):
# 35B-A3B: 10 attention layers x 2 KV heads x 512 = 10240 KV elements per token (5.6 KiB at q4_0)
# 27B:     16 attention layers x 4 KV heads x 512 = 32768 (18.0 KiB at q4_0)
SHAPES: Dict[str, ModelShape] = {
    "qwen3.6-35b-a3b": header(shape(experts=256, kv_elems=10240, rs_bytes=65863680), 2, 1024, 1024, 16384),
    "qwen3.8-27b": header(shape(experts=0, kv_elems=32768, rs_bytes=156893184), 4, 2048, 2048, 17408),
    "qwen3.8-9b": header(shape(experts=0, kv_elems=16384, rs_bytes=52690944), 4, 2048, 2048, 12288),
    # Gemma 4 (2026-10-04): full-attention KV elements + sliding-window ones (window 512 / 1024); MTP from a drafter
    "gemma-4-e4b": header(with_window(shape(experts=0, kv_elems=8192, rs_bytes=0), 512, 20480), 2, 0, 2048, 10240, 1024),
    "gemma-4-12b": header(with_window(shape(experts=0, kv_elems=8192, rs_bytes=0), 1024, 163840), 8, 0, 1024, 15360,
                          4096),
    "gemma-4-26b-a4b": header(with_window(shape(experts=128, kv_elems=10240, rs_bytes=0), 1024, 102400), 8, 0, 2048,
                              22528, 4096),
    "gemma-4-31b": header(with_window(shape(experts=0, kv_elems=40960, rs_bytes=0), 1024, 409600), 16, 0, 4096, 21504,
                          8192)}
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
        24: ("qwen3.6-35b-a3b-iq3", "qwen3.8-27b-iq3"),        # 16.0 GiB (both with n-gram: MTP does not fit)
        32: ("qwen3.6-35b-a3b", "qwen3.8-27b"),                # 24.0 GiB (estimate, 3/4 like a real M2 Max)
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
        """25.0 GiB (the M2 Max) holds the Q4 35B-A3B with two 96K windows only without MTP (Phase 23.5 item 5a:
        with MTP + n-gram it paged and failed there; the estimate is 25.1 GiB now): Auto fit keeps the slots and
        the window and drops MTP. The 27B Q4 keeps MTP (23.3 GiB). With the VM up the 10 GiB reserve leaves
        22.0 GiB and the IQ3 build is the pick."""
        cands = real_catalogue()
        here = Budget(THIS_MAC_LIMIT, 32 * GIB, RESERVE)
        fit = auto_fit(cands, here, "everyday")
        self.assertEqual(fit.name, "qwen3.6-35b-a3b")
        assert fit.plan and fit.pick
        self.assertEqual((fit.plan.slots, fit.plan.ctx, fit.plan.spec.kind, fit.plan.dropped),
                         (2, 98304, "ngram-mod", True))
        self.assertAlmostEqual(fit.plan.need / GIB, 23.1, places=1)
        self.assertGreater(need_bytes(fit.pick.shape or {}, fit.pick.weights, 98304, 2, "q4_0", True, fit.pick.spec),
                           THIS_MAC_LIMIT)
        self.assertIn("MTP does not fit with 2 slots × 96K tokens on this Mac, so the speculation is n-gram only",
                      fit.because())
        hard = auto_fit(cands, here, "hard-code")
        self.assertEqual((hard.name, hard.plan.spec.kind if hard.plan else ""), ("qwen3.8-27b", "draft-mtp,ngram-mod"))
        self.assertAlmostEqual((hard.plan.need if hard.plan else 0) / GIB, 23.3, places=1)
        vm = auto_fit(cands, Budget(THIS_MAC_LIMIT, 32 * GIB, RESERVE_VM), "everyday")
        self.assertEqual(vm.name, "qwen3.6-35b-a3b-iq3")
        self.assertIn("qwen3.6-35b-a3b (quality rank 3) does not fit: 2 slots × 96K tokens need 23.1 GiB, and a "
                      "model can use 22.0 GiB",
                      [r.line() for r in vm.rejected])

    def test_16gb_the_12b_drops_mtp_the_e4b_keeps_it(self) -> None:
        """16 GB (10.0 GiB): the 12B's drafter and its draft context do not fit with 2 x 96K, so n-gram; the E4B
        keeps MTP."""
        hard, everyday = auto_fit(real_catalogue(), mac(16), "hard-code"), auto_fit(real_catalogue(), mac(16))
        assert hard.plan and everyday.plan
        self.assertEqual((hard.plan.slots, hard.plan.ctx, hard.plan.spec.kind), (2, 98304, "ngram-mod"))
        self.assertEqual(everyday.plan.spec.kind, "draft-mtp,ngram-mod")

    def test_a_fast_small_dense_model_joins_the_everyday_family(self) -> None:
        """Without the fast mark the E4B is only a dense fallback, and the better-ranked 12B wins everyday too."""
        cands = real_catalogue()
        self.assertTrue(next(c for c in cands if c.name == "gemma-4-e4b").fast)
        slow = [replace(c, fast=False) for c in cands]
        self.assertEqual(auto_fit(slow, mac(16), "everyday").name, "gemma-4-12b")
        fit = auto_fit(cands, mac(16), "everyday")
        self.assertFalse(fit.fallback)
        self.assertIn("Of the stock small dense models that hold two slots", fit.because())
        self.assertIn("this model has the best quality rank", fit.because())
        self.assertIn("gemma-4-12b (quality rank 8) is dense: Auto fit keeps it for the hard code goal",
                      [r.line() for r in fit.rejected])

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
            "qwen3.8-27b (quality rank 1) does not fit: 2 slots × 96K tokens need 20.2 GiB, and a model can use "
            "16.0 GiB",
            "qwen3.8-27b-q3 (quality rank 2) does not fit: 2 slots × 96K tokens need 17.1 GiB, and a model can use "
            "16.0 GiB",
            # X11 (Phase 21 audit): a model that fits no pass says so, not "kept for the other goal"
            "qwen3.6-35b-a3b (quality rank 3) does not fit: the weights and buffers alone need 21.4 GiB, and a model "
            "can use 16.0 GiB"])
        self.assertIn("two slots of 96K tokens", fit.because())

    def test_downloaded_scope(self) -> None:
        cands = real_catalogue(downloaded=("qwen3.6-35b-a3b-iq3", "orcarouter-27b-iq3", "heretic-35b-a3b-iq3"))
        here = Budget(THIS_MAC_LIMIT, 32 * GIB, RESERVE)
        best = auto_fit(cands, here, "everyday", "catalogue")
        start = best_downloaded(best, cands)
        self.assertEqual((best.name, start.name), ("qwen3.6-35b-a3b", "qwen3.6-35b-a3b-iq3"))
        self.assertEqual(start.scope, "downloaded")
        self.assertIn("qwen3.6-35b-a3b (quality rank 3) is not downloaded", [r.line() for r in start.rejected])
        self.assertIs(best_downloaded(start, cands), start)            # downloaded already: itself

    def test_16gb_the_9b_only_when_it_is_the_one_downloaded(self) -> None:
        fit = auto_fit(real_catalogue(), mac(16), "everyday")
        self.assertEqual((fit.name, fit.fallback), ("gemma-4-e4b", False))   # fast family: the E4B fits
        self.assertEqual((fit.plan.slots, fit.plan.ctx) if fit.plan else None, (2, 98304))
        only_9b = [replace(c, downloaded=c.name == "qwen3.8-9b") for c in real_catalogue()]
        start = auto_fit(only_9b, mac(16), "everyday", "downloaded")
        self.assertEqual((start.name, start.fallback), ("qwen3.8-9b", True))    # no fast build downloaded
        self.assertIn("gemma-4-e4b (quality rank 9) is not downloaded", [r.line() for r in start.rejected])

    def test_nothing_fits_lists_every_candidate(self) -> None:
        fit = auto_fit(real_catalogue(), mac(8), "everyday")
        self.assertIsNone(fit.pick)
        self.assertEqual([r.name for r in fit.rejected],
                         ["qwen3.8-27b", "qwen3.8-27b-q3", "qwen3.6-35b-a3b", "qwen3.8-27b-iq3", "qwen3.6-35b-a3b-iq3",
                          "gemma-4-31b", "gemma-4-26b-a4b", "gemma-4-12b", "gemma-4-e4b", "qwen3.8-9b"])
        self.assertIn("No stock model with a quality rank fits", fit.because())
        self.assertIn("nothing fits", fit.summary())


def cand(name: str, arch: str, rank: Optional[int], weights_gib: float, kv_elems: int = 8192,
         abliterated: bool = False, downloaded: bool = True, known: bool = True, spec: Spec = Spec()) -> Candidate:
    return Candidate(name, arch, rank, abliterated, downloaded, int(weights_gib * GIB),
                     shape(kv_elems=kv_elems) if known else None, spec=spec)


class PassesTest(unittest.TestCase):
    """Synthetic shapes: 8192 KV elements per token = 4.5 KiB at q4_0, so 96K costs 0.42 GiB; 65536 = 36 KiB,
    96K = 3.4 GiB. The compute buffer: about 0.1 GiB + 5 KiB per token of the pool."""

    def test_two_slots_win_over_a_better_rank_with_one(self) -> None:
        """Pass 1 (2 x 96K) is tried on every model of the family before pass 2."""
        big = cand("big", "moe", 1, 13.0, kv_elems=65536)       # 96K = 3.4 GiB per slot
        small = cand("small", "moe", 2, 8.0, kv_elems=65536)
        fit = auto_fit([big, small], Budget(18 * GIB, 0, 0), "everyday")
        self.assertEqual((fit.name, fit.plan.slots if fit.plan else 0, fit.tier), ("small", 2, 0))
        self.assertEqual(fit.rejected[0].reason, "does not fit: 2 slots × 96K tokens need 20.9 GiB, and a model can use 18.0 GiB")

    def test_sliding_window_models_plan_as_cache_swa_says(self) -> None:
        """A sliding-window model: planned with the window cache (auto, window), with every layer at full
        length only with cache.swa = full (then 2 x 48K: two slots before the window)."""
        g = Candidate("g", "moe", 1, False, True, 13 * GIB,
                      {**shape(kv_elems=8192), "swa_window": 1024, "kv_elems_per_token_swa": 65536})
        auto = auto_fit([g], Budget(18 * GIB, 0, 0), "everyday")
        self.assertEqual((auto.name, auto.plan.slots if auto.plan else 0), ("g", 2))
        full = auto_fit([g], Budget(18 * GIB, 0, 0, swa="full"), "everyday")
        self.assertEqual((full.plan.slots, full.plan.ctx) if full.plan else None, (2, 49152))

    def test_the_order_on_every_mac(self) -> None:
        """2 x 96K, 2 x 64K, 2 x 48K, 1 x 96K, 1 x 64K, 1 x 48K; nothing below 48K. The needs of m:
        20.9, 18.4, 17.1, 17.1, 15.8, 15.1 GiB."""
        self.assertEqual([t.label() for t in TIERS],
                         ["2 slots × 96K tokens", "2 slots × 64K tokens", "2 slots × 48K tokens",
                          "1 slot × 96K tokens", "1 slot × 64K tokens", "1 slot × 48K tokens"])
        m = cand("m", "moe", 1, 13.0, kv_elems=65536)
        for budget, want, holds in (
                (21.0, (2, 98304, 0), "two slots of 96K tokens (the main session and a subagent)"),
                (18.5, (2, 65536, 1), "two slots of 64K tokens (the main session and a subagent; two slots of 96K "
                                      "tokens do not fit)"),
                (17.5, (2, 49152, 2), "two slots of 48K tokens (the main session and a subagent; two slots of 64K "
                                      "tokens do not fit)"),
                (16.0, (1, 65536, 4), "one slot of 64K tokens (one slot of 96K tokens does not fit)"),
                (15.2, (1, 49152, 5), "one slot of 48K tokens (one slot of 64K tokens does not fit)")):
            with self.subTest(budget=budget):
                fit = auto_fit([m], Budget(budget * GIB, 0, 0), "everyday")
                self.assertEqual((fit.plan.slots, fit.plan.ctx, fit.tier) if fit.plan else None, want)
                self.assertIn(holds, fit.because())
        self.assertEqual(auto_fit([m], Budget(16.0 * GIB, 0, 0)).order_text(),
                         "Auto fit takes the first setup in its order that fits: 1 slot × 64K tokens, because 1 slot × "
                         "96K tokens does not fit")
        # a recurrent state of 1 GiB per slot: 2 x 48K (19.1 GiB) needs more than 1 x 96K (18.1 GiB)
        one = Candidate("one", "moe", 1, False, True, 13 * GIB, shape(kv_elems=65536, rs_bytes=GIB))
        self.assertEqual(auto_fit([one], Budget(18.5 * GIB, 0, 0)).tier, 3)
        self.assertIn("one slot of 96K tokens (two slots of 48K tokens do not fit)",
                      auto_fit([one], Budget(18.5 * GIB, 0, 0)).because())
        none = auto_fit([m], Budget(15.0 * GIB, 0, 0), "everyday")                        # 32K would fit: not offered
        self.assertIsNone(none.pick)
        self.assertEqual(none.rejected[0].reason, "does not fit: 1 slot × 48K tokens need 15.1 GiB, and a model can use "
                                                  "15.0 GiB")

    def test_mtp_goes_before_a_slot_or_the_window(self) -> None:
        """A model whose MTP fits only without it keeps 2 x 96K with n-gram (MTP: 14.8 GiB, n-gram: 12.0 GiB)."""
        q = Candidate("q", "moe", 1, False, True, 10 * GIB, {**shape(kv_elems=8192), "kv_elems_per_token_mtp": 2048},
                      spec=Spec("draft-mtp,ngram-mod", 1))
        roomy = auto_fit([q], Budget(16 * GIB, 0, 0))
        assert roomy.plan
        self.assertEqual((roomy.plan.spec.kind, roomy.plan.dropped, roomy.plan.spec_note()),
                         ("draft-mtp,ngram-mod", False, ""))
        tight = auto_fit([q], Budget(13 * GIB, 0, 0))
        assert tight.plan
        self.assertEqual((tight.plan.slots, tight.plan.ctx, tight.plan.spec.kind, tight.plan.dropped),
                         (2, 98304, "ngram-mod", True))
        self.assertIn("MTP does not fit with 2 slots × 96K tokens on this Mac, so the speculation is n-gram only",
                      tight.because())
        self.assertEqual(tight.order_text(), "Auto fit takes the first setup in its order that fits: 2 slots × 96K "
                                             "tokens. MTP does not fit with 2 slots × 96K tokens on this Mac, so the "
                                             "speculation is n-gram only")
        # a drafter's weights (1 GiB here) go with MTP; n-gram keeps the number of guesses
        g = replace(q, shape={**shape(kv_elems=8192), "nextn": 0}, spec=Spec("draft-mtp", 2, GIB))
        plan = plan_for(g, TIERS[0], 12.2 * GIB)
        self.assertEqual((plan.spec, plan.dropped) if plan else None, (Spec("ngram-mod", 2), True))

    def test_one_model_the_order_under_its_own_window(self) -> None:
        """A start of one model (the launcher): the order, never above the model's window or other slots."""
        m = cand("m", "moe", 1, 13.0, kv_elems=65536)
        self.assertEqual(model_plan(m, 18.5 * GIB)[0], 1)                                  # 2 x 64K
        tier, plan = model_plan(m, 30 * GIB, cap=65536)
        self.assertEqual((tier, plan.ctx if plan else 0), (1, 65536))
        tier, plan = model_plan(m, 18.5 * GIB, slots=1)
        self.assertEqual((tier, plan.slots if plan else 0), (3, 1))
        self.assertEqual(model_plan(m, 10 * GIB), (5, None))
        assert plan
        self.assertEqual(order_note("m", 3, plan, 98304), "")                               # the window it had
        tier, small = model_plan(m, 17.5 * GIB)
        assert small
        self.assertEqual(order_note("m", tier, small, 98304),
                         "m starts with 2 slots × 48K tokens (q4), the first setup in Auto fit's order that fits: "
                         "2 slots × 64K tokens do not fit this Mac.")

    def test_goal_family_first_then_fallback(self) -> None:
        dense = cand("dense", "dense", 1, 10.0)
        moe = cand("moe", "moe", 2, 30.0)
        fit = auto_fit([dense, moe], Budget(20 * GIB, 0, 0), "everyday")
        self.assertEqual((fit.name, fit.fallback), ("dense", True))
        self.assertIn("No fast (MoE or small dense) model fits", fit.because())
        self.assertEqual([r.name for r in fit.rejected], ["moe"])
        hard = auto_fit([dense, moe], Budget(40 * GIB, 0, 0), "hard-code")
        self.assertEqual((hard.name, hard.fallback), ("dense", False))
        everyday = auto_fit([dense, moe], Budget(40 * GIB, 0, 0), "everyday")
        self.assertEqual(everyday.name, "moe")
        self.assertEqual(everyday.rejected[0].line(), "dense (quality rank 1) is dense: Auto fit keeps it for the hard "
                                                      "code goal")

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
        self.assertEqual(fit.rejected[0].reason, "has an unknown size: CARL cannot read its GGUF header (possibly no network)")
        self.assertIsNone(plan_for(unknown, TIERS[0], 20 * GIB))

    def test_window_beyond_the_trained_length(self) -> None:
        short = Candidate("s", "moe", 1, False, True, GIB, shape(ctx_train=65536))
        self.assertIsNone(plan_for(short, TIERS[0], 40 * GIB))
        fit = auto_fit([short], Budget(40 * GIB, 0, 0))
        self.assertEqual((fit.plan.slots, fit.plan.ctx) if fit.plan else None, (2, 65536))
        tiny = Candidate("t", "moe", 1, False, True, GIB, shape(ctx_train=32768))     # below the 48K floor
        self.assertIsNone(auto_fit([tiny], Budget(40 * GIB, 0, 0)).pick)
        self.assertIn("its trained context is 32K tokens, less than 48K",
                      auto_fit([tiny], Budget(40 * GIB, 0, 0)).rejected[0].reason)

    def test_budget(self) -> None:
        self.assertEqual(Budget(25 * GIB, 32 * GIB, 6 * GIB).allowed, 25 * GIB)
        self.assertEqual(Budget(25 * GIB, 32 * GIB, 10 * GIB).allowed, 22 * GIB)
        self.assertEqual(Budget(25 * GIB, 0, 10 * GIB).allowed, 25 * GIB)      # RAM unknown: the GPU limit
        self.assertIn("minus 10.0 GiB kept free for macOS and apps", Budget(25 * GIB, 32 * GIB, 10 * GIB).describe())
        self.assertEqual(Budget(25 * GIB, 32 * GIB, 6 * GIB).describe(), "25.0 GiB (the GPU memory limit)")
        self.assertEqual(Budget(25 * GIB, 32 * GIB, 6 * GIB).explain(),
                         "A model can use 25.0 GiB: the GPU memory limit. This is less than the RAM (32.0 GiB) minus "
                         "the memory kept free for macOS and apps (6.0 GiB), which is 26.0 GiB.")

    def test_candidate_from_a_record(self) -> None:
        m = cast(ModelInfo, {"name": "x", "arch": "MoE", "rank": 2, "abliterated": False, "status": "downloaded",
                             "bytes": 7, "tune": {"kv": "q8_0", "spec": "draft-mtp,ngram-mod", "spec_n": 2}})
        c = candidate(m, None)
        self.assertEqual((c.arch, c.rank, c.downloaded, c.weights, c.kv), ("moe", 2, True, 7, "q8_0"))
        self.assertEqual(c.spec, Spec("ngram-mod", 2))                     # no head known, no drafter: n-gram
        self.assertEqual(candidate(m, shape()).spec, Spec("draft-mtp,ngram-mod", 2))   # an MTP head
        self.assertIsNone(candidate(cast(ModelInfo, {"name": "y", "rank": True}), None).rank)

    def test_config_choices_match_the_domain(self) -> None:
        self.assertEqual(LLAMA_KEYS["auto_goal"].choices, GOALS)
        self.assertEqual(set(LLAMA_KEYS["auto_fit"].choices), set(SCOPES))
        self.assertEqual(LLAMA_KEYS["auto_fit"].default, "catalogue")


if __name__ == "__main__":
    unittest.main()
