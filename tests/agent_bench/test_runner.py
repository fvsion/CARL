"""The prompts, the matrix and its resumable results file, the report, the fixtures, the variants, the client
commands and environment, the server setup, the harness HOME checks and the fake server. No client runs here."""
from __future__ import annotations

import dataclasses
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from typing import Any, Dict, List

import _support as sp

import fakeserver as fs
from agentbench import clients as cl
from agentbench import events as ev
from agentbench import fixtures, home, matrix, prompts, report, results, server, variant


class PromptsTest(unittest.TestCase):
    def test_the_prompts_file(self) -> None:
        everything = prompts.load_prompts()
        ps = prompts.select(everything)                      # the baseline's 17: the extra prompts only when asked
        self.assertEqual(len(ps), 17)
        for cat in prompts.CATEGORIES:                       # large: 4 + the 11.8 control (large-newpkg)
            self.assertEqual(sum(1 for p in ps if p.category == cat), 5 if cat == "large" else 4, cat)
        extra = [p.id for p in everything if p.extra]        # V5's false positives: small requests with a new file
        self.assertEqual(extra, ["small-gitignore", "small-newtest"])
        self.assertEqual(len(prompts.select(everything, with_extra=True)), 19)
        self.assertEqual([p.id for p in prompts.select(everything, ids=["small-newtest"])], ["small-newtest"])
        self.assertEqual(len(prompts.select(everything, categories=["small"])), 4)
        self.assertEqual({p.expect for p in ps if p.category in ("large", "stuck")}, {"delegate"})
        self.assertEqual({p.expect for p in ps if p.category in ("small", "question")}, {"keep"})
        for p in ps:
            self.assertTrue(os.path.isdir(os.path.join(fixtures.FIXTURES_DIR, p.fixture)), p.id)
            for h in p.hidden:
                self.assertTrue(os.path.isfile(os.path.join(fixtures.HIDDEN_DIR, p.fixture, h)), h)
        self.assertGreaterEqual(len([p for p in ps if p.full]), 2)
        stuck = [p for p in ps if p.category == "stuck"]
        self.assertTrue(all("twice" in p.text for p in stuck))           # the user says the fix failed twice
        self.assertTrue(all("```" in p.text for p in stuck))             # and quotes the error

    def test_select(self) -> None:
        ps = prompts.load_prompts()
        self.assertEqual({p.category for p in prompts.select(ps, ["small"])}, {"small"})
        self.assertEqual([p.id for p in prompts.select(ps, None, ["large-cli"])], ["large-cli"])
        self.assertTrue(all(p.full for p in prompts.select(ps, full=True)))
        with self.assertRaises(ValueError):
            prompts.select(ps, ["huge"])
        with self.assertRaises(ValueError):
            prompts.select(ps, None, ["nope"])

    def test_bad_files(self) -> None:
        one: Dict[str, Any] = {"id": "a", "category": "large", "fixture": "pylib", "prompt": "x"}
        expect = {"large": "delegate"}
        self.assertEqual(len(prompts.parse_prompts({"expect": expect, "prompts": [one]})), 1)
        bads: List[Dict[str, Any]] = [{"prompts": []}, {"expect": expect, "prompts": [one, one]}]
        for change in ({"category": "medium"}, {"fixture": "rust"}, {"prompt": " "}, {"full": {"hidden": ["../x.py"]}}):
            bads.append({"expect": expect, "prompts": [{**one, **change}]})
        for bad in bads:
            with self.assertRaises(ValueError):
                prompts.parse_prompts(bad)


def result(**kw: Any) -> results.Result:
    base: Dict[str, Any] = dict(time="t", mode="decision", model="m", client="pi", client_version="1.0.2",
                                thinking="default", thinking_level="high", variant="baseline", category="large",
                                prompt="large-cli", run=1, decision="delegated", expected="delegate", correct=True)
    base.update(kw)
    return results.Result(**base)


