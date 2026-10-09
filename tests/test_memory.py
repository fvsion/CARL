"""The memory estimate against what llama.cpp 0.6.0 measured (Phase 23.4.4 items 3 and 4): Saluki (a
Qwen3.8-27B hybrid at IQ2) on the M3 Pro, docs/phase-plans/phase23.4.2/results/fit/ (fit_speed.jsonl and each
server log, -lv 4); the Gemma 4 E4B's compute buffers (docs/phase-plans/phase20/research-gemma4/smoke/); the two
starts of Phase 23.5 item 5a that did not fit although the old estimate said they did."""
from __future__ import annotations

import unittest
from typing import Tuple

from support import GIB
from carl_core.domain.fit import (MARGIN, Spec, compute_bytes, max_ctx, mtp_spec, need_bytes, need_parts,
                                  start_plan)
from carl_core.domain.gguf import Meta, model_shape

MIB = 2 ** 20


def meta(arch: str, **keys: object) -> Meta:
    """A header's scalars: arch.KEY for each keyword (dots as double underscores)."""
    out: Meta = {"general.architecture": arch}
    for k, v in keys.items():
        out[f"{arch}.{k.replace('__', '.')}"] = v           # type: ignore[assignment]
    return out


# The GGUF headers (read from the files: Saluki from its server log, the others from CARL's header cache)
SALUKI = model_shape(meta("qwen35", block_count=65, nextn_predict_layers=1, context_length=262144,
                          embedding_length=5120, feed_forward_length=17408, attention__head_count=24,
                          attention__head_count_kv=4, attention__key_length=256, attention__value_length=256,
                          ssm__conv_kernel=4, ssm__state_size=128, ssm__group_count=16, ssm__time_step_rank=48,
                          ssm__inner_size=6144, full_attention_interval=4))
QWEN35B = model_shape(meta("qwen35moe", block_count=41, nextn_predict_layers=1, context_length=262144,
                           embedding_length=2048, attention__head_count=16, attention__head_count_kv=2,
                           attention__key_length=256, attention__value_length=256, expert_count=256,
                           expert_used_count=8, expert_feed_forward_length=512, expert_shared_feed_forward_length=512,
                           ssm__conv_kernel=4, ssm__state_size=128, ssm__group_count=16, ssm__time_step_rank=32,
                           ssm__inner_size=4096, full_attention_interval=4))
GEMMA12B = model_shape(meta("gemma4", block_count=48, context_length=262144, embedding_length=3840,
                            feed_forward_length=15360, attention__head_count=16,
                            attention__head_count_kv=",".join(["8,8,8,8,8,1"] * 8), attention__key_length=512,
                            attention__value_length=512, attention__sliding_window=1024,
                            attention__shared_kv_layers=0, attention__sliding_window_pattern="111110" * 8,
                            attention__key_length_swa=256, attention__value_length_swa=256))
E4B = model_shape(meta("gemma4", block_count=42, context_length=131072, embedding_length=2560,
                       feed_forward_length=10240, attention__head_count=8, attention__head_count_kv=2,
                       attention__key_length=512, attention__value_length=512, attention__sliding_window=512,
                       attention__shared_kv_layers=18, attention__sliding_window_pattern="111110" * 7,
                       attention__key_length_swa=256, attention__value_length_swa=256))

