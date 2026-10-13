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
        self.assertEqual(table[1], ["none", "1", "100%", "0%", "", "", "", "1.00", "0.50", "1.0", "100%", "0%", "0%",
                                    "30,000", ""])
        self.assertEqual(table[2][-1], "60")         # the event's re-read: 30,000 tokens at the measured 500 tokens/s
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


class ErrorProbeTest(unittest.TestCase):
    def test_the_error_in_any_form_and_from_a_diagnostic(self) -> None:
        """2026-10-13: the answers that named the failure by its message, not its test or line, were missed; an edit's
        LSP diagnostic is an error too; the probe lists every error, most recent first."""
        t = transcript()[:4] + [
            {"role": "assistant", "content": "", "tool_calls": [call("2", "bash", command="python3 -m pytest -q")]},
            {"role": "tool", "tool_call_id": "2",
             "content": "F\n____ test_longest ____\ntests/test_longest.py:8: AssertionError\n"
                        "E       AssertionError: 'a' != 'ccc'\n1 failed"}]
        keys = cb.error_keys(t)
        self.assertIn("test_longest", keys)
        said = cb.errors_said({"errors": [{"error": "AssertionError: 'a' != 'ccc' in longest_word", "fix": "max"}]})
        self.assertTrue(cb.error_recalled(said, cb.last_error(t), keys))
        self.assertFalse(cb.error_recalled("ZeroDivisionError", cb.last_error(t), keys))
        lsp = t + [
            {"role": "assistant", "content": "", "tool_calls": [call("3", "edit", filePath="/w/pylib/notes/cli.py")]},
            {"role": "tool", "tool_call_id": "3", "content": "Edit applied.\nLSP errors detected in this file:\n"
                                                             "ERROR [101:34] Cannot access attribute \"days_ago\""}]
        self.assertEqual(cb.error_result(lsp), 7)
        self.assertTrue(cb.error_recalled('ERROR [101:34] Cannot access attribute "days_ago" for class "date"',
                                          cb.last_error(lsp), cb.error_keys(lsp)))
        self.assertEqual(cb.errors_said({"last_error": "x"}), "x")                       # an older answer
        self.assertTrue(cb.error_recalled("", "", []))                                    # none, and none said


