"""tools/compaction-bench/compbench.py: the transcript read by code, the mask method, the checkpoints and the scores
(no server). Run: python3 -m unittest discover -s tests/compaction_bench -t tests/compaction_bench"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools", "compaction-bench"))
import compbench as cb  # noqa: E402


def call(cid: str, name: str, **args: object) -> dict:
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def transcript() -> list:
    """A small session: read, a failed test, an edit, a passing test."""
    return [
        {"role": "system", "content": "You are a coding agent."},
        {"role": "user", "content": "Fix mean_word_length in textstats/stats.py, don't touch the tests."},
        {"role": "assistant", "content": "", "tool_calls": [call("1", "read", filePath="/w/pylib/textstats/stats.py")]},
        {"role": "tool", "tool_call_id": "1", "content": "def mean_word_length(text):\n" + "    x = 1\n" * 300},
        {"role": "assistant", "content": "", "tool_calls": [call("2", "bash", command="python3 -m pytest -q")]},
        {"role": "tool", "tool_call_id": "2", "content": "F\nE   ZeroDivisionError: division by zero\n1 failed"},
        {"role": "assistant", "content": "", "tool_calls": [call("3", "edit", filePath="/w/pylib/textstats/stats.py",
                                                                 oldString="a", newString="b")]},
        {"role": "tool", "tool_call_id": "3", "content": "Edit applied."},
        {"role": "assistant", "content": "", "tool_calls": [call("4", "bash", command="python3 -m pytest -q")]},
        {"role": "tool", "tool_call_id": "4", "content": "." * 40 + "\n40 passed"},
    ]


class TranscriptTest(unittest.TestCase):
    def test_code_reads_the_files_the_error_and_the_paths(self) -> None:
        t = transcript()
        self.assertEqual(cb.files_changed(t), ["textstats/stats.py"])
        self.assertEqual(cb.last_error(t), "E   ZeroDivisionError: division by zero")
        self.assertIn("textstats/stats.py", cb.all_paths(t))
        self.assertEqual(cb.call_target(cb.tool_calls(t[4])[0]), "python3")
        self.assertEqual(cb.norm_path("./textstats/stats.py"), "textstats/stats.py")
        self.assertEqual(cb.norm_path("/Users/u/cb-work/projects/opencode-pycli/pycli-iyew2b0/notes/notes/store.py"),
                         "notes/store.py")                                # collect's copy of the project

    def test_an_error_is_a_commands_not_a_file_reads(self) -> None:
        t = transcript() + [
            {"role": "assistant", "content": "", "tool_calls": [call("5", "read", filePath="/w/pylib/textstats/cli.py")]},
            {"role": "tool", "tool_call_id": "5", "content": "    raise ValueError('no such file')"}]
        self.assertEqual(cb.last_error(t), "E   ZeroDivisionError: division by zero")
        out = cb.mask(t, keep_calls=0, keep_share=0.0)
        self.assertIn("ZeroDivisionError", out[5]["content"])             # the command's error stays
        self.assertTrue(out[11]["content"].startswith("[cleared"))        # the read that shows a raise does not


class MaskTest(unittest.TestCase):
    def test_the_two_way_guard(self) -> None:
        """The newest K results, the last share of the context and the latest error stay; the rest is one line."""
        t = transcript()
        out = cb.mask(t, keep_calls=1, keep_share=0.0)
        self.assertTrue(out[3]["content"].startswith("[cleared: the output of read textstats/stats.py"))
        self.assertIn("ZeroDivisionError", out[5]["content"])         # the latest error
        self.assertEqual(out[7]["content"], "[cleared: the output of edit textstats/stats.py; call it again to see it]")
        self.assertIn("40 passed", out[9]["content"])                   # the newest one
        self.assertEqual([m["role"] for m in out], [m["role"] for m in t])
        self.assertEqual(t[3]["content"][:24], "def mean_word_length(tex")   # the input untouched
        everything = cb.mask(t, keep_calls=0, keep_share=1.0)          # the whole context is the kept share
        self.assertEqual(everything, t)

    def test_methods_by_name(self) -> None:
        self.assertEqual(cb.method_fn("none")(transcript()), transcript())
        self.assertNotEqual(cb.method_fn("mask:1:0")(transcript()), transcript())
        with self.assertRaises(SystemExit):
            cb.method_fn("summary")


class CheckpointTest(unittest.TestCase):
    def test_the_chain_and_the_truth(self) -> None:
        t = transcript()
        reqs = [{"tools": [{}], "messages": t[:n]} for n in (2, 4, 6, 8, 10)]
        bodies = reqs[:2] + [{"messages": [{"role": "user", "content": "a title"}]}] + reqs[2:]
        self.assertEqual(len(cb.chain(bodies)), 5)                     # the title request is left out
        with tempfile.TemporaryDirectory() as d:
            for i, b in enumerate(bodies, 1):
                with open(os.path.join(d, f"chat-{i:03d}.json"), "w", encoding="utf-8") as f:
                    json.dump(b, f)
            self.assertEqual(len(cb.chain(cb.load_bodies(d))), 5)
        size = sum(cb.est_tokens(m) for m in t[:4])
        cps = cb.checkpoints("s", reqs, window=size * 2, at=[0.5])
        self.assertEqual([(c.index, c.truth["tool_calls"][0]["id"]) for c in cps], [(1, "2")])


class ScoreTest(unittest.TestCase):
    def test_next_step(self) -> None:
        t = transcript()
        self.assertEqual(cb.score_next(t[4], t[4])["target"], True)
        s = cb.score_next(t[2], t[4])
        self.assertEqual((s["tool"], s["target"], s["want"]), (False, False, "bash python3"))
        self.assertEqual(cb.score_next({"content": "done"}, t[4])["got"], "text")

    def test_probe(self) -> None:
        t = transcript()
        good = cb.parse_probe('Sure: {"files_changed": ["textstats/stats.py"], "last_error": '
                              '"E   ZeroDivisionError: division by zero", "next_step": "done"}')
        s = cb.score_probe(good, t)
        self.assertEqual((s["file_recall"], s["file_precision"], s["invented"], s["error"]), (1.0, 1.0, 0, True))
        bad = cb.score_probe({"files_changed": ["textstats/made_up.py"], "last_error": ""}, t)
        self.assertEqual((bad["file_recall"], bad["invented"], bad["error"]), (0.0, 1, False))
        self.assertFalse(cb.score_probe(cb.parse_probe("no json here"), t)["parsed"])

    def test_the_error_named_by_its_test_or_line(self) -> None:
        truth = "FAILED tests/test_store.py::StoreTest::test_mark_done - AssertionError: False..."
        self.assertTrue(cb.error_recalled("test_mark_done failed: mark_done returned False", truth))
        self.assertFalse(cb.error_recalled("a ZeroDivisionError", truth))
        self.assertTrue(cb.error_recalled("the assert at tests/test_longest.py:8", "tests/test_longest.py:8: AssertionError"))
        self.assertTrue(cb.error_recalled("", ""))

    def test_report(self) -> None:
        row = {"method": "none", "next": {"tool": True, "target": False},
               "probe": {"file_recall": 1.0, "file_precision": 0.5, "invented": 1, "error": True},
               "prompt_n": 30000, "read_ms": 60000}
        row = dict(row, session="s", index=1)
        table = cb.report([row, dict(row, method="mask:4:0.3")])
        self.assertEqual(table[1], ["none", "1", "100%", "0%", "", "", "", "1.00", "0.50", "1.0", "100%", "30,000",
                                    "30,000", "60.0"])
        self.assertEqual(table[2][4:6], ["+0% (+0% to +0%)", "1.00"])
        self.assertEqual(len(table), 3)

    def test_the_statistics(self) -> None:
        self.assertEqual(cb.mcnemar_p(0, 0), 1.0)
        self.assertAlmostEqual(cb.mcnemar_p(0, 6), 0.03125)          # 6 points only one way: 2 / 64
        rows = []
        for s in ("a", "b", "c"):
            for i in range(4):
                rows.append({"session": s, "index": i, "method": "none", "next": {"target": True}})
                rows.append({"session": s, "index": i, "method": "m", "next": {"target": i != 0}})
        pr = cb.paired(rows, "m")
        self.assertEqual((pr["n"], round(pr["diff"], 2)), (12, -0.25))
        self.assertLessEqual(pr["lo"], pr["diff"])

    def test_points_in_a_fill_range_and_parallel_calls(self) -> None:
        t = transcript()
        reqs = [{"tools": [{}], "messages": t[:n]} for n in (2, 4, 6, 8, 10)]
        total = sum(cb.est_tokens(m) for m in t[:8])
        pts = cb.points_in_range("s", reqs, window=total, lo=0.0, hi=1.0)
        self.assertEqual([p.index for p in pts], [0, 1, 2, 3])
        self.assertEqual([p.index for p in cb.points_in_range("s", reqs, window=total, lo=0.0, hi=1.0, every=2)], [0, 2])
        two = {"tool_calls": [call("a", "read", filePath="/w/pylib/x.py"), call("b", "read", filePath="/w/pylib/y.py")]}
        one = {"tool_calls": [call("c", "read", filePath="/w/pylib/y.py")]}
        self.assertTrue(cb.score_next(one, two)["target"])


if __name__ == "__main__":
    unittest.main()