# Saluki, llama.cpp 0.6.0, KV q4_0, flash attention (fit_speed.jsonl): (slots, ctx per slot, --spec-type, guesses,
# -ub) -> the GPU buffers in GiB: KV (with the draft context's), recurrent state, compute (every scheduler)
SALUKI_RUNS: Tuple[Tuple[int, int, str, int, int, float, float, float], ...] = (
    (2, 32768, "draft-mtp", 1, 512, 1.375, 0.584, 1.237),
    (2, 32768, "none", 1, 512, 1.125, 0.292, 0.411),
    (1, 65536, "draft-mtp", 1, 512, 1.375, 0.292, 1.237),
    (1, 65536, "none", 1, 512, 1.125, 0.146, 0.411),
    (1, 65536, "draft-mtp", 2, 512, 1.375, 0.438, 1.237),
    (1, 65536, "draft-mtp,ngram-mod", 1, 512, 1.375, 0.292, 1.237),
    (1, 65536, "ngram-mod", 1, 512, 1.125, 0.146, 0.411),
    (2, 32768, "draft-mtp,ngram-mod", 1, 512, 1.375, 0.584, 1.237),
    (1, 49152, "draft-mtp", 1, 512, 1.031, 0.292, 1.003),
    (2, 49152, "draft-mtp", 1, 512, 2.063, 0.584, 1.707),
    (2, 49152, "none", 1, 512, 1.688, 0.292, 0.567),
    (2, 32768, "draft-mtp", 1, 256, 1.375, 0.584, 1.042),
    (1, 65536, "draft-mtp", 1, 256, 1.375, 0.292, 1.031),
    (2, 32768, "none", 1, 256, 1.125, 0.292, 0.354),
    (2, 49152, "none", 1, 256, 1.688, 0.292, 0.495),
    (1, 65536, "draft-mtp", 1, 128, 1.375, 0.292, 0.932))
SALUKI_MTP_FILE, SALUKI_PLAIN_FILE = 8_350_000_000, 7_900_000_000      # 8.35 GB and 7.9 GB (the model card)
M2_MAX_LIMIT = 26800603136                                           # 25.0 GiB (32 GB)
M3_PRO_LIMIT = int(28.1 * GIB)                                       # 36 GB (reference/memory.md)
GEMMA12B_FILE, GEMMA12B_DRAFTER = 7219673216, 253708960             # host/catalog.json
QWEN35B_Q4_FILE = 22663387424


class HeaderTest(unittest.TestCase):
    def test_saluki_shape(self) -> None:
        s = SALUKI
        self.assertEqual((s["attn_layers"], s["rec_layers"], s["kv_elems_per_token"], s["kv_elems_per_token_mtp"]),
                         (16, 48, 32768, 2048))
        self.assertAlmostEqual(s["rs_bytes"] / MIB, 149.625)          # the log: RS buffer 149.62 MiB per slot
        self.assertEqual((s["attn_elems"], s["act_width"]), (2048, 17408))
        self.assertEqual((QWEN35B["attn_elems"], QWEN35B["act_width"]), (1024, 16384))   # 2048 x 8 experts' outputs
        self.assertEqual((GEMMA12B["attn_elems"], GEMMA12B["attn_elems_swa"], GEMMA12B["act_width"]),
                         (1024, 4096, 15360))
        self.assertEqual((E4B["attn_elems"], E4B["attn_elems_swa"]), (2048, 1024))