class CollectTest(unittest.TestCase):
    def test_briefs_sizes_and_the_clients_own_compaction(self) -> None:
        class P:
            def __init__(self, text: str) -> None:
                self.text = text
        prompts = {"a": P("Do A."), "b": P("Do B.")}
        self.assertEqual(cb.item_text("brief:a,b", prompts), cb.BRIEF + "1. Do A.\n\n2. Do B.")
        self.assertEqual(cb.item_text("brief:Read it, then fix it.", prompts), cb.BRIEF + "1. Read it, then fix it.")
        self.assertEqual(cb.item_text("a", prompts), "Do A.")
        self.assertGreater(cb.body_tokens({"messages": transcript(), "tools": [{"x": "y" * 360}]}), 100)
        with tempfile.TemporaryDirectory() as home:
            for d, name, doc in ((".config/opencode", "opencode.json", {"provider": {}}),
                                 (".pi/agent", "settings.json", {"defaultThinkingLevel": "low"})):
                os.makedirs(os.path.join(home, d))
                with open(os.path.join(home, d, name), "w", encoding="utf-8") as f:
                    json.dump(doc, f)
            self.assertEqual(len(cb.client_compaction_off(home)), 2)
            self.assertEqual(cb.client_compaction_off(home), [])                          # already off
            with open(os.path.join(home, ".config/opencode/opencode.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"provider": {}, "compaction": {"auto": False, "prune": False}})


def long_session() -> list:
    """Two user turns: the old one reads two files and runs the tests; the current one reads and changes stats.py,
    lists the project, runs the tests, then reads cli.py. Each read ~2,800 tokens (10K chars)."""
    big = lambda name: "\n".join(f"{n}: line of {name}" + "x" * 60 for n in range(1, 140))   # noqa: E731
    fail = "F.\nFAILED tests/test_stats.py::StatsTest::test_mean - AssertionError: 1 != 2\n1 failed, 1 passed in 0.1s"
    return [
        {"role": "system", "content": "You are a coding agent."},
        {"role": "user", "content": "Review the project."},
        {"role": "assistant", "content": "", "tool_calls": [call("1", "read", filePath="/w/pylib/textstats/tokenize.py")]},
        {"role": "tool", "tool_call_id": "1", "content": big("tokenize")},
        {"role": "assistant", "content": "", "tool_calls": [call("2", "read", filePath="/w/pylib/textstats/stats.py")]},
        {"role": "tool", "tool_call_id": "2", "content": big("stats")},
        {"role": "assistant", "content": "", "tool_calls": [call("3", "bash", command="cd /w/pylib && python3 -m pytest -q")]},
        {"role": "tool", "tool_call_id": "3", "content": fail + "\n" + "." * 4000},
        {"role": "assistant", "content": "Two notes."},
        {"role": "user", "content": "Fix mean in stats.py."},
        {"role": "assistant", "content": "", "tool_calls": [call("4", "read", filePath="/w/pylib/textstats/stats.py")]},
        {"role": "tool", "tool_call_id": "4", "content": big("stats")},
        {"role": "assistant", "content": "", "tool_calls": [call("5", "glob", pattern="**/*.py")]},
        {"role": "tool", "tool_call_id": "5", "content": "\n".join(f"/w/pylib/textstats/m{n}.py" for n in range(400))},
        {"role": "assistant", "content": "", "tool_calls": [call("6", "edit", filePath="/w/pylib/textstats/stats.py",
                                                                 oldString="a", newString="b")]},
        {"role": "tool", "tool_call_id": "6", "content": "Edit applied successfully."},
        {"role": "assistant", "content": "", "tool_calls": [call("7", "bash", command="python3 -m pytest -q")]},
        {"role": "tool", "tool_call_id": "7", "content": "..\n2 passed in 0.1s"},
        {"role": "assistant", "content": "", "tool_calls": [call("8", "read", filePath="/w/pylib/textstats/cli.py")]},
        {"role": "tool", "tool_call_id": "8", "content": big("cli")},
    ]


class EventTest(unittest.TestCase):
    def test_the_shape_and_the_drain_line(self) -> None:
        """2026-10-13, agreed: under the drain line nothing changes; above it the old part goes first, the current
        turn's user message and the working tail stay, the newest view of a file the turn changes stays (budget)."""
        t = long_session()
        size = sum(cb.est_tokens(m) for m in t)
        self.assertEqual(cb.event(t, 0, int(size / 0.39), 1, 0.2, True), t)            # under 40%: nothing
        window = int(size / 0.7)                                                          # the event at ~70%
        out = cb.event(t, 0, window, 1, 0.1, True)
        self.assertTrue(out[3]["content"].startswith("[pruned] read textstats/tokenize.py: 139 lines."))
        self.assertIn("The file changed after this read. This data is old.", out[5]["content"])
        self.assertEqual(out[11]["content"], t[11]["content"])        # stats.py's newest view: the turn changes it
        self.assertEqual(out[9], t[9])                                # the current turn's user message
        self.assertEqual(out[19]["content"], t[19]["content"])        # the working tail (cli.py's read)
        self.assertLessEqual(sum(cb.est_tokens(m) for m in out), 0.4 * window + 200)
        self.assertEqual([m["role"] for m in out], [m["role"] for m in t])
        self.assertEqual(t[3]["content"][:3], "1: ")                  # the input untouched

    def test_a_changing_file_over_its_budget(self) -> None:
        t = long_session()
        window = int(sum(cb.est_tokens(m) for m in t) / 0.95)
        out = cb.event(t, 0, window, 1, 0.05, True)                  # 10% of this window < one read: over the budget
        self.assertIn("You changed this file in this task. Read the file again before you change it.", out[11]["content"])

    def test_placeholders_in_ste100(self) -> None:
        cmd = lambda c: {"name": "bash", "args": {"command": c}}           # noqa: E731
        fail = "FAILED tests/test_stats.py::StatsTest::test_mean - AssertionError\n1 failed, 33 passed in 0.1s"
        self.assertEqual(cb.placeholder(cmd("cd /w && python3 -m pytest -q"), fail + "\n\nCommand exited with code 1",
                                        False, False),
                         '[pruned] bash "python3 -m pytest -q": exit 1. 1 test failed: test_mean. 33 tests passed. '
                         "Do the tests again to see their status.")
        self.assertEqual(cb.placeholder(cmd("git commit -am x"), "[main 1a2b] x", False, False),
                         '[pruned] bash "git commit -am x": This command changed the project. Do not do this command '
                         "again.")
        self.assertEqual(cb.placeholder(cmd("python3 -m notes list"), "a\n3 notes\n", False, False),
                         '[pruned] bash "python3 -m notes list": Last line: "3 notes".')
        self.assertTrue(cb.placeholder(cmd("python3 x.py"), "Traceback ...\nKeyError: 'due'", False, False)
                        .endswith("Error: KeyError: 'due'"))
        self.assertEqual(cb.placeholder({"name": "glob", "args": {"pattern": "*.py"}}, "/w/pylib/a.py\n/w/pylib/b.py",
                                        False, False), '[pruned] glob "*.py": 2 results. The first 2: a.py, b.py.')
        edit = {"name": "edit", "args": {"filePath": "/w/pylib/notes/cli.py"}}
        self.assertEqual(cb.placeholder(edit, "Edit applied successfully.\n<diagnostics>\nERROR [3:1] x\nERROR [9:2] y",
                                        False, False),
                         "[pruned] edit notes/cli.py: The change is done. The tool reported 2 errors.")
        self.assertEqual(cb.placeholder(edit, "edits[1].oldText must not be empty", False, False),
                         '[pruned] edit notes/cli.py: The change failed: "edits[1].oldText must not be empty".')
        self.assertEqual(cb.placeholder({"name": "webfetch", "args": {"url": "u"}}, "x\ny", False, False),
                         "[pruned] webfetch u: 2 lines.")

    def test_thresholds_methods_and_repeats(self) -> None:
        t = long_session()
        window = int(sum(cb.est_tokens(m) for m in t) / 0.7)
        out = cb.event(t, 0, window, 1, 0.1, True)
        self.assertEqual(out[15]["content"], t[15]["content"])        # an edit's result (under 80 tokens): never
        plain = cb.method_fn("mask:1:0.1+turn", window)(t)
        self.assertTrue(plain[3]["content"].startswith("[cleared: the output of read"))
        self.assertEqual(cb.method_fn("mask:1:0.1+turn+ph=facts", window)(t), out)
        with self.assertRaises(SystemExit):
            cb.method_fn("mask:8:0.2+ph=facts")                       # the placeholders go with the event's shape
        again = {"tool_calls": [call("9", "read", filePath="/w/pylib/textstats/tokenize.py")]}
        self.assertTrue(cb.repeated(out, again))
        self.assertFalse(cb.repeated(out, {"tool_calls": [call("9", "read", filePath="/w/pylib/textstats/x.py")]}))


class ArgsTest(unittest.TestCase):
    def test_old_edit_and_write_arguments(self) -> None:
        """#4 (agreed 2026-10-13): an edit's or a write's strings become "[pruned: N lines]" (names, paths and other
        arguments kept; Pi's edits[] too), in the old part with its results; never in the tail; under 80 tokens they
        stay; a stub copied into the next call counts as imitated."""
        self.assertEqual(cb.stub_args({"filePath": "/w/a.py", "oldString": "a\nb", "newString": "c", "replaceAll": True}),
                         {"filePath": "/w/a.py", "oldString": "[pruned: 2 lines]", "newString": "[pruned: 1 lines]",
                          "replaceAll": True})
        self.assertEqual(cb.stub_args({"path": "a.py", "edits": [{"oldText": "x", "newText": "y\nz"}]}),
                         {"path": "a.py", "edits": [{"oldText": "[pruned: 1 lines]", "newText": "[pruned: 2 lines]"}]})
        t = long_session()
        code = "\n".join(f"def f{n}(): return {n}" for n in range(60))
        t.insert(2, {"role": "assistant", "content": "", "tool_calls": [call("w", "write", filePath="/w/pylib/new.py",
                                                                                content=code)]})
        t.insert(3, {"role": "tool", "tool_call_id": "w", "content": "Wrote file successfully."})
        t.insert(4, {"role": "assistant", "content": "", "tool_calls": [call("s", "edit", filePath="/w/pylib/x.py",
                                                                                oldString="a", newString="b")]})
        t.insert(5, {"role": "tool", "tool_call_id": "s", "content": "Edit applied successfully."})
        window = int(sum(cb.est_tokens(m) for m in t) / 0.7)
        out = cb.event(t, 0, window, 1, 0.1, True, True)
        self.assertEqual(json.loads(out[2]["tool_calls"][0]["function"]["arguments"]),
                         {"filePath": "/w/pylib/new.py", "content": "[pruned: 60 lines]"})
        self.assertEqual(out[4], t[4])                                # a small edit (under 80 tokens) stays
        self.assertIn("def f0", t[2]["tool_calls"][0]["function"]["arguments"])        # the input untouched
        self.assertEqual(out, cb.method_fn("mask:1:0.1+turn+ph=facts+args", window)(t))
        with self.assertRaises(SystemExit):
            cb.method_fn("mask:8:0.2+args")
        self.assertTrue(cb.imitated({"tool_calls": [call("9", "edit", filePath="a", oldString="[pruned: 3 lines]")]}))
        self.assertFalse(cb.imitated({"tool_calls": [call("9", "edit", filePath="a", oldString="x = 1")]}))


class ThinkTest(unittest.TestCase):
    def test_the_remnant(self) -> None:
        """2026-10-13 (user): "the last 50 tokens when it is a whole sentence the last 100 when it's a list of other
        format"."""
        prose = "First I looked at the store. " * 20 + "The KeyError comes from a missing due key. I will add a default."
        out = cb.think_last(prose)
        self.assertTrue(out.startswith(f"[pruned: {int(len(prose) / cb.CHARS_PER_TOKEN)} tokens] "))
        self.assertTrue(out.endswith("The KeyError comes from a missing due key. I will add a default."))
        self.assertLessEqual(len(out.split("] ", 1)[1]), cb.THINK_SENTENCES * cb.CHARS_PER_TOKEN)
        lst = "Plan:\n" + "\n".join(f"- step {n}: check module {n}" for n in range(60))
        tail = cb.think_last(lst).split("] ", 1)[1]
        self.assertTrue(tail.startswith("- step ") and tail.endswith("check module 59"))
        self.assertLessEqual(len(tail), cb.THINK_OTHER * cb.CHARS_PER_TOKEN)
        self.assertEqual(cb.est_tokens({"role": "assistant", "content": "", "reasoning_content": "x" * 335}), 100)

    def test_old_thinking_first_the_current_turn_kept_never_the_tail(self) -> None:
        t = long_session()
        why = "I compare the two files. " * 40 + "So I will read stats.py next."
        for i in (2, 4, 16, 18):                     # two in the old part, two in the current turn
            t[i] = dict(t[i], reasoning_content=why)
        window = int(sum(cb.est_tokens(m) for m in t) / 0.405)         # just over the line: the thinking alone drains it
        out = cb.event(t, 0, window, 1, 0.05, True, True, "last")
        self.assertTrue(out[2]["reasoning_content"].startswith("[pruned: "))
        self.assertTrue(out[2]["reasoning_content"].endswith("So I will read stats.py next."))
        self.assertEqual(out[3]["content"], t[3]["content"])          # the old results wait: the thinking went first
        self.assertEqual(out[16]["reasoning_content"], why)          # the current turn's: kept (the baseline)
        empty = cb.event(t, 0, int(sum(cb.est_tokens(m) for m in t) / 0.9), 1, 0.05, True, True, "empty", True)
        self.assertEqual(empty[2]["reasoning_content"], "")
        self.assertEqual(empty[16]["reasoning_content"], "")         # +thinkmid: the current turn's middle too
        self.assertEqual(empty[18]["reasoning_content"], why)        # the tail: never
        self.assertEqual(cb.method_fn("mask:1:0.05+turn+ph=facts+args+think=last", window)(t), out)
        for bad in ("mask:8:0.2+turn+thinkmid", "mask:8:0.2+turn+think=last+think=empty", "mask:8:0.2+think=last"):
            with self.assertRaises(SystemExit):
                cb.method_fn(bad)
        row = lambda m, ok: {"session": "s", "index": 0, "fraction": 0.62, "method": m,       # noqa: E731
                             "next": {"target": ok, "want": "read textstats/stats.py"}, "next_got": []}
        page = "\n".join(cb.sample([row("mask:1:0.05+turn+ph=facts+args", True),
                                    row("mask:1:0.05+turn+ph=facts+args+think=last", False)],
                                   {"s": [{"messages": t}]}, "mask:1:0.05+turn+ph=facts+args",
                                   "mask:1:0.05+turn+ph=facts+args+think=last", window=window))
        self.assertIn("the real next step: read textstats/stats.py", page)
        self.assertIn("kept: [pruned: ", page)
        inline = {"role": "assistant", "content": "<think>\n" + why + "\n</think>\nDone."}
        self.assertEqual(cb.thinking(inline).strip(), why.strip())
        cb._set_thinking(inline, "")
        self.assertEqual(inline["content"], "\nDone.")


def note_session() -> list:
    """A session on a two-file project: read, a failing test, an edit, a new file, a passing run, a todo list, an
    edit's diagnostic; then a second user turn."""
    ok = "Edit applied successfully."
    return [
        {"role": "system", "content": "You are a coding agent."},
        {"role": "user", "content": "Fix mean."},
        {"role": "assistant", "content": "", "tool_calls": [call("1", "read", filePath="/w/pylib/textstats/stats.py")]},
        {"role": "tool", "tool_call_id": "1", "content": "<path>/w/pylib/textstats/stats.py</path>\n<type>file</type>\n"
         "<content>\n1: def mean(xs):\n2:     return sum(xs) / len(xs)\n\n(End of file - total 2 lines)\n</content>"},
        {"role": "assistant", "content": "", "tool_calls": [call("2", "bash", command="python3 -m pytest -q")]},
        {"role": "tool", "tool_call_id": "2", "content": "FAILED tests/test_stats.py::StatsTest::test_mean - "
         "ZeroDivisionError: division by zero\n1 failed, 3 passed in 0.1s"},
        {"role": "assistant", "content": "", "tool_calls": [call("3", "edit", filePath="/w/pylib/textstats/stats.py",
                                                                 oldString="    return sum(xs) / len(xs)",
                                                                 newString="    return sum(xs) / len(xs) if xs else 0.0")]},
        {"role": "tool", "tool_call_id": "3", "content": ok},
        {"role": "assistant", "content": "", "tool_calls": [call("4", "write", filePath="/w/pylib/textstats/ngrams.py",
                                                                 content="class Ngrams:\n    def top(self):\n        return []\n")]},
        {"role": "tool", "tool_call_id": "4", "content": "Wrote file successfully.\nLSP errors detected in this file:\n"
                                                         "ERROR [3:9] Return type mismatch"},
        {"role": "assistant", "content": "", "tool_calls": [call("5", "bash", command="python3 -m pytest -q")]},
        {"role": "tool", "tool_call_id": "5", "content": "....\n4 passed in 0.1s"},
        {"role": "assistant", "content": "", "tool_calls": [call("6", "todowrite", todos=[
            {"content": "Fix mean", "status": "completed"}, {"content": "Add ngrams", "status": "in_progress"}])]},
        {"role": "tool", "tool_call_id": "6", "content": "2 todos"},
        {"role": "assistant", "content": "Done."},
        {"role": "user", "content": "Now add tests for ngrams."},
        {"role": "assistant", "content": "", "tool_calls": [call("7", "read", filePath="/w/pylib/textstats/ngrams.py")]},
        {"role": "tool", "tool_call_id": "7", "content": "class Ngrams:\n    def top(self):\n        return []\n"},
    ]


class NoteTest(unittest.TestCase):
    PROJECT = {"textstats/stats.py": "def mean(xs):\n    return sum(xs) / len(xs)\n",
               "textstats/tokenize.py": "def words(t):\n    return t.split()\n", "README.md": "# x\n"}

    def test_the_replay(self) -> None:
        """The bench's stand-in for the disk: the fixture, then whole reads, writes and applied edits, in order."""
        r = cb.Replay(note_session(), self.PROJECT)
        self.assertEqual(r.state["textstats/stats.py"], "def mean(xs):\n    return sum(xs) / len(xs) if xs else 0.0\n")
        self.assertEqual((r.status["textstats/stats.py"], r.status["textstats/ngrams.py"]), ("M", "C"))
        self.assertEqual(r.edits["textstats/stats.py"], 1)
        self.assertEqual(cb.read_text("x", {"offset": 10}), None)                      # a part is not the file
        self.assertEqual(cb.diagnostics('Wrote file successfully.\nLSP errors detected in other files:\n<diagnostics '
                                        'file="/w/pylib/notes/cli.py">\nERROR [96:28] Argument of type\n</diagnostics>',
                                        "CHANGES.md"), {"notes/cli.py": ["ERROR [96:28] Argument of type"]})
        gone = note_session() + [
            {"role": "assistant", "content": "", "tool_calls": [call("8", "read", filePath="/w/pylib/store.py")]},
            {"role": "tool", "tool_call_id": "8", "content": "Error: File not found: /w/pylib/store.py"}]
        self.assertNotIn("store.py", cb.Replay(gone, self.PROJECT).status)

    def test_the_note(self) -> None:
        """2026-10-13, agreed: Files, Errors (open or fixed), Tests, Todo, Map, Working set; STE100; before the
        current turn's user message; the reads of the files it holds pruned first."""
        t = note_session()
        note, held = cb.carl_note(t, self.PROJECT, 15, 98304, True, True, True)
        self.assertTrue(note.startswith("[CARL note] This note shows the state of this session"))
        self.assertIn("- textstats/stats.py: changed, 2 lines, 1 change", note)
        self.assertIn("- textstats/ngrams.py: created, 3 lines, 1 change", note)
        self.assertIn("- fixed: test_mean failed: ZeroDivisionError: division by zero", note)
        self.assertIn("- open: textstats/ngrams.py: ERROR [3:9] Return type mismatch", note)
        self.assertIn('Tests (the last run, "python3 -m pytest -q"): 4 tests passed. Fixed since an earlier failure: '
                      "test_mean.", note)
        self.assertIn("- [in_progress] Add ngrams", note)
        self.assertIn("textstats/ngrams.py: class Ngrams 1, Ngrams.top 2", note)
        self.assertIn("textstats/tokenize.py: words 1", note)
        self.assertIn("Other files: README.md.", note)
        self.assertIn("== textstats/stats.py (2 lines; 1 change in this session).\n1: def mean(xs):\n2:     return sum(xs) "
                      "/ len(xs) if xs else 0.0", note)
        self.assertEqual(held, ["textstats/ngrams.py", "textstats/stats.py"])
        # a file whose current view is in the working tail is not copied again (2026-10-13: "Count a file once")
        self.assertEqual(cb.tail_views(t, 16), {"textstats/ngrams.py"})
        self.assertEqual(cb.carl_note(t, self.PROJECT, 15, 98304, True, True, False, start=16)[1], ["textstats/stats.py"])
        self.assertEqual(cb.carl_note(t, self.PROJECT, 15, 98304, True, True, False, start=16, scope="turn")[1], [])
        self.assertIn("1: class Ngrams:", note)
        # too small to drain: the excess in the old part is stage 2's (the whole note stays); in the current turn (a
        # coder-style session: one user message) the working set yields, then the map
        small = cb.method_fn("mask:1:0.01+turn+notes+files", int(sum(cb.est_tokens(m) for m in t) / 0.5))(
            t, 0, project=self.PROJECT)
        self.assertIn("== textstats/stats.py", next(m["content"] for m in small if str(m["content"]).startswith("[C")))
        coder = t[:15]
        small = cb.method_fn("mask:1:0.01+turn+notes+files+map", int(sum(cb.est_tokens(m) for m in coder) / 0.5))(
            coder, 0, project=self.PROJECT)
        note = next(m["content"] for m in small if str(m["content"]).startswith("[C"))
        self.assertNotIn("== textstats/", note)
        self.assertNotIn("Map of the code", note)
        self.assertIn("Files of this session:", note)
        t[13] = dict(t[13], content="2 todos\n" + "todo detail line\n" * 400)       # room to prune in the old part
        window = int(sum(cb.est_tokens(m) for m in t) / 0.5)
        out = cb.method_fn("mask:1:0.01+turn+ph=facts+notes+files+map", window)(t, 0, project=self.PROJECT)
        self.assertEqual(out[15]["content"][:11], "[CARL note]")                   # before the turn's user message
        self.assertEqual(out[16], t[15])
        self.assertEqual(out[3], t[3])                                # with ph=facts: under 80 tokens, it stays
        out = cb.method_fn("mask:1:0.01+turn+notes+files", window)(t, 0, project=self.PROJECT)
        self.assertEqual(out[3]["content"], "[pruned] read textstats/stats.py: 2 lines. The current contents of this "
                                            "file are in the CARL note.")


class ContinuationTest(unittest.TestCase):
    def test_a_cut_tool_call_in_the_templates_text(self) -> None:
        """continuation/continue_test3.py: a tool call cut while streaming, rebuilt as the A3B template writes a call
        (each parameter in its own block, the last one left open at the cut)."""
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools",
                                        "compaction-bench", "continuation"))
        import continue_test3 as ct
        cut = '{"filePath": "/w/a.py", "oldString": "def a():\\n    retu'
        self.assertEqual(ct.partial_args(cut), [("filePath", "/w/a.py", True), ("oldString", "def a():\n    retu", False)])
        self.assertEqual(ct.as_template("edit", cut, False), "<tool_call>\n<function=edit>\n<parameter=filePath>\n/w/a.py"
                                                            "\n</parameter>\n<parameter=oldString>\ndef a():\n    retu")
        self.assertEqual(ct.partial_args('{"n": 3, "s": "x\\'), [("n", "3", True), ("s", "x", False)])   # a cut escape
        self.assertEqual(ct.partial_args('{"path'), [])


class DriverTest(unittest.TestCase):
    def test_pi_starts_at_the_thinking_level(self) -> None:
        """2026-10-13: the matrix's Pi driver starts Pi with thinking off; the bench's starts it at the level asked
        (default: CARL's, the defaultThinkingLevel of the harness's settings.json)."""
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools", "agent-bench"))
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools", "cache-matrix"))
        import cachematrix as cm
        seen = []

        class FakeChild:
            def __init__(self, argv, *a, **k) -> None:
                seen.append(argv)
        was = cm.Child
        cm.Child = FakeChild
        try:
            with tempfile.TemporaryDirectory() as home:
                os.makedirs(os.path.join(home, ".pi", "agent"))
                with open(os.path.join(home, ".pi", "agent", "settings.json"), "w", encoding="utf-8") as f:
                    json.dump({"defaultThinkingLevel": "low"}, f)
                cb._drivers("pi", home, home, {}, "m").start()
                cb._drivers("pi", home, home, {}, "m", "off").start()
        finally:
            cm.Child = was
        self.assertTrue(seen[0][-1].endswith("/m:low"), seen[0])
        self.assertTrue(seen[1][-1].endswith("/m:off"), seen[1])


if __name__ == "__main__":
    unittest.main()