class MatrixTest(unittest.TestCase):
    def test_build_and_order(self) -> None:
        ps = prompts.load_prompts()[:3]
        cells = matrix.build(["a", "b"], ["opencode", "pi"], ["default", "off"], 2, "baseline", ps)
        self.assertEqual(len(cells), 2 * 2 * 2 * 2 * 3)
        self.assertEqual(list(matrix.by_model(cells)), ["a", "b"])
        self.assertEqual([c.prompt.id for c in cells[:4]], [p.id for p in ps] + [ps[0].id])
        self.assertEqual(cells[3].run, 2)
        with self.assertRaises(ValueError):
            matrix.build(["a"], ["pi"], ["off"], 0, "baseline", ps)

    def test_resumable(self) -> None:
        tmp = tempfile.mkdtemp(prefix="ab-res-")
        self.addCleanup(shutil.rmtree, tmp)
        path = os.path.join(tmp, "sub", "results.jsonl")
        ps = [p for p in prompts.load_prompts() if p.id in ("large-cli", "small-flag")]
        cells = matrix.build(["m"], ["pi"], ["default"], 1, "baseline", ps)
        store = results.ResultStore(path)
        self.assertEqual(len(matrix.pending(cells, store.done_keys())), 2)
        store.append(result(prompt="large-cli"))
        with open(path, "a") as f:
            f.write('{"broken line\n\n')                          # a run that stopped while it wrote
        store2 = results.ResultStore(path)
        todo = matrix.pending(cells, store2.done_keys())
        self.assertEqual([c.prompt.id for c in todo], ["small-flag"])
        store2.append(result(prompt="small-flag", decision="error", correct=None, category="small", expected="keep"))
        self.assertEqual(matrix.pending(cells, results.ResultStore(path).done_keys()), [])
        retry = results.ResultStore(path, retry_errors=True)
        self.assertEqual([c.prompt.id for c in matrix.pending(cells, retry.done_keys())], ["small-flag"])
        self.assertEqual(len(list(results.read_results(path))), 2)
        # another variant or thinking is another cell
        other = matrix.build(["m"], ["pi"], ["off"], 1, "v1", ps)
        self.assertEqual(len(matrix.pending(other, store2.done_keys())), 2)

    def test_old_results_are_another_measure(self) -> None:
        tmp = tempfile.mkdtemp(prefix="ab-res-")
        self.addCleanup(shutil.rmtree, tmp)
        path = os.path.join(tmp, "results.jsonl")
        old = json.loads(result(prompt="large-cli").to_json())
        del old["measure"]
        with open(path, "w") as f:
            f.write(json.dumps(old) + "\n")
        ps = [p for p in prompts.load_prompts() if p.id == "large-cli"]
        cells = matrix.build(["m"], ["pi"], ["default"], 1, "baseline", ps)
        self.assertEqual(cells[0].measure, ev.MEASURE)
        store = results.ResultStore(path)
        self.assertEqual(len(matrix.pending(cells, store.done_keys())), 1)     # the old line does not count
        legacy = [dataclasses.replace(c, measure=ev.LEGACY_MEASURE) for c in cells]
        self.assertEqual(matrix.pending(legacy, store.done_keys()), [])
        store.append(result(prompt="large-cli"))
        self.assertEqual(matrix.pending(cells, results.ResultStore(path).done_keys()), [])
        other = [dataclasses.replace(c, measure=ev.MEASURE + "-t12-s300") for c in cells]
        self.assertEqual(len(matrix.pending(other, results.ResultStore(path).done_keys())), 1)

    def test_eta(self) -> None:
        self.assertEqual(matrix.eta_seconds([], 5), 0.0)
        self.assertEqual(matrix.eta_seconds([10.0, 20.0], 4), 60.0)
        self.assertEqual(matrix.fmt_duration(42), "42 s")
        self.assertEqual(matrix.fmt_duration(125), "2 min 05 s")
        self.assertEqual(matrix.fmt_duration(3 * 3600 + 7 * 60), "3 h 07 min")


def prac(prompt: str, category: str, decision: str, strict: str, **kw: Any) -> results.Result:
    expected = "delegate" if category in ("large", "stuck") else "keep"
    return result(prompt=prompt, category=category, expected=expected, decision=decision, decision_strict=strict,
                  correct=ev.expected_ok(expected, decision), correct_strict=ev.expected_ok(expected, strict), **kw)