class MeasuredTest(unittest.TestCase):
    """The rule against every measured point: within 10 MiB."""

    def test_saluki_kv_state_and_compute(self) -> None:
        for slots, ctx, kind, n, ub, kv, rs, compute in SALUKI_RUNS:
            with self.subTest(slots=slots, ctx=ctx, spec=kind, n=n, ub=ub):
                p = need_parts(SALUKI, 0, ctx, slots, "q4_0", True, Spec(kind, n), ub)
                self.assertAlmostEqual((p.context + (p.draft - 2 * p.compute if p.draft else 0)) / GIB, kv, delta=0.001)
                self.assertAlmostEqual(p.state / GIB, rs, delta=0.001)
                got = p.compute + (2 * p.compute if p.draft else 0)
                self.assertAlmostEqual(got / GIB, compute, delta=10 / 1024)

    def test_saluki_totals_with_the_mtp_file(self) -> None:
        """The whole estimate with the MTP file (its GPU buffer 7.77 GiB): the measured GPU totals + the margin.
        Without MTP the file counts its head too (0.42 GiB that llama.cpp 0.6.0 did not put on the GPU)."""
        for slots, ctx, kind, total in ((2, 49152, "none", 9.893), (2, 49152, "draft-mtp", 12.12),
                                        (1, 65536, "draft-mtp", 10.67), (2, 32768, "none", 9.174)):
            with self.subTest(slots=slots, ctx=ctx, spec=kind):
                head = 0 if "mtp" in kind else 7952.40 / 1024 - 7521.99 / 1024
                parts = need_parts(SALUKI, SALUKI_MTP_FILE, ctx, slots, "q4_0", True, Spec(kind, 1))
                self.assertAlmostEqual((parts.total - parts.margin) / GIB, total + head, delta=0.03)

    def test_e4b_compute_with_both_caches(self) -> None:
        """2 x 64K, -ub 512: 714.30 MiB with the window cache, 840.80 MiB with the full cache."""
        self.assertAlmostEqual(compute_bytes(E4B, 65536, 2, 512, False) / MIB, 714.30, delta=5)
        self.assertAlmostEqual(compute_bytes(E4B, 65536, 2, 512, True) / MIB, 840.80, delta=5)


class SixteenGbTest(unittest.TestCase):
    """A 16 GB Mac: a model can use 10.0 GiB (RAM - 6 GiB; the GPU limit is 10.67)."""

    def test_saluki_2x48k_fits_without_mtp_only(self) -> None:
        budget = 10 * GIB
        plain = need_bytes(SALUKI, SALUKI_PLAIN_FILE, 49152, 2, "q4_0", True, Spec("ngram-mod", 2))
        self.assertLessEqual(plain, budget)                                           # measured 9.89 GiB
        mtp = need_bytes(SALUKI, SALUKI_MTP_FILE, 49152, 2, "q4_0", True, Spec("draft-mtp,ngram-mod", 1))
        self.assertAlmostEqual(mtp / GIB, 12.2, delta=0.05)                           # measured 12.12 GiB
        self.assertGreater(mtp, budget)
        # the file with the MTP head counts the head even without MTP: 2 x 48K does not fit then
        self.assertGreater(need_bytes(SALUKI, SALUKI_MTP_FILE, 49152, 2, "q4_0", True, Spec("ngram-mod", 2)), budget)
        p = start_plan("auto", SALUKI, SALUKI_MTP_FILE, 49152, "auto", "q4_0", 11 * GIB,
                       mtp_spec("draft-mtp,ngram-mod", 1, SALUKI), 512)
        self.assertEqual((p.slots, p.spec.kind, p.dropped), (2, "ngram-mod", True))   # MTP goes before a slot


