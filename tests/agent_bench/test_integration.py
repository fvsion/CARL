"""The real OpenCode and Pi against the fake server: one decision of each kind for each client, a full run for
each client (the background coder, its result, the tests, the hidden tests), and bench.py run (resumable) and
report. Skipped when no client HOME is there (_support.client_home: AGENT_BENCH_CLIENT_HOME)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import Any, Dict

import _support as sp

import fakeserver as fs
from agentbench import clients as cl, events as ev, fixtures, results

SRC = sp.client_home()


@unittest.skipIf(SRC is None, "no client HOME with opencode and pi (set AGENT_BENCH_CLIENT_HOME)")
class RealClientsTest(unittest.TestCase):
    tmp = ""
    home = ""
    port = 0
    env: Dict[str, str] = {}

    @classmethod
    def setUpClass(cls) -> None:
        assert SRC is not None
        cls.tmp = tempfile.mkdtemp(prefix="ab-int-")
        cls.port = cl.free_port()
        cls.home = sp.clone_home(SRC, os.path.join(cls.tmp, "home"), cls.port)
        cls.env = cl.client_env(cls.home)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def serve(self, script: Dict[str, Any]) -> fs.FakeServer:
        srv = fs.FakeServer(fs.Script.from_json(script), self.port).start()
        self.addCleanup(srv.stop)
        return srv

    def spec(self, client: str, prompt: str, fixture: str = "pycli", thinking: str = "default",
             limit: float = 180) -> cl.RunSpec:
        repo = fixtures.make_fresh(fixture, os.path.join(self.tmp, "runs"))
        return cl.RunSpec(client, sp.MODEL, thinking, prompt, repo, self.home, limit)

    def test_one_decision_of_each_kind(self) -> None:
        for client in ev.CLIENTS:
            for name, (strict, practical, looks) in sp.EXPECT.items():
                with self.subTest(client=client, decision=name):
                    srv = fs.FakeServer(fs.Script.from_json(sp.scenario_script(name)), self.port).start()
                    try:
                        spec = self.spec(client, sp.PROMPTS[name])
                        out = cl.run_decision(spec, self.env)
                    finally:
                        srv.stop()
                    self.assertEqual(out.decision, strict, out.obs.error or out.stderr[-500:])
                    self.assertEqual((out.practical.decision, out.practical.looks), (practical, looks))
                    self.assertLess(out.seconds, 120)
                    if practical == ev.SELF_WRITE and client == "opencode":
                        # OpenCode's tool_use comes after the tool ran (Pi's toolCall comes before it runs)
                        self.assertIn("notes/due.py", fixtures.changed_files(spec.cwd))
                    if name == "delegated":
                        assert out.obs.first_tool is not None
                        self.assertEqual(out.obs.first_tool.name, "task" if client == "opencode" else "subagent")
                        self.assertEqual(out.thinking_level, "high")
                    if name == "error":
                        self.assertIn("context size", out.obs.error)

    def test_thinking_off_reaches_the_server(self) -> None:
        srv = self.serve(sp.scenario_script("answer"))
        for client in ev.CLIENTS:
            srv.requests.clear()
            out = cl.run_decision(self.spec(client, "How?", thinking="off"), self.env)
            self.assertEqual(out.decision, ev.ANSWER)
            main = [r for r in srv.requests if r.get("tools")]
            self.assertTrue(main, client)
            body = main[-1]
            if client == "opencode":
                self.assertEqual(body.get("reasoning_effort"), "none")
                self.assertEqual(out.thinking_level, "none")
            else:
                self.assertFalse(body["chat_template_kwargs"]["enable_thinking"])
                self.assertEqual(out.thinking_level, "off")

    def test_full_runs(self) -> None:
        with open(os.path.join(sp.DATA, "script_full.json"), encoding="utf-8") as f:
            script = json.load(f)
        script["models"] = [sp.MODEL]
        self.serve(script)
        for client in ev.CLIENTS:
            with self.subTest(client=client):
                spec = self.spec(client, "Add a readability module to textstats, with tests.", "pylib", limit=240)
                out = cl.run_full(spec, self.env)
                self.assertEqual(out.decision, ev.DELEGATED, out.obs.error)
                self.assertFalse(out.timed_out)
                self.assertEqual(out.full["coder_calls"], 1)
                self.assertGreaterEqual(out.full["coder_results"], 1)
                self.assertTrue(out.full["tests_run"])
                checks = fixtures.check_hidden(spec.cwd, "pylib", ["test_hidden_readability.py"])
                self.assertTrue(checks["hidden"].passed, checks["hidden"].output)
                self.assertIn("textstats/readability.py", fixtures.changed_files(spec.cwd))

    def bench(self, *args: str) -> "subprocess.CompletedProcess[str]":
        return subprocess.run([sys.executable, os.path.join(sp.BENCH, "bench.py"), *args], capture_output=True,
                              text=True, timeout=600)

    def test_bench_run_resumes_and_reports(self) -> None:
        self.serve({"models": [sp.MODEL], "rules": [
            {"last_role": "tool", "reply": {"text": "Started."}},
            {"user_contains": "command-line tool", "has_tool": "task",
             "reply": {"tool": "task", "arguments": {"description": "cli", "prompt": sp.BRIEF,
                                                     "subagent_type": "coder"}}},
            {"user_contains": "command-line tool", "has_tool": "subagent",
             "reply": {"tool": "subagent", "arguments": {"agent": "coder", "task": sp.BRIEF}}},
            {"reply": {"text": "In render(), at the end."}}], "default": {"text": "OK"}})
        out = os.path.join(self.tmp, "results.jsonl")
        args = ["run", "--home", self.home, "--work", os.path.join(self.tmp, "work"), "--no-server",
                "--models", sp.MODEL, "--thinking", "off", "--runs", "1", "--prompts", "large-cli,question-where",
                "--out", out]
        p = self.bench(*args)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("4 to run", p.stdout)
        docs = list(results.read_results(out))
        self.assertEqual(len(docs), 4)
        self.assertEqual(sorted((d["client"], d["prompt"], d["decision"]) for d in docs),
                         [("opencode", "large-cli", "delegated"), ("opencode", "question-where", "answer"),
                          ("pi", "large-cli", "delegated"), ("pi", "question-where", "answer")])
        for d in docs:
            self.assertTrue(d["correct"])
            self.assertTrue(d["correct_strict"])
            self.assertEqual(d["measure"], ev.MEASURE)
            self.assertEqual(d["decision"], d["decision_strict"])
            self.assertEqual(d["variant"], "baseline")
            self.assertNotEqual(d["client_version"], "unknown")
            if d["prompt"] == "large-cli":                     # the brief as the main agent wrote it, checked
                self.assertEqual([(b["text"], b["format"], b["valid"], b["refused"]) for b in d["briefs"]],
                                 [(sp.BRIEF, "toml", True, False)])
                self.assertEqual(d["brief_tokens"], [None])     # --no-server: bench.py tokens counts them later
            else:
                self.assertEqual(d["briefs"], [])
        p = self.bench(*args)
        self.assertIn("4 done already, 0 to run", p.stdout)
        p = self.bench("report", out, "--md")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn(f"| gemma-4-e4b | opencode | off | baseline | {ev.MEASURE} | 2 | 100% (1/1) | 100% (1/1) | "
                      "100% (1/1) | 100% (1/1) | 0 | 0 |", p.stdout)
        self.assertEqual(os.listdir(os.path.join(self.tmp, "work", "runs")), [])   # decision copies removed


if __name__ == "__main__":
    unittest.main()