class ReportTest(unittest.TestCase):
    def docs(self) -> List[Dict[str, Any]]:
        rs = [prac("large-cli", "large", "delegated", "self", tool="read", decision_tool="subagent", looks=2,
                   decision_seconds=30.0, tool_seconds=10.0, thinking_tokens=100, thinking_tokens_decision=300),
              prac("large-module", "large", "self-write", "self", tool="read", decision_tool="write", looks=1,
                   decision_seconds=20.0),
              prac("stuck-test", "stuck", "delegated", "delegated", tool="subagent", decision_tool="subagent",
                   decision_seconds=5.0),
              prac("stuck-console", "stuck", "undecided", "self", tool="bash", looks=8),
              prac("small-flag", "small", "self-write", "self", tool="read", decision_tool="edit", looks=2,
                   decision_seconds=10.0),
              prac("question-where", "question", "answer", "answer"),
              prac("question-where", "question", "error", "error", run=2, error="time limit"),
              prac("small-typo", "small", "undecided", "self", tool="grep", looks=8)]
        docs = [json.loads(r.to_json()) for r in rs]
        for prompt, cat, decision, tool in (("large-cli", "large", "delegated", "task"),
                                            ("small-flag", "small", "self", "edit")):
            docs.append({"time": "t", "mode": "decision", "model": "m", "client": "opencode",
                         "client_version": "1.18.34", "thinking": "default", "variant": "baseline",
                         "category": cat, "prompt": prompt, "run": 1, "decision": decision, "tool": tool,
                         "expected": "delegate" if cat == "large" else "keep", "correct": True, "schema": 1})
        full = result(mode="full", prompt="large-module", seconds=600.0, decision_strict="delegated",
                      full={"coder_calls": 1, "coder_results": 1, "tests_run": True, "hidden_passed": True,
                            "suite_passed": False, "suite_passed_before": False})
        return docs + [json.loads(full.to_json())]

    def test_numbers(self) -> None:
        g = report.summarize(self.docs())
        pi = g[("m", "pi", "default", "baseline", ev.MEASURE)]
        self.assertEqual((pi.runs, pi.errors, pi.undecided), (8, 1, 2))
        self.assertEqual((pi.deleg_strict.ok, pi.deleg_strict.total), (1, 4))
        self.assertEqual((pi.deleg_prac.ok, pi.deleg_prac.total), (2, 4))
        self.assertEqual((pi.keep_strict.ok, pi.keep_strict.total), (3, 3))
        self.assertEqual((pi.keep_prac.ok, pi.keep_prac.total), (3, 3))    # undecided on a small request: kept
        self.assertEqual(sorted(pi.looks), [0.0, 1.0, 2.0, 2.0])               # before a decisive action only
        self.assertEqual((pi.passed_strict, pi.passed_practical), (False, False))
        self.assertEqual(pi.wrong, {("large-module", "delegate", "self-write (write)", "self (read)"): 1,
                                    ("stuck-console", "delegate", "undecided", "self (bash)"): 1})
        old = g[("m", "opencode", "default", "baseline", ev.LEGACY_MEASURE)]
        self.assertEqual((old.passed_strict, old.passed_practical, old.practical), (True, None, False))

    def test_text_and_markdown(self) -> None:
        text = report.report(self.docs())
        self.assertIn("50% (2/4)", text)
        self.assertIn("Full runs", text)
        self.assertIn("time limit", text)
        md = report.report(self.docs(), md=True)
        self.assertIn("## Decision runs", md)
        self.assertIn("| Model | Client | Thinking | Variant | Measure |", md)
        self.assertIn(f"| m | pi | default | baseline | {ev.MEASURE} | 8 | 25% (1/4) | 50% (2/4) | 100% (3/3) | "
                      "100% (3/3) | 2 | 1 | 1.5 | 15.0 | 10.0 | 300 | no | no |", md)
        self.assertIn("| m | opencode | default | baseline | first-tool | 2 | 100% (1/1) | - | 100% (1/1) | - | 0 | 0 |",
                      md)
        self.assertIn("| large-module | delegate | self-write (write) | self (read) | 1 |", md)
        self.assertIn(f"| m | pi | default | baseline | {ev.MEASURE} | large-module | 1 | yes | yes | - | yes | yes | no | "
                      "no | 10.0 |", md)
        self.assertEqual(report.versions_line(self.docs()), {"opencode": ["1.18.34"], "pi": ["1.0.2"]})


class FixturesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="ab-fix-")
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_fresh_copy_with_git(self) -> None:
        for fix, name in fixtures.PROJECT_NAMES.items():
            d = fixtures.make_fresh(fix, self.tmp)
            self.assertEqual(os.path.basename(d), name)
            if fix != "empty":                                   # the 11.8 control starts with an empty folder
                self.assertTrue(os.path.isfile(os.path.join(d, "README.md")))
            log = subprocess.run(["git", "log", "--oneline"], cwd=d, capture_output=True, text=True,
                                 env=fixtures.git_env()).stdout.splitlines()
            self.assertEqual(len(log), 1, fix)
            self.assertEqual(fixtures.changed_files(d), [])
            self.assertFalse(any("__pycache__" in r for r, _, _ in os.walk(d)))
        a, b = fixtures.make_fresh("pycli", self.tmp), fixtures.make_fresh("pycli", self.tmp)
        self.assertNotEqual(a, b)
        with self.assertRaises(ValueError):
            fixtures.make_fresh("rust", self.tmp)

    def test_small_projects(self) -> None:
        lines = 0
        for root, _, files in os.walk(fixtures.FIXTURES_DIR):
            for f in files:
                if f.endswith((".py", ".js", ".html", ".css", ".md")):
                    with open(os.path.join(root, f)) as fh:
                        lines += len(fh.read().splitlines())
        self.assertLess(lines, 600)

    def test_suites(self) -> None:
        # pylib has one failing test (the stuck prompt's), pycli passes; the hidden tests fail before any work
        lib = fixtures.make_fresh("pylib", self.tmp)
        before = fixtures.run_suite(lib)
        self.assertFalse(before.passed)
        self.assertIn("test_mean_word_length", before.output)
        self.assertTrue(fixtures.run_suite(fixtures.make_fresh("pycli", self.tmp)).passed)
        chk = fixtures.check_hidden(lib, "pylib", ["test_hidden_readability.py"])
        self.assertFalse(chk["hidden"].passed)
        self.assertIn("tests/test_hidden_readability.py", fixtures.changed_files(lib))
        self.assertEqual(fixtures.test_command(["tests/test_x.py"], "py", pytest=False),
                         ["py", "-m", "unittest", "tests.test_x"])


class ClientSetupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="ab-home-")
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_env_file(self) -> None:
        path = os.path.join(self.tmp, "opencode.env")
        with open(path, "w") as f:
            f.write("# a comment\nexport OPENCODE_ENABLE_EXA=1      # web search\n"
                    "export OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=1\nexport lower=x\nexport A=$(rm -rf /)\n")
        env = cl.read_env_file(path)
        self.assertEqual(env["OPENCODE_ENABLE_EXA"], "1")
        self.assertEqual(env["OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS"], "1")
        self.assertNotIn("lower", env)
        self.assertEqual(env.get("A"), "$(rm")                           # read as text, never run
        self.assertEqual(cl.read_env_file(os.path.join(self.tmp, "none")), {})

    def test_client_env(self) -> None:
        os.makedirs(os.path.join(self.tmp, ".config", "carl"))
        with open(os.path.join(self.tmp, ".config", "carl", "opencode.env"), "w") as f:
            f.write("export OPENCODE_ENABLE_EXA=1\n")
        env = cl.client_env(self.tmp, {"HOME": "/Users/me", "PATH": "/x", "SECRET": "s", "LANG": "C"})
        self.assertEqual(env["HOME"], self.tmp)
        self.assertTrue(env["PATH"].startswith(os.path.join(self.tmp, ".local", "bin") + ":"))
        self.assertNotIn("SECRET", env)
        self.assertEqual(env["OPENCODE_ENABLE_EXA"], "1")
        self.assertEqual(env["OPENCODE_DISABLE_AUTOUPDATE"], "1")

    def test_commands(self) -> None:
        os.makedirs(os.path.join(self.tmp, ".pi", "agent"))
        with open(os.path.join(self.tmp, ".pi", "agent", "settings.json"), "w") as f:
            json.dump({"defaultThinkingLevel": "low"}, f)

        def argv(client: str, thinking: str) -> List[str]:
            return list(cl.decision_argv(cl.RunSpec(client, "gemma-4-e4b", thinking, "Do it", "/tmp", self.tmp, 60)))
        self.assertEqual(argv("opencode", "default"),
                         ["opencode", "run", "--format", "json", "--thinking", "--print-logs", "--log-level", "INFO",
                          "-m", "llamacpp/gemma-4-e4b", "Do it"])
        self.assertEqual(argv("opencode", "off")[-3:], ["--variant", "none", "Do it"])
        self.assertEqual(argv("opencode", "high")[-3:], ["--variant", "high", "Do it"])
        self.assertEqual(argv("pi", "default"),
                         ["pi", "-p", "--mode", "json", "--model", "llamacpp/gemma-4-e4b:low", "--", "Do it"])
        self.assertEqual(argv("pi", "off")[5], "llamacpp/gemma-4-e4b:off")
        with self.assertRaises(ValueError):
            argv("pi", "very high")
        with self.assertRaises(ValueError):
            argv("cursor", "off")

    def test_stale_locks(self) -> None:
        locks = os.path.join(self.tmp, ".local", "state", "opencode", "locks")
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        for name, pid, host in (("dead.lock", dead.pid, "h"), ("live.lock", os.getpid(), "h"),
                                ("other.lock", dead.pid, "elsewhere")):
            os.makedirs(os.path.join(locks, name))
            with open(os.path.join(locks, name, "meta.json"), "w") as f:
                json.dump({"pid": pid, "hostname": host}, f)
        os.makedirs(os.path.join(locks, "nometa.lock"))
        self.assertEqual(cl.clear_stale_locks(self.tmp, hostname="h"), ["dead.lock"])
        self.assertEqual(sorted(os.listdir(locks)), ["live.lock", "nometa.lock", "other.lock"])

    def test_harness_home_checks(self) -> None:
        real = os.path.expanduser("~")
        for bad in (real, os.path.dirname(real), "/"):
            with self.assertRaises(home.HomeError):
                home.check_home_path(bad)
        self.assertEqual(home.check_home_path(self.tmp), os.path.realpath(self.tmp))
        with self.assertRaises(home.HomeError):
            home.require_harness_home(self.tmp)
        home.write_marker(self.tmp, {"a": 1})
        self.assertEqual(home.require_harness_home(self.tmp), os.path.realpath(self.tmp))
        self.assertEqual(home.read_marker(self.tmp)["a"], 1)
        os.makedirs(os.path.join(self.tmp, "x"))
        with open(os.path.join(self.tmp, "x", "f"), "w") as f:
            f.write("mine")
        with self.assertRaises(home.HomeError):                         # not empty, not a harness HOME
            home.prepare(home.PackageSource("/r", "/c", "/c", "/o"), os.path.join(self.tmp, "x"))

    def test_variants(self) -> None:
        self.assertIn("baseline", variant.available())
        v = variant.load("baseline")
        self.assertEqual(v.apply(self.tmp), [])
        for bad in ("../x", "Baseline", "nope"):
            with self.assertRaises(ValueError):
                variant.load(bad)
        with open(os.path.join(self.tmp, "broken.py"), "w") as f:
            f.write("DESCRIPTION = 1\n")
        with self.assertRaises(ValueError):
            variant.load("broken", self.tmp)


class ServerSetupTest(unittest.TestCase):
    def test_never_8080(self) -> None:
        with self.assertRaises(server.ServerError):
            server.check_port(8080)
        with self.assertRaises(server.ServerError):
            server.check_port(80)
        server.check_port(8097)

    def test_launcher_env_and_argv(self) -> None:
        s = server.ServerSetup(repo="/repo", model="gemma-4-e4b", work="/w", port=8097, models_dir="/m", slots=2)
        env = s.env("/w/logs/x.log", {"HOME": "/Users/me", "PATH": "/usr/bin"})
        self.assertEqual((env["MONITOR"], env["PORT"], env["CARL_CONF_DIR"], env["CARL_CLIENT_DIR"]),
                         ("0", "8097", "/w/conf", "/w/client"))
        self.assertEqual((env["API_KEY_FILE"], env["LOG_FILE"], env["MODELS_DIR"], env["HOME"], env["PATH"]),
                         ("/w/api-key", "/w/logs/x.log", "/m", "/w/home", "/usr/bin"))
        self.assertTrue(server.ServerSetup("/r", "m", "/w").models_dir.endswith("/models/gguf"))
        self.assertEqual(s.argv(), ["./carl.sh", "--model", "gemma-4-e4b", "--local", "--slots", "2"])

    def test_busy_port(self) -> None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            port = sock.getsockname()[1]
            tmp = tempfile.mkdtemp(prefix="ab-srv-")
            self.addCleanup(shutil.rmtree, tmp)
            srv = server.CarlServer(server.ServerSetup("/nonexistent", "m", tmp, port=port))
            with self.assertRaises(server.ServerError):
                srv.start()
            self.assertIsNone(srv.proc)

    def test_key_file(self) -> None:
        tmp = tempfile.mkdtemp(prefix="ab-key-")
        self.addCleanup(shutil.rmtree, tmp)
        key = server.ensure_key(os.path.join(tmp, "k", "api-key"))
        self.assertEqual(len(key), 40)
        self.assertEqual(os.stat(os.path.join(tmp, "k", "api-key")).st_mode & 0o777, 0o600)
        self.assertEqual(server.ensure_key(os.path.join(tmp, "k", "api-key")), key)


