"""Server log parsing (real llama.cpp lines) and the log tail."""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode = True                                  # keep tools/ free of __pycache__
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools"))

from monitor.logbook import LogBook, level_of, offset
from monitor.logtail import LogTail, log_start

REQUEST = """2.09.112.847 I slot launch_slot_: id  1 | task 118 | processing task, is_child = 0
2.35.285.030 I slot print_timing: id  1 | task 118 | prompt eval time =   20316.41 ms /   550 tokens (   36.94 ms per token,    27.07 tokens per second)
2.35.285.035 I slot print_timing: id  1 | task 118 |        eval time =    5855.24 ms /   134 tokens (   44.02 ms per token,    22.71 tokens per second)
2.35.285.063 I slot print_timing: id  1 | task 118 | draft acceptance = 0.51852 (   84 accepted /   162 generated), mean len =  2.68
2.35.285.790 I slot      release: id  1 | task 118 | stop processing: n_tokens = 685, truncated = 0
0.02.901.765 W srv          init: chat template supports preserving reasoning
5.00.000.000 E srv  send_error: task id = 9, error: ggml_metal: OutOfMemory
"""


ROUTER = """0.00.106.007 I srv  load_startup: (startup) loading model qwen3.6-35b-a3b-iq3
0.45.530.992 I srv          tick: evicting idle LRU name=qwen3.6-35b-a3b-iq3 for a queued request
0.45.531.014 I srv  ensure_model: waiting until model name=qwen3.8-9b-q4_k_m is fully loaded...
0.45.740.187 I srv  ensure_model: slot available, loading queued model name=qwen3.8-9b-q4_k_m
0.45.740.300 I srv          load: spawning server instance with name=qwen3.8-9b-q4_k_m on port 51808
[51808] 0.10.000.000 I slot launch_slot_: id  0 | task 2 | processing task, is_child = 0
0.55.900.000 I srv  proxy_reques: proxying request to model qwen3.8-9b-q4_k_m on port 51808
[51808] 0.12.500.000 I slot print_timing: id  0 | task 2 |        eval time =    1669.89 ms /    80 tokens (   21.14 ms per token,    47.31 tokens per second)
[51808] 0.12.600.000 I slot      release: id  0 | task 2 | stop processing: n_tokens = 97, truncated = 0
"""


SETTLE = """0.10.000.000 I slot launch_slot_: id  1 | task 200 | processing task, is_child = 0
0.10.069.000 I slot print_timing: id  1 | task 200 | prompt eval time =      69.53 ms /     1 tokens (   69.53 ms per token,    14.38 tokens per second)
0.10.069.001 I slot print_timing: id  1 | task 200 |        eval time =       0.00 ms /     1 tokens (    0.00 ms per token,     0.00 tokens per second)
0.10.070.000 I slot      release: id  1 | task 200 | stop processing: n_tokens = 684, truncated = 0
"""
CANCELLED = """0.20.000.000 I slot launch_slot_: id  0 | task 300 | processing task, is_child = 0
0.24.000.000 I slot print_timing: id  0 | task 300 | prompt processing, n_tokens =   4096, progress = 0.38, t =   6.29 s / 651.63 tokens per second
0.30.000.000 I slot print_timing: id  0 | task 300 | n_gen =    101, tg =  14.65 t/s, tg_3s =  15.10 t/s
0.31.000.000 W srv          stop: cancel task, id_task = 300
0.31.000.100 I slot      release: id  0 | task 300 | stop processing: n_tokens = 9000, truncated = 0
"""


class SpeedsTest(unittest.TestCase):
    """Phase 23.5 (the user: the dashboard "not always showing the speeds")."""

    def test_carls_own_requests_are_not_the_last_answer(self) -> None:
        book = LogBook()
        for line in (REQUEST.splitlines()[:5] + SETTLE.splitlines()):
            book.add(line)
        self.assertEqual([r.task for r in book.requests], [118, 200])
        last = book.last_answer()
        self.assertEqual((last.task, last.pp, last.tg) if last else None, (118, 27.07, 22.71))

    def test_a_cancelled_request_keeps_its_progress_speeds(self) -> None:
        book = LogBook()
        for line in CANCELLED.splitlines():
            book.add(line)
        r = book.last_answer()
        self.assertIsNotNone(r)
        assert r is not None
        self.assertEqual((r.task, r.cancelled, r.pp, r.tg, r.gen), (300, True, 651.63, 14.65, 101))

    def test_live_speeds_of_every_running_slot(self) -> None:
        book = LogBook()
        for line in ("0.40.000.000 I slot launch_slot_: id  0 | task 1 | processing task, is_child = 0",
                     "0.40.000.001 I slot launch_slot_: id  1 | task 2 | processing task, is_child = 0",
                     "0.44.000.000 I slot print_timing: id  0 | task 1 | prompt processing, n_tokens =   2048, progress = 0.2, t =   4.00 s / 512.00 tokens per second",
                     "0.45.000.000 I slot print_timing: id  1 | task 2 | n_gen =    50, tg =  20.00 t/s, tg_3s =  21.50 t/s"):
            book.add(line)
        self.assertEqual(book.live(), (512.0, 21.5))
        book.add("1.00.000.000 I srv  update_slots: something later")       # 15 s on: no longer now
        self.assertEqual(book.live(), (None, None))


