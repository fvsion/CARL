"""The server log's RAM cache lines and CARL's filter rule for a -lv 4 server (tools/carl_core/domain/serverlog.py,
Phase 23.4.4 item 12). The lines are in llama.cpp 0.6.0's form (--log-timestamps --log-prefix); the values are
made up."""
from __future__ import annotations

import unittest

import support  # noqa: F401  (puts tools/ on sys.path)
from carl_core.domain import serverlog as S

MIB = 2 ** 20
STATE = ("0.03.729.393 I srv        update:  - cache state: 3 prompts, 1536.250 MiB (limits: 8192.000 MiB, "
         "65536 tokens, 25831 est)")
# a request at -lv 4: what the log keeps (True) and drops (False)
REQUEST = [
    ("0.02.000.001 I srv  update_slots: all slots are idle", False),
    ("0.02.000.002 I srv  server_strea: conv_id= (empty=1)", False),
    ("0.02.000.003 I srv    operator(): chat format: peg-gemma4", False),
    ("0.02.000.004 I slot get_availabl: id  0 | task -1 |  - checking sim = 0.407 (11/27) > 0.100", False),
    ("0.02.000.005 I slot get_availabl: id  0 | task -1 | selected slot by LCP similarity, f_sim_best = 0.407", True),
    ("0.02.000.006 I srv  get_availabl: updating prompt cache", False),
    ("0.02.000.007 I srv   prompt_save:  - saving prompt with length 43, total state size = 2.353 MiB", True),
    ("0.02.000.008 I srv          load:  - looking for better prompt, base f_keep = 0.256, f_sim = 0.407", False),
    ("0.02.000.009 I srv          load:    - prompt with length      43, lcp =      11", False),
    (STATE, True),
    ("0.02.000.010 I srv        update:    - prompt 0x8e0c540b0:      43 tokens, checkpoints:  2,     3.409 MiB", False),
    ("0.02.000.011 I srv  get_availabl: prompt cache update took 0.38 ms", False),
    ("0.02.000.012 I slot launch_slot_: id  0 | task -1 | sampler params: ", False),
    ("n = 64, repeat_penalty = 1.000, frequency_penalty = 0.000", False),        # goes with the line before it
    ("0.02.000.013 I slot launch_slot_: id  0 | task 24 | processing task, is_child = 0", True),
    ("0.02.000.014 I slot   operator(): id  0 | task 24 | new prompt, n_ctx_slot = 8192, n_keep = 0", False),
    ("0.02.000.015 I slot   operator(): id  0 | task 24 | cached n_tokens = 7, memory_seq_rm [7, end)", False),
    ("0.02.000.016 I slot create_check: id  0 | task 24 | created context checkpoint 2 of 32", False),
    ("0.02.000.017 I slot init_sampler: id  0 | task 24 | init sampler, took 0.00 ms", False),
    ("0.02.000.018 I slot print_timing: id  0 | task 24 | prompt eval time =      95.87 ms /    20 tokens", True),
    ("0.02.000.019 I slot      release: id  0 | task 24 | stop processing: n_tokens = 66, truncated = 0", True),
    ("0.02.000.020 W srv  update_slots: all slots are idle (a warning of a kind that is dropped as info)", True),
    ("0.02.000.021 W srv   alloc: - making room for prompt cache entry, removing oldest entry (size = 994.843 MiB)", True),
    ("0.02.000.022 I srv  something_new: a line CARL does not know", True),
    ("a line without a prefix after a kept one", True),
]


class FilterTest(unittest.TestCase):
    def test_the_per_request_detail_goes_and_the_rest_stays(self) -> None:
        kept = True
        for line, want in REQUEST:
            kept = S.keep_line(line, kept)
            self.assertEqual(kept, want, line)

    def test_a_line_without_a_prefix_goes_with_the_line_before_it(self) -> None:
        self.assertTrue(S.keep_line("plain text", True))
        self.assertFalse(S.keep_line("plain text", False))


class CacheStateTest(unittest.TestCase):
    def test_the_state_line(self) -> None:
        self.assertEqual(S.cache_state(STATE), S.CacheState(prompts=3, bytes=int(1536.25 * MIB),
                                                            limit_bytes=8192 * MIB, limit_tokens=65536))
        one = STATE.replace("3 prompts, 1536.250", "1 prompts, 3.409")
        self.assertEqual(S.cache_state(one).prompts, 1)
        self.assertIsNone(S.cache_state("0.02.000.007 I srv   prompt_save:  - saving prompt with length 43"))

    def test_the_verbosity_on_the_command_line(self) -> None:
        self.assertTrue(S.counts_cache("llama-server -m x.gguf -lv 4 --port 8080"))
        self.assertTrue(S.counts_cache("llama-server --log-verbosity 5"))
        self.assertFalse(S.counts_cache("llama-server -m x.gguf --log-file a.log"))
        self.assertFalse(S.counts_cache("llama-server -lv 3"))


if __name__ == "__main__":
    unittest.main()
