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