class FakeServerTest(unittest.TestCase):
    def setUp(self) -> None:
        script = fs.Script.from_json({"models": ["fake-a"], "rules": [
            {"system_contains": ["coder", "pi"], "reply": {"text": "both"}},
            {"user_contains": "fail", "reply": {"error": 400, "text": "too long"}},
            {"has_tool": "task", "tool_results": 0, "reply": {"reasoning": "Large.", "tool": "task",
                                                              "arguments": {"subagent_type": "coder"}}},
            {"last_role": "tool", "reply": {"text": "Done."}}]})
        self.srv = fs.FakeServer(script).start()
        self.addCleanup(self.srv.stop)
        self.url = f"http://127.0.0.1:{self.srv.port}"

    def post(self, body: Dict[str, Any]) -> Any:
        req = urllib.request.Request(self.url + "/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.read().decode()
        except urllib.error.HTTPError as e:
            e.close()
            return e.code

    def test_get(self) -> None:
        for path, key in (("/health", "status"), ("/props", "total_slots"), ("/v1/models", "data")):
            with urllib.request.urlopen(self.url + path, timeout=10) as r:
                self.assertIn(key, json.loads(r.read()))

    def test_stream_tool_call(self) -> None:
        tools = [{"type": "function", "function": {"name": "task"}}]
        text = self.post({"stream": True, "stream_options": {"include_usage": True}, "tools": tools,
                          "messages": [{"role": "system", "content": "x"}, {"role": "user", "content": "Go"}]})
        chunks = [json.loads(line[6:]) for line in text.split("\n") if line.startswith("data: {")]
        self.assertTrue(text.rstrip().endswith("data: [DONE]"))
        deltas = [c["choices"][0]["delta"] for c in chunks if c["choices"]]
        self.assertEqual("".join(d.get("reasoning_content", "") for d in deltas), "Large.")
        calls = [d["tool_calls"][0] for d in deltas if "tool_calls" in d]
        self.assertEqual(calls[0]["function"]["name"], "task")
        self.assertEqual(json.loads("".join(c["function"]["arguments"] for c in calls)), {"subagent_type": "coder"})
        self.assertEqual(chunks[-2]["choices"][0]["finish_reason"], "tool_calls")
        self.assertEqual(chunks[-1]["usage"]["completion_tokens_details"]["reasoning_tokens"], 1)
        self.assertEqual(len(self.srv.requests), 1)

    def test_rules(self) -> None:
        body = {"messages": [{"role": "system", "content": [{"type": "text", "text": "the coder in pi"}]},
                             {"role": "user", "content": "x"}]}
        self.assertEqual(json.loads(self.post(body))["choices"][0]["message"]["content"], "both")
        self.assertEqual(self.post({"messages": [{"role": "user", "content": "please fail"}]}), 400)
        done = self.post({"tools": [{"function": {"name": "task"}}], "messages": [
            {"role": "user", "content": "x"}, {"role": "tool", "content": "ok"}]})
        self.assertEqual(json.loads(done)["choices"][0]["message"]["content"], "Done.")
        self.assertEqual(json.loads(self.post({"messages": []}))["choices"][0]["message"]["content"], "OK")


class FullReportTest(unittest.TestCase):
    def test_coder_column(self) -> None:
        base = {"mode": "full", "model": "m", "client": "opencode", "thinking": "default", "variant": "baseline",
                "measure": ev.MEASURE, "prompt": "large-cli", "run": 1}
        docs = [{**base, "full": {"coder_calls": 1, "coder_results": 1, "coder_stalled": True}},
                {**base, "run": 2, "full": {"coder_calls": 1, "coder_results": 1, "coder_stalled": False}},
                {**base, "run": 3, "full": {"coder_calls": 0, "coder_results": 0, "coder_stalled": False}},
                {**base, "run": 4, "full": {"coder_calls": 1, "coder_results": 1}}]
        ft = [t for t in report.tables(docs) if t.title == "Full runs"][0]
        col = ft.head.index("Coder")
        self.assertEqual([r[col] for r in ft.rows], ["STALLED", "worked", "-", "-"])


class CloneHomeTest(unittest.TestCase):
    """A copy of a client HOME gets a resolved path (macOS: /private/var, not /var): OpenCode resolves the link but
    not the plugin paths, so in a copy under /var no CARL plugin loaded (2026-10-06)."""

    def test_the_copy_path_is_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(src, ".config", "opencode"))
            with open(os.path.join(src, ".config", "opencode", "opencode.json"), "w") as f:
                json.dump({"plugin": [["file:" + os.path.join(src, ".config/opencode/plugins/carl-cache"), {}]]}, f)
            with open(os.path.join(src, ".config", "opencode", "carl.json"), "w") as f:
                json.dump({"base_url": "http://127.0.0.1:8097/v1"}, f)
            dst = sp.clone_home(src, os.path.join(tmp, "home"), 9999)
            self.assertEqual(dst, os.path.join(os.path.realpath(tmp), "home"))
            with open(os.path.join(dst, ".config", "opencode", "opencode.json")) as f:
                self.assertIn(dst + "/.config/opencode/plugins/carl-cache", f.read())


