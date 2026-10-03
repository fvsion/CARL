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
        self.assertEqual((self.book.counts["W"], self.book.counts["E"], self.book.counts["oom"]), (1, 1, 1))
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