class StatusTest(unittest.TestCase):
    """Phase 23.5 item 9: /carl/status's snapshot."""

    def test_reading_writing_and_after(self) -> None:
        book = LogBook()
        self.assertEqual(book.status()["state"], "idle")
        for line in CANCELLED.splitlines()[:2]:                         # a prompt being read: 38% at 651.63 tok/s
            book.add(line)
        st = book.status()
        self.assertEqual((st["state"], st["read"], st["write"], st["progress"]), ("reading", 651.6, None, 0.38))
        book.add(CANCELLED.splitlines()[2])                              # then writing at 15.1 tok/s (the last 3 s)
        st = book.status()
        self.assertEqual((st["state"], st["read"], st["write"]), ("writing", 651.6, 15.1))
        for line in REQUEST.splitlines()[:5]:
            book.add(line)
        book.add(CANCELLED.splitlines()[3])
        book.add(CANCELLED.splitlines()[4])
        st = book.status()
        self.assertEqual(st["state"], "idle")
        self.assertEqual(st["last"], {"read": 651.6, "write": 14.7})     # the stopped request's progress speeds


class RoutineTest(unittest.TestCase):
    def test_routine_warnings_are_notices(self) -> None:
        book = LogBook()
        for line in ("0.01.000.000 W srv          stop: cancel task, id_task = 70",
                     "0.01.000.001 W srv  llama_server: notice: server default port will be changed to :9931 in a future release",
                     "0.01.000.002 W slot create_check: id  1 | task 5 | erasing old context checkpoint (pos_min = 1)",
                     "0.01.000.003 W srv  something unexpected happened",
                     "0.01.000.004 E srv  send_error: task id = 9, error: boom"):
            book.add(line)
        self.assertEqual((book.counts["W"], book.counts["notice"], book.counts["E"]), (1, 3, 1))


class KnownTest(unittest.TestCase):
    """Phase 23.5 item 8: a known warning is a routine notice with its reason; an unknown one stays a warning."""

    def test_known_lines_and_their_reasons(self) -> None:
        from carl_core.domain.known_warnings import known
        book = LogBook()
        for line in ("0.01.000.000 W slot operator(): id  0 | task 5 | need to evaluate at least 1 token for each active slot",
                     "0.01.000.001 W slot operator(): id  0 | task 5 | n_past was set to 9256",
                     "0.01.000.002 W llama_kv_cache_iswa: using full-size SWA cache (ref: x)",
                     "0.01.000.003 W srv  operator(): unauthorized: Invalid API Key",
                     "0.01.000.004 W llama_context: n_ctx_seq (196608) > n_ctx_train (131072) -- possible training context overflow"):
            book.add(line)
        self.assertEqual((book.counts["notice"], book.counts["W"]), (3, 2))
        self.assertIn("in the slot already", known("n_past was set to 1") or "")
        self.assertIsNone(known("unauthorized: Invalid API Key"))

    def test_the_log_shows_the_reason_under_a_known_line(self) -> None:
        from monitor.cards import log_view
        from monitor.fmt import ANSI
        from test_cards import view as View
        book = LogBook()
        book.add("0.01.000.000 W slot create_check: id  1 | task 9 | erasing old context checkpoint (pos_min = 1)")
        book.add("0.01.000.001 W srv  send_error: task id = 9, error: the request exceeds the available context size")
        lines = [ANSI.sub("", x) for x in log_view(View(log=book, log_path="/x.log"), 20, 120)]
        i = next(n for n, x in enumerate(lines) if "erasing old context checkpoint" in x)
        self.assertEqual(lines[i + 1].strip(), "↳ known: llama.cpp drops the oldest checkpoint as a session grows")
        self.assertIn("exceeds the available context", lines[i + 2])     # an unknown warning: no reason line