class BorrowKeepTest(unittest.TestCase):
    """practical-v2 groups with only large and stuck runs take their keep shares from practical-v1."""

    def test_borrow(self) -> None:
        base = {"mode": "decision", "model": "m", "client": "pi", "thinking": "off", "variant": "baseline", "run": 1}
        docs = [{**base, "measure": ev.PREVIOUS_MEASURE, "expected": "keep", "category": "small", "prompt": "s",
                 "decision": ev.SELF_WRITE, "decision_strict": ev.SELF},
                {**base, "measure": ev.MEASURE, "expected": "delegate", "category": "large", "prompt": "l",
                 "decision": ev.DELEGATED, "decision_strict": ev.DELEGATED}]
        g = report.summarize(docs)[("m", "pi", "off", "baseline", ev.MEASURE)]
        self.assertEqual((g.keep_prac.ok, g.keep_prac.total, g.keep_from), (1, 1, ev.PREVIOUS_MEASURE))
        main = report.tables(docs)[0]
        self.assertTrue(any("[practical-v1]" in cell for row in main.rows for cell in row))


class _ThinkingChild:
    """A client that starts a model step and then only thinks: no tool call, no text, never ends by itself."""

    def __init__(self, *_: Any, closes: bool = False) -> None:
        self.t, self.sent, self.closes = 0.0, False, closes

    def next_line(self, _timeout: float) -> Any:
        self.t += 10.0
        if not self.sent:
            self.sent = True
            return ("out", json.dumps({"type": "message_start", "message": {"role": "assistant"}}), self.t)
        return None

    def elapsed(self) -> float:
        return self.t

    def streams_open(self) -> bool:
        return not (self.closes and self.t > 50)

    def stop(self) -> int:
        return -2

    def err_text(self, _n: int = 0) -> str:
        return ""


class ThinkingToTheLimitTest(unittest.TestCase):
    """A model that thinks past the time limit with no tool call and no text is undecided, not an error (the 27B Q3,
    2026-10-05: 'the client ended (code -2)' was the harness stopping it at 240 s)."""

    def spec(self, d: str) -> cl.RunSpec:
        return cl.RunSpec("pi", "m", "off", "Write a module", d, d, 600.0)

    def test_limit_while_thinking_is_undecided(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            out = cl.run_decision(self.spec(d), {}, cl.Limits(max_seconds=30.0), child_factory=_ThinkingChild)
        self.assertEqual(out.obs.error, "")
        self.assertTrue(out.obs.limit)
        self.assertEqual(out.practical.decision, ev.UNDECIDED)
        self.assertEqual(ev.classify(out.obs), ev.UNDECIDED)
        self.assertFalse(ev.expected_ok("delegate", ev.classify(out.obs)))

    def test_client_that_ends_by_itself_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            out = cl.run_decision(self.spec(d), {}, cl.Limits(max_seconds=1000.0),
                                  child_factory=lambda *a: _ThinkingChild(closes=True))
        self.assertIn("ended", out.obs.error)
        self.assertEqual(ev.classify(out.obs), ev.ERROR)


if __name__ == "__main__":
    unittest.main()
