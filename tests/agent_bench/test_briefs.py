"""The coder briefs of a run (Phase 23.4.3): their full text, format and CARL's check (through node and the repo's
carl-brief.js), the refusals, a decision run that goes on after a refusal, the tokens (the fake server's /tokenize,
and bench.py tokens afterwards), the result line and the report's table. No model, no client."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import Any, Dict, List, Optional

import _support as sp

import fakeserver as fs
from agentbench import briefs as bf
from agentbench import clients as cl
from agentbench import events as ev
from agentbench import report, results

NODE = shutil.which("node")
REPO = bf.REPO
TOML = sp.BRIEF
JSON_BRIEF = json.dumps({
    "mode": "code", "tests": "new", "goal": "Add a readability module to textstats.",
    "scope": {"in": [{"text": "textstats/readability.py"}], "out": [{"text": "the existing tests", "why": "they pass"}]},
    "file": [{"path": "textstats/readability.py", "action": "create"}],
    "requirement": [{"id": "R1", "text": "flesch_reading_ease"}],
    "check": [{"id": "A1", "covers": ["R1"], "run": "python3 -m pytest -q", "expect": "all pass"}]}, indent=2)
KV = "Mode: code\nGoal: Add a readability module.\nFiles: textstats/readability.py\nRequirements: flesch_reading_ease"
REFUSAL = "[CARL] Brief refused: the coder did not start. Fix these points and send the whole brief again:\n- x"


def call(name: str, args: Dict[str, Any], at: float = 1.0, result: Optional[str] = None) -> ev.ToolCall:
    return ev.ToolCall(name, args, at, result=result)


class CoderTextsTest(unittest.TestCase):
    def test_each_form(self) -> None:
        self.assertEqual(bf.coder_texts("opencode", "task", {"subagent_type": "coder", "prompt": "P"}), [("prompt", "P")])
        self.assertEqual(bf.coder_texts("opencode", "task", {"subagent_type": "carl-coder", "prompt": "P"}),
                         [("prompt", "P")])
        self.assertEqual(bf.coder_texts("opencode", "task", {"subagent_type": "explore", "prompt": "P"}), [])
        self.assertEqual(bf.coder_texts("pi", "subagent", {"agent": "coder", "task": "T"}), [("task", "T")])
        args = {"tasks": [{"agent": "scout", "task": "a"}, {"agent": "coder", "task": "b"}],
                "chain": [{"agent": "coder", "task": "c {previous}"}]}
        self.assertEqual(bf.coder_texts("pi", "subagent", args), [("tasks[1]", "b"), ("chain[0]", "c {previous}")])
        self.assertEqual(bf.coder_texts("pi", "read", {"agent": "coder"}), [])

    def test_refusal_mark(self) -> None:
        self.assertTrue(ev.is_refusal(REFUSAL))
        self.assertTrue(ev.is_refusal("Error: " + REFUSAL))                   # OpenCode's error text
        self.assertFalse(ev.is_refusal("Started in the background: bg-1 (coder)"))
        self.assertFalse(ev.is_refusal("The coder said: " + REFUSAL[:5]))
        self.assertFalse(ev.is_refusal(None))
        self.assertFalse(ev.is_refusal("a long first line that is not a label at all, and then " + REFUSAL))


@unittest.skipUnless(NODE, "node is not installed")
class CheckTest(unittest.TestCase):
    def test_formats_and_problems(self) -> None:
        bad_toml = TOML.replace('goal = "Add a readability module to textstats."', "goal = oops")
        no_check = TOML.split("[[check]]")[0]
        out = bf.check_texts([TOML, "Here it is:\n```json\n" + JSON_BRIEF + "\n```", KV, "Please add it.", bad_toml,
                              no_check, '{"mode": "code", "goal": oops}'])
        self.assertEqual([o["format"] for o in out], ["toml", "json", "kv", "other", "toml", "toml", "json"])
        self.assertEqual([o["valid"] for o in out], [True, True, False, False, False, False, False])
        self.assertEqual(out[0]["problems"], [])
        self.assertRegex(out[4]["problems"][0], r"^line 3: this value has no quotes")
        self.assertIn("The brief has no check", out[5]["problems"][0])
        self.assertRegex(out[6]["problems"][0], r"^line 1: this value has no quotes")

    def test_collect(self) -> None:
        tools = [call("read", {"filePath": "a.py"}),
                 call("task", {"subagent_type": "coder", "prompt": KV}, 2.0, "Error: " + REFUSAL),
                 call("task", {"subagent_type": "coder", "prompt": TOML}, 3.0, '<task id="x" state="running">'),
                 call("task", {"subagent_type": "coder", "prompt": "go on", "task_id": "ses_1"}, 4.0, "ok")]
        got = bf.collect("opencode", tools)
        self.assertEqual(len(got), 3)
        self.assertEqual(got[0]["text"], KV)
        self.assertEqual((got[0]["format"], got[0]["refused"], got[0]["valid"]), ("kv", True, False))
        self.assertEqual((got[1]["format"], got[1]["refused"], got[1]["valid"]), ("toml", False, True))
        self.assertEqual(got[1]["text"], TOML)                                 # the full text, never cut
        self.assertTrue(got[2]["continues"])
        pi = bf.collect("pi", [call("subagent", {"tasks": [{"agent": "coder", "task": TOML * 3}]})])
        self.assertEqual(len(pi[0]["text"]), len(TOML) * 3)
        self.assertFalse(pi[0]["refused"])                                     # no result yet: not refused

    def test_one_checker_the_command_of_the_client_plugins(self) -> None:
        """The harness runs CARL's brief check command (client/shared/carl-brief-check.mjs, installed with the client
        plugins) in its --json mode: the same answer as the command gives a user."""
        self.assertEqual(os.path.relpath(bf.BRIEF_CLI, REPO), os.path.join("client", "shared", "carl-brief-check.mjs"))
        p = subprocess.run([NODE, bf.BRIEF_CLI, "-"], input=TOML, capture_output=True, text=True, timeout=60)
        self.assertEqual((p.returncode, p.stdout), (0, "Format: TOML\nThe brief is valid.\n"), p.stderr)
        self.assertTrue(bf.check_texts([TOML])[0]["valid"])

    def test_without_node(self) -> None:
        out = bf.check_texts(["x"], node="/nonexistent/node")
        self.assertEqual(out[0]["format"], "unknown")
        self.assertIsNone(out[0]["valid"])


class GoesOnAfterARefusalTest(unittest.TestCase):
    """A decision run: the decision is the first call to the coder; the run goes on while CARL refused its newest
    brief, so the result has every brief until one is taken."""

    def oc(self, tool: str, args: Dict[str, Any], status: str = "completed", output: str = "") -> Dict[str, Any]:
        state: Dict[str, Any] = {"status": status, "input": args}
        state["output" if status == "completed" else "error"] = output
        return {"type": "tool_use", "part": {"tool": tool, "callID": "c", "state": state}}

    def test_opencode(self) -> None:
        p = ev.OpenCodeParser()
        p.feed({"type": "step_start", "part": {}}, 1.0)
        p.feed(self.oc("task", {"subagent_type": "coder", "prompt": KV}, "error", "Error: " + REFUSAL), 2.0)
        p.feed({"type": "step_finish", "part": {"reason": "tool-calls"}}, 2.1)
        prac = ev.practical(p.obs, now=2.2)
        self.assertEqual(prac.decision, ev.DELEGATED)                          # the decision: the first coder call
        self.assertTrue(ev.brief_pending(p.obs, 2.2))
        self.assertFalse(cl.finished(p, prac, 2.2, cl.Limits()))
        p.feed(self.oc("task", {"subagent_type": "coder", "prompt": TOML}, output='<task state="running">'), 3.0)
        p.feed({"type": "step_finish", "part": {"reason": "tool-calls"}}, 3.1)
        self.assertFalse(ev.brief_pending(p.obs, 3.2))
        self.assertTrue(cl.finished(p, ev.practical(p.obs, now=3.2), 3.2, cl.Limits()))
        self.assertEqual(ev.practical(p.obs).tool.args["prompt"], KV)          # type: ignore[union-attr]

    def test_pi_waits_for_the_result(self) -> None:
        p = ev.PiParser()
        p.feed({"type": "message_start", "message": {"role": "assistant"}}, 1.0)
        p.feed({"type": "message_end", "message": {"role": "assistant", "stopReason": "toolUse", "content": [
            {"type": "toolCall", "id": "t1", "name": "subagent", "arguments": {"agent": "coder", "task": KV}}]}}, 2.0)
        self.assertTrue(ev.brief_pending(p.obs, 3.0))                           # its result has not come yet
        self.assertFalse(ev.brief_pending(p.obs, 2.0 + ev.WRITE_RESULT_WAIT))   # no result in time: stop
        p.feed({"type": "tool_execution_end", "toolCallId": "t1", "toolName": "subagent",
                "result": {"content": [{"type": "text", "text": REFUSAL}]}}, 3.5)
        self.assertTrue(ev.brief_pending(p.obs, 4.0))
        self.assertTrue(ev.is_refusal(p.obs.tools[0].result))
        p.feed({"type": "agent_settled"}, 5.0)                                  # the turn ended: stop
        self.assertFalse(ev.brief_pending(p.obs, 5.5))

    def test_limits(self) -> None:
        o = ev.Observation("opencode", model_start=0.0)
        o.tools = [call("task", {"subagent_type": "coder"}, 1.0, REFUSAL)]
        self.assertTrue(ev.brief_pending(o, 2.0))
        self.assertFalse(ev.brief_pending(o, 2.0 + ev.MAX_SECONDS))
        o.tools += [call("read", {}, 2.0, "x") for _ in range(ev.MAX_TOOLS)] + [call("task", {"subagent_type": "coder"}, 3.0, REFUSAL)]
        self.assertFalse(ev.brief_pending(o, 4.0))
        o.tools = [call("read", {}, 1.0, "x")]
        self.assertFalse(ev.brief_pending(o, 2.0))                              # no coder call


class TokensTest(unittest.TestCase):
    def setUp(self) -> None:
        self.srv = fs.FakeServer(fs.Script.from_json({"models": ["m"], "rules": []}), api_key="k3y").start()
        self.addCleanup(self.srv.stop)
        self.url = f"http://127.0.0.1:{self.srv.port}"
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_tokenize(self) -> None:
        self.assertEqual(bf.tokenize(self.url, "k3y", "one two three"), 3)
        self.assertIsNone(bf.tokenize(self.url, "wrong", "one two"))
        self.assertIsNone(bf.tokenize("http://127.0.0.1:9", "", "x", timeout=2))
        count = bf.tokenizer(self.url, "k3y")
        self.assertEqual(bf.count_all([{"text": "a b"}, {"text": "c"}], count), [2, 1])
        self.assertEqual(bf.count_all([{"text": "a b"}], None), [None])

    def line(self, model: str, texts: List[str], tokens: Optional[List[Optional[int]]] = None) -> str:
        r = results.Result(time="t", mode="decision", model=model, client="pi", client_version="1", thinking="default",
                           thinking_level="low", variant="baseline", category="large", prompt="large-cli", run=1,
                           decision="delegated", expected="delegate", correct=True,
                           briefs=[{"text": t, "format": "toml", "valid": True, "problems": [], "refused": False}
                                   for t in texts],
                           brief_tokens=tokens if tokens is not None else [None] * len(texts))
        return r.to_json()

    def test_bench_tokens_fills_them_in(self) -> None:
        path = os.path.join(self.tmp.name, "r.jsonl")
        key = os.path.join(self.tmp.name, "api-key")
        with open(key, "w") as f:
            f.write("k3y\n")
        with open(path, "w") as f:
            f.write("\n".join([self.line("m", ["a b c", "d"]), self.line("other", ["x y"]), "{broken",
                               self.line("m", ["e f"], [7])]) + "\n")
        p = subprocess.run([sys.executable, os.path.join(sp.BENCH, "bench.py"), "tokens", path, "--url", self.url,
                            "--key-file", key, "--model", "m"], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("2 brief(s) counted in 1 line(s); 1 still without a count.", p.stdout)
        with open(path) as f:
            lines = f.read().splitlines()
        self.assertEqual(json.loads(lines[0])["brief_tokens"], [3, 1])
        self.assertEqual(json.loads(lines[1])["brief_tokens"], [None])            # another model: as it was
        self.assertEqual(lines[2], "{broken")
        self.assertEqual(json.loads(lines[3])["brief_tokens"], [7])               # counted already
        self.assertEqual(self.srv.tokenized, ["a b c", "d"])


class ReportTest(unittest.TestCase):
    def doc(self, variant: str, briefs: List[Dict[str, Any]], tokens: List[Optional[int]], mode: str = "decision",
            hidden: Optional[bool] = None, client: str = "opencode") -> Dict[str, Any]:
        r = results.Result(time="t", mode=mode, model="gemma-4-e4b", client=client, client_version="1",
                           thinking="default", thinking_level="high", variant=variant, category="large",
                           prompt="large-cli", run=1, decision="delegated", expected="delegate", correct=True,
                           seconds=600.0, briefs=briefs, brief_tokens=tokens,
                           full={"hidden_passed": hidden} if mode == "full" else {})
        return dict(json.loads(r.to_json()))

    def test_table(self) -> None:
        bad = {"text": "x", "format": "toml", "valid": False, "problems": ["p"], "refused": True}
        good = {"text": "y", "format": "toml", "valid": True, "problems": [], "refused": False}
        js = {"text": "z", "format": "json", "valid": True, "problems": [], "refused": False}
        kv = {"text": "k", "format": "kv", "valid": False, "problems": ["no"], "refused": False}
        docs = [self.doc("baseline", [bad, bad, good], [100, 110, 120]), self.doc("baseline", [good], [80]),
                self.doc("brief_json", [js], [None]), self.doc("brief_kv", [kv], [50]),
                self.doc("baseline", [good], [90], "full", True), self.doc("baseline", [], [], "full", False),
                {"mode": "decision", "model": "old", "client": "pi", "variant": "baseline"}]   # an older line
        t = report.brief_table(docs)
        assert t is not None
        rows = {(r[2], r[3]): r for r in t.rows}
        self.assertEqual(set(rows), {("baseline", "decision"), ("brief_json", "decision"), ("brief_kv", "decision"),
                                     ("baseline", "full")})
        b = rows[("baseline", "decision")]
        self.assertEqual(b[4:], ["2", "4", "toml 4", "50% (1/2)", "100% (2/2)", "1.0 (max 2)", "90", "105", "-", "-"])
        self.assertEqual(rows[("brief_kv", "decision")][7], "-")               # no check for kv
        self.assertEqual(rows[("brief_json", "decision")][10:12], ["-", "-"])   # no tokens yet
        self.assertEqual(rows[("baseline", "full")][12:], ["100% (1/1)", "10.0"])
        md = report.render([t], md=True)
        self.assertIn("## Coder briefs", md)
        self.assertIn("| gemma-4-e4b | opencode | baseline | decision | 2 | 4 | toml 4 | 50% (1/2) |", md)
        self.assertIsNone(report.brief_table([docs[-1]]))
        self.assertIn("Coder briefs", report.report(docs[:-1]))

    def test_result_line(self) -> None:
        d = self.doc("baseline", [{"text": "T" * 5000}], [1234])
        self.assertEqual(len(d["briefs"][0]["text"]), 5000)
        self.assertEqual(d["brief_tokens"], [1234])
        old = results.Result(time="t", mode="decision", model="m", client="pi", client_version="1", thinking="d",
                             thinking_level="l", variant="v", category="c", prompt="p", run=1, decision="answer",
                             expected="keep", correct=True)
        self.assertEqual((json.loads(old.to_json())["briefs"], json.loads(old.to_json())["brief_tokens"]), ([], []))


if __name__ == "__main__":
    unittest.main()