class PhaseTwentyThreeFiveTest(unittest.TestCase):
    """Phase 23.5 item 5a: two starts that failed although the old estimate (flat 1 GiB of buffers, no draft
    context) said they fit. No buffer sizes were logged for them: the headers and the catalogue sizes only."""

    def test_35b_q4_mtp_and_ngram_on_the_32gb_m2_max(self) -> None:
        """2 x 96K, MTP + n-gram n=2: the old estimate 23.3 GiB; it paged (4-5 tok/s) and failed. Now it does
        not fit; n-gram only fits, so the launcher keeps both slots and the window."""
        spec = mtp_spec("draft-mtp,ngram-mod", 2, QWEN35B)
        parts = need_parts(QWEN35B, QWEN35B_Q4_FILE, 98304, 2, "q4_0", True, spec)
        self.assertGreater(parts.total, M2_MAX_LIMIT)
        self.assertAlmostEqual(parts.total / GIB, 25.06, delta=0.01)
        self.assertAlmostEqual(parts.draft / GIB, 0.375 + 2 * parts.compute / GIB)     # the head's f16 KV + 2 buffers
        self.assertAlmostEqual(parts.state, 3 * 2 * QWEN35B["rs_bytes"])              # x (1 + 2 guesses)
        self.assertLess(need_bytes(QWEN35B, QWEN35B_Q4_FILE, 98304, 2, "q4_0", True, spec.without_mtp()), M2_MAX_LIMIT)
        p = start_plan("auto", QWEN35B, QWEN35B_Q4_FILE, 98304, "auto", "q4_0", M2_MAX_LIMIT, spec)
        self.assertEqual((p.slots, p.spec.kind, p.dropped), (2, "ngram-mod", True))
        self.assertLess(parts.total, M3_PRO_LIMIT)                                     # the 36 GB M3 Pro keeps MTP

    def test_12b_full_sliding_window_cache_on_the_36gb_m3_pro(self) -> None:
        """2 x 96K, q4, the full cache, MTP from the drafter: the old estimate 25.7 GiB; every request failed
        ("Insufficient Memory"). Now 30.8 GiB: it does not fit, and cache.swa = auto takes the window cache."""
        spec = mtp_spec("draft-mtp,ngram-mod", 2, GEMMA12B, GEMMA12B_DRAFTER)
        full = need_bytes(GEMMA12B, GEMMA12B_FILE, 98304, 2, "q4_0", True, spec)
        self.assertGreater(full, M3_PRO_LIMIT)
        self.assertAlmostEqual(full / GIB, 30.8, delta=0.05)
        p = start_plan("auto", GEMMA12B, GEMMA12B_FILE, 98304, "auto", "q4_0", M3_PRO_LIMIT, spec)
        self.assertEqual((p.slots, p.swa_full, p.spec.kind), (2, False, "draft-mtp,ngram-mod"))
        self.assertLess(need_bytes(GEMMA12B, GEMMA12B_FILE, 98304, 2, "q4_0", False, spec), 10.5 * GIB)


class SpecTest(unittest.TestCase):
    def test_mtp_needs_a_head_or_a_drafter(self) -> None:
        self.assertEqual(mtp_spec("draft-mtp,ngram-mod", 2, SALUKI), Spec("draft-mtp,ngram-mod", 2))
        self.assertEqual(mtp_spec("draft-mtp", 2, GEMMA12B), Spec("ngram-mod", 2))           # no drafter file
        self.assertEqual(mtp_spec("draft-mtp", 2, GEMMA12B, 5).draft_bytes, 5)
        self.assertEqual(mtp_spec("draft-mtp", 1, SALUKI, 5).draft_bytes, 0)                # a head: no -md
        self.assertEqual(mtp_spec("ngram-mod", 0, None), Spec("ngram-mod", 1))
        self.assertEqual(Spec("draft-mtp,ngram-mod").words(), "MTP + n-gram")

    def test_a_drafter_shares_the_kv_cache_and_counts_its_weights(self) -> None:
        with_mtp = need_parts(GEMMA12B, GEMMA12B_FILE, 98304, 2, "q4_0", False, Spec("draft-mtp", 2, GEMMA12B_DRAFTER))
        self.assertEqual(with_mtp.drafter, GEMMA12B_DRAFTER)
        self.assertAlmostEqual(with_mtp.draft, 2 * with_mtp.compute)                  # no KV of its own

    def test_ubatch_and_the_margin(self) -> None:
        self.assertLess(compute_bytes(SALUKI, 65536, 1, 256), compute_bytes(SALUKI, 65536, 1, 512))
        p = need_parts(SALUKI, GIB, 65536)
        self.assertAlmostEqual(p.margin, (p.total - p.margin) * MARGIN)
        self.assertGreater(max_ctx(SALUKI, SALUKI_PLAIN_FILE, 10 * GIB, 1, "q4_0", True, Spec("none"), 256),
                           max_ctx(SALUKI, SALUKI_PLAIN_FILE, 10 * GIB, 1, "q4_0", True, Spec("none"), 512))


if __name__ == "__main__":
    unittest.main()