class RouterLogTest(unittest.TestCase):
    """A router's log: its model servers' lines lose the [PORT] prefix and move onto the router's
    clock; its forwarding lines (the dashboard's own polls) are not shown; loads are recorded."""

    def test_router_lines(self) -> None:
        book = LogBook()
        for line in ROUTER.splitlines():
            book.add(line)
        r = book.requests[0]
        self.assertEqual((r.task, r.gen), (2, 80))
        self.assertAlmostEqual(r.t0 or 0, 45.740 + 10.0, places=2)        # the spawn time + the server's own
        self.assertAlmostEqual((r.t1 or 0) - (r.t0 or 0), 2.6, places=2)
        self.assertFalse(any(x.startswith("[") or "proxying" in x for x in book.lines))
        self.assertTrue(any(x.startswith("0.55.740.000 I slot launch_slot_") for x in book.lines))
        self.assertEqual([n for _, n in book.switches], ["qwen3.6-35b-a3b-iq3", "qwen3.8-9b-q4_k_m"])


class LogBookTest(unittest.TestCase):
    def setUp(self) -> None:
        self.book = LogBook()
        for line in REQUEST.splitlines():
            self.book.add(line)

    def test_a_finished_request(self) -> None:
        self.assertEqual(len(self.book.requests), 1)
        r = self.book.requests[0]
        self.assertEqual((r.task, r.slot, r.new, r.gen, r.ctx), (118, 1, 550, 134, 685))
        self.assertAlmostEqual(r.pp or 0, 27.07)
        self.assertAlmostEqual(r.tg or 0, 22.71)
        self.assertAlmostEqual(r.acc or 0, 0.51852)
        self.assertAlmostEqual((r.t1 or 0) - (r.t0 or 0), 26.173, places=3)
        self.assertEqual(self.book.current, {})

    def test_counts_and_errors(self) -> None:
        self.assertEqual((self.book.counts["W"], self.book.counts["notice"], self.book.counts["E"], self.book.counts["oom"]),
                         (0, 1, 1, 1))                         # the reasoning notice is routine
        self.assertEqual(len(self.book.errors), 1)

    def test_a_running_request(self) -> None:
        self.book.add("9.00.000.000 I slot launch_slot_: id  0 | task 241 | processing task")
        self.assertIn(241, self.book.current)

    def test_timestamps(self) -> None:
        self.assertEqual(offset("2.09.112.847 I x"), 129.112)
        self.assertIsNone(offset("no timestamp"))
        self.assertEqual(level_of("0.02.901.765 W srv"), "W")
        self.assertEqual(level_of("plain"), "")

    def test_wall_clock_from_the_log_start(self) -> None:
        self.assertEqual(self.book.wall(10), "--:--:--")
        self.book.start = time.mktime((2026, 10, 2, 14, 4, 5, 0, 0, -1))
        self.assertEqual(self.book.wall(65), "14:05:10")


class LogTailTest(unittest.TestCase):
    """LogTail reads a real file (adapter test)."""

    def test_incremental_reads_partial_lines_and_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "llama-server-20261002-140405.log")
            with open(path, "w") as f:
                f.write("0.00.000.001 I one\n0.00.000.002 \x1b[31mW\x1b[0m two\x1b]0;x\x07\n0.00.000.003 I thr")
            tail = LogTail()
            tail.update(path)
            self.assertEqual(list(tail.book.lines), ["0.00.000.001 I one", "0.00.000.002 W two]0;x"])
            self.assertEqual(tail.book.counts["W"], 1)
            self.assertEqual(tail.book.start, log_start(path))
            with open(path, "a") as f:
                f.write("ee\n")
            tail.update(path)
            self.assertEqual(tail.book.lines[-1], "0.00.000.003 I three")
            with open(path, "w") as f:                       # truncated: start over
                f.write("0.00.000.004 I four\n")
            tail.update(path)
            self.assertEqual(list(tail.book.lines), ["0.00.000.004 I four"])

    def test_no_file(self) -> None:
        tail = LogTail()
        tail.update(None)
        tail.update("/nonexistent/llama-server.log")
        self.assertEqual(list(tail.book.lines), [])


if __name__ == "__main__":
    unittest.main()
