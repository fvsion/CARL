"""agentbench.events: the real event streams of OpenCode 1.18.34 and Pi 1.0.2 (captured with capture_events.py
against the fake server) and the decision they give."""
from __future__ import annotations

import unittest
from typing import Mapping, Union

import _support as sp

from agentbench import events as ev

Parser = Union[ev.OpenCodeParser, ev.PiParser]


def feed(client: str, name: str) -> Parser:
    p: Parser = ev.OpenCodeParser() if client == "opencode" else ev.PiParser()
    for i, doc in enumerate(sp.read_events(f"{client}-{name}")):
        p.feed(doc, float(i))
    if isinstance(p, ev.OpenCodeParser):
        for line in sp.read_log(f"{client}-{name}"):
            p.feed_log(line, 0.5)
    p.end()
    return p


class CapturedOpenCodeTest(unittest.TestCase):
    def test_delegated(self) -> None:
        o = feed("opencode", "delegated").obs
        self.assertEqual(o.decision(), ev.DELEGATED)
        assert o.first_tool is not None
        self.assertEqual(o.first_tool.name, "task")
        self.assertEqual(o.first_tool.args["subagent_type"], "coder")
        self.assertTrue(o.first_tool.args["background"])
        self.assertEqual(o.step_tools, ["task"])
        self.assertTrue(o.step_done)
        self.assertGreater(o.thinking_chars, 0)          # the reasoning event (with --thinking)
        self.assertGreater(o.thinking_tokens, 0)         # step_finish tokens.reasoning
        self.assertEqual(o.first_event["type"], "tool_use")
        self.assertEqual(o.model_start, 0.0)
        self.assertEqual(o.tool_seconds(), 2.0)

    def test_delegated_log_line(self) -> None:
        log = sp.read_log("opencode-delegated")
        self.assertTrue(any("permission=task pattern=coder" in x for x in log))
        p = ev.OpenCodeParser()
        for line in log:
            p.feed_log(line, 1.0)
        self.assertIsNone(p.obs.first_tool)
        p.promote_log_tool()
        self.assertEqual(p.obs.decision(), ev.DELEGATED)
        assert p.obs.first_tool is not None
        self.assertEqual(p.obs.first_tool.source, "log")

    def test_self(self) -> None:
        o = feed("opencode", "self").obs
        self.assertEqual(o.decision(), ev.SELF)
        assert o.first_tool is not None
        self.assertEqual(o.first_tool.name, "read")
        self.assertIn("filePath", o.first_tool.args)

    def test_answer(self) -> None:
        o = feed("opencode", "answer").obs
        self.assertEqual(o.decision(), ev.ANSWER)
        self.assertIn("JSON file", o.text)
        self.assertTrue(o.ended)                          # step_finish reason stop: the turn ended
        self.assertIsNone(o.tool_seconds())

    def test_error(self) -> None:
        p = feed("opencode", "error")
        self.assertEqual(p.obs.decision(), ev.ERROR)
        self.assertIn("exceeds the available context size", p.obs.error)
        assert isinstance(p, ev.OpenCodeParser)
        self.assertTrue(p.log_errors())


class CapturedPiTest(unittest.TestCase):
    def test_delegated(self) -> None:
        o = feed("pi", "delegated").obs
        self.assertEqual(o.decision(), ev.DELEGATED)
        assert o.first_tool is not None
        self.assertEqual(o.first_tool.name, "subagent")
        self.assertEqual(o.first_tool.args["agent"], "coder")
        self.assertEqual(o.thinking_level, "high")       # Pi's level "low" maps to CARL's high (thinkingLevelMap)
        self.assertGreater(o.thinking_tokens, 0)         # usage.reasoning
        self.assertGreater(o.thinking_chars, 0)
        self.assertEqual(o.first_event["type"], "message_end")

    def test_self(self) -> None:
        o = feed("pi", "self").obs
        self.assertEqual(o.decision(), ev.SELF)
        assert o.first_tool is not None
        self.assertEqual((o.first_tool.name, o.first_tool.args), ("read", {"path": "README.md"}))

    def test_answer(self) -> None:
        o = feed("pi", "answer").obs
        self.assertEqual(o.decision(), ev.ANSWER)
        self.assertEqual(o.settled, 1)
        self.assertEqual(o.error, "")

    def test_error(self) -> None:
        o = feed("pi", "error").obs
        self.assertEqual(o.decision(), ev.ERROR)
        self.assertIn("400", o.error)

    def test_a_retry_that_works_is_no_error(self) -> None:
        p = ev.PiParser()
        p.feed({"type": "message_end", "message": {"role": "assistant", "content": [], "stopReason": "error",
                                                   "errorMessage": "Connection error."}}, 1.0)
        p.feed({"type": "auto_retry_start", "attempt": 1}, 1.5)
        p.feed({"type": "message_end", "message": {"role": "assistant", "stopReason": "toolUse", "content": [
            {"type": "toolCall", "id": "c1", "name": "edit", "arguments": {"path": "a.py"}}]}}, 3.0)
        p.feed({"type": "auto_retry_end", "success": True}, 3.1)
        p.end()
        self.assertEqual(p.obs.decision(), ev.SELF)
        self.assertEqual(p.obs.error, "")

    def test_failed_retries_are_an_error(self) -> None:
        p = ev.PiParser()
        p.feed({"type": "message_end", "message": {"role": "assistant", "content": [], "stopReason": "error",
                                                   "errorMessage": "Connection error."}}, 1.0)
        p.feed({"type": "auto_retry_end", "success": False, "finalError": "Connection error."}, 9.0)
        p.feed({"type": "agent_settled"}, 9.1)
        p.end()
        self.assertEqual((p.obs.decision(), p.obs.error), (ev.ERROR, "Connection error."))


class DelegationTest(unittest.TestCase):
    def test_opencode(self) -> None:
        self.assertTrue(ev.is_delegation("opencode", "task", {"subagent_type": "coder"}))
        self.assertTrue(ev.is_delegation("opencode", "task", {"subagent_type": "carl-coder"}))
        self.assertTrue(ev.is_delegation("opencode", "task", {"subagent_type": " Coder "}))
        self.assertFalse(ev.is_delegation("opencode", "task", {"subagent_type": "general"}))
        self.assertFalse(ev.is_delegation("opencode", "task", {"subagent_type": "explore"}))
        self.assertFalse(ev.is_delegation("opencode", "write", {"subagent_type": "coder"}))

    def test_pi(self) -> None:
        self.assertTrue(ev.is_delegation("pi", "subagent", {"agent": "coder", "task": "x"}))
        self.assertTrue(ev.is_delegation("pi", "subagent", {"tasks": [{"agent": "scout"}, {"agent": "coder"}]}))
        self.assertTrue(ev.is_delegation("pi", "subagent", {"chain": [{"agent": "coder", "task": "x"}]}))
        self.assertFalse(ev.is_delegation("pi", "subagent", {"agent": "browser"}))
        self.assertFalse(ev.is_delegation("pi", "bash", {"agent": "coder"}))
        self.assertFalse(ev.is_delegation("pi", "subagent", {"tasks": "coder"}))

    def test_unknown_client(self) -> None:
        with self.assertRaises(ValueError):
            ev.is_delegation("cursor", "task", {})

    def test_first_tool_decides(self) -> None:
        o = ev.Observation("opencode")
        o.first_tool = ev.ToolCall("todowrite", {}, 1.0)
        o.text = "I will delegate"
        self.assertEqual(ev.classify(o), ev.SELF)
        o = ev.Observation("pi")
        self.assertEqual(ev.classify(o), ev.ERROR)        # no tool call and no text
        o.text = "  "
        self.assertEqual(ev.classify(o), ev.ERROR)

    def test_expected(self) -> None:
        self.assertTrue(ev.expected_ok("delegate", ev.DELEGATED))
        self.assertFalse(ev.expected_ok("delegate", ev.SELF))
        self.assertFalse(ev.expected_ok("delegate", ev.ANSWER))
        self.assertTrue(ev.expected_ok("keep", ev.SELF))
        self.assertTrue(ev.expected_ok("keep", ev.ANSWER))
        self.assertFalse(ev.expected_ok("keep", ev.DELEGATED))
        self.assertIsNone(ev.expected_ok("keep", ev.ERROR))
        self.assertFalse(ev.expected_ok("delegate", ev.SELF_WRITE))
        self.assertFalse(ev.expected_ok("delegate", ev.UNDECIDED))       # no hand-off in the limits: wrong
        self.assertTrue(ev.expected_ok("keep", ev.SELF_WRITE))
        self.assertTrue(ev.expected_ok("keep", ev.UNDECIDED))            # no hand-off: kept
        with self.assertRaises(ValueError):
            ev.expected_ok("maybe", ev.SELF)

    def test_test_commands(self) -> None:
        for cmd in ("python3 -m pytest -q", "pytest tests/", "python -m unittest discover", "npm test",
                    "npm run test", "node --test", "cd x && make test"):
            self.assertTrue(ev.is_test_command("bash", {"command": cmd}), cmd)
        self.assertFalse(ev.is_test_command("bash", {"command": "ls tests"}))
        self.assertFalse(ev.is_test_command("read", {"command": "pytest"}))


class OpenCodeStreamTest(unittest.TestCase):
    def test_parallel_tools_of_one_step(self) -> None:
        p = ev.OpenCodeParser()
        p.feed({"type": "step_start", "part": {}}, 1.0)
        p.feed({"type": "tool_use", "part": {"tool": "read", "state": {"input": {"filePath": "a"}}}}, 2.0)
        p.feed({"type": "tool_use", "part": {"tool": "task", "state": {"input": {"subagent_type": "coder"}}}}, 2.1)
        p.feed({"type": "step_finish", "part": {"reason": "tool-calls", "tokens": {"reasoning": 7}}}, 2.2)
        p.feed({"type": "tool_use", "part": {"tool": "bash", "state": {"input": {}}}}, 5.0)
        self.assertEqual(p.obs.decision(), ev.SELF)
        self.assertEqual(p.obs.step_tools, ["read", "task"])
        self.assertEqual(p.obs.thinking_tokens, 7)
        self.assertEqual([t.name for t in p.obs.tools], ["read", "task", "bash"])

    def test_parse_line(self) -> None:
        self.assertEqual(ev.parse_line('{"type":"x"}\n'), {"type": "x"})
        for bad in ("", "not json", "[1]", "{broken", "timestamp=... level=INFO"):
            self.assertIsNone(ev.parse_line(bad))


class TrimTest(unittest.TestCase):
    def test_trim(self) -> None:
        t = ev.trim({"a": "x" * 500, "b": list(range(30)), "c": {"d": {"e": 1}}}, max_str=10, max_items=5)
        self.assertTrue(t["a"].startswith("x" * 10) and "490 more" in t["a"])
        self.assertEqual(len(t["b"]), 6)
        self.assertEqual(t["c"], {"d": {"e": 1}})
        self.assertEqual(ev.trim({"a": {"b": {"c": 1}}}, depth=2), {"a": {"b": "..."}})


class FullFactsTest(unittest.TestCase):
    def test_pi_tests_after_the_result(self) -> None:
        tools = [ev.ToolCall("subagent", {"agent": "coder"}, 1.0), ev.ToolCall("bash", {"command": "pytest -q"}, 0.5),
                 ev.ToolCall("bash", {"command": "python3 -m pytest"}, 30.0)]
        f = ev.full_facts("pi", tools, 1, 20.0)
        self.assertTrue(f.delegated and f.tests_run)
        self.assertEqual((f.coder_calls, f.coder_results), (1, 1))
        self.assertFalse(ev.full_facts("pi", tools[:2], 1, 20.0).tests_run)   # tests only before the result

    def test_opencode_messages(self) -> None:
        msgs = [
            {"info": {"role": "user"}, "parts": [{"type": "text", "text": "Add a module"}]},
            {"info": {"role": "assistant"}, "parts": [
                {"type": "tool", "tool": "task", "state": {"status": "completed", "input": {"subagent_type": "coder"},
                                                           "metadata": {"background": True}}}]},
            {"info": {"role": "user"}, "parts": [{"type": "text", "text": '<task id="ses_1" state="completed">\n'
                                                  "<summary>Background task completed: x</summary>"}]},
            {"info": {"role": "assistant"}, "parts": [
                {"type": "tool", "tool": "bash", "state": {"input": {"command": "python3 -m pytest -q"}}}]},
        ]
        tools, results, first = ev.opencode_messages_facts(msgs)
        self.assertEqual([t.name for t in tools], ["task", "bash"])
        self.assertEqual((results, first), (1, 2))
        f = ev.full_facts("opencode", tools, results, float(first or 0))
        self.assertTrue(f.delegated and f.tests_run)

    def test_opencode_foreground_task(self) -> None:
        msgs = [{"info": {"role": "assistant"}, "parts": [
            {"type": "tool", "tool": "task", "state": {"status": "completed", "input": {"subagent_type": "coder"},
                                                       "metadata": {"background": False}}}]}]
        self.assertEqual(ev.opencode_messages_facts(msgs)[1:], (1, 0))


CAPTURED_MAX_TOOLS = 8                                     # the limit when events/ was captured (practical-v1)


class CapturedPracticalTest(unittest.TestCase):
    """Both measures on every captured stream: the strict decision and the practical one with its looks."""

    def test_every_scenario(self) -> None:
        for client in ev.CLIENTS:
            for name, (strict, prac, looks) in sp.EXPECT.items():
                with self.subTest(client=client, scenario=name):
                    o = feed(client, name).obs
                    self.assertEqual(o.decision(), strict)
                    # the captured streams were made at the first limits (8 tool calls): judge them at those
                    p = ev.practical(o, None, max_tools=CAPTURED_MAX_TOOLS)
                    self.assertEqual((p.decision, p.looks), (prac, min(looks, CAPTURED_MAX_TOOLS)))
                    if prac in (ev.DELEGATED, ev.SELF_WRITE):
                        assert p.tool is not None
                        self.assertEqual(p.tool.source, "event")
                        self.assertIsNotNone(p.seconds)
                    else:
                        self.assertIsNone(p.tool)

    def test_tools_of_the_decision(self) -> None:
        self.assertEqual(ev.practical(feed("opencode", "look-write").obs).tool.name, "write")  # type: ignore[union-attr]
        self.assertEqual(ev.practical(feed("pi", "bash-write").obs).tool.name, "bash")        # type: ignore[union-attr]
        p = ev.practical(feed("opencode", "undecided").obs, max_tools=8)   # captured at the first limits
        self.assertEqual(p.reason, "8 tool calls")

    def test_pending_while_it_runs(self) -> None:
        events = sp.read_events("pi-look-delegate")
        p = ev.PiParser()
        cut = next(i for i, d in enumerate(events) if d.get("type") == "tool_execution_end")
        for i, doc in enumerate(events[:cut + 1]):
            p.feed(doc, float(i))
        self.assertEqual(ev.practical(p.obs, None, now=float(cut)).decision, ev.PENDING)   # one look so far
        self.assertEqual(ev.practical(p.obs, None).decision, ev.UNDECIDED)                 # stopped here: no decision
        self.assertEqual(ev.classify(p.obs), ev.SELF)

    def test_opencode_log_start_of_a_write(self) -> None:
        p = ev.OpenCodeParser()
        p.feed({"type": "step_start", "part": {}}, 1.0)
        p.feed({"type": "tool_use", "part": {"tool": "read", "state": {"input": {"filePath": "a"}}}}, 2.0)
        for line in sp.read_log("opencode-look-write"):
            p.feed_log(line, 3.0)                          # the write started; its tool_use has not come
        self.assertEqual(ev.practical(p.obs, None, now=5.0).decision, ev.PENDING)
        d = ev.practical(p.obs, None, now=14.0)
        self.assertEqual((d.decision, d.looks), (ev.SELF_WRITE, 1))
        assert d.tool is not None
        self.assertEqual(d.tool.source, "log")


class StopTest(unittest.TestCase):
    def test_opencode_waits_for_the_step_end(self) -> None:
        from agentbench import clients as cl
        p = ev.OpenCodeParser()
        p.feed({"type": "step_start", "part": {}}, 1.0)
        p.feed({"type": "tool_use", "part": {"tool": "write", "state": {"input": {}}}}, 2.0)
        lim = cl.Limits()
        prac = ev.practical(p.obs, None, now=2.5)
        self.assertEqual(prac.decision, ev.SELF_WRITE)
        self.assertFalse(cl.finished(p, prac, 2.5, lim))                # its step has not ended
        self.assertTrue(cl.finished(p, prac, 5.1, lim))                 # step_grace
        p.feed({"type": "step_finish", "part": {"reason": "tool-calls", "tokens": {"reasoning": 4}}}, 2.6)
        self.assertTrue(cl.finished(p, prac, 2.6, lim))
        self.assertFalse(cl.finished(p, ev.Practical(ev.PENDING), 2.6, lim))
        pi = ev.PiParser()
        self.assertTrue(cl.finished(pi, ev.Practical(ev.DELEGATED, ev.ToolCall("subagent", {}, 1.0)), 1.0, lim))


def obs_with(client: str, *calls: "tuple[str, Mapping[str, object], float]", ended: bool = False) -> ev.Observation:
    o = ev.Observation(client, model_start=10.0, turn_ended=ended)
    o.tools = [ev.ToolCall(n, dict(a), at) for n, a, at in calls]
    o.first_tool = o.tools[0] if o.tools else None
    return o


class PracticalRulesTest(unittest.TestCase):
    def test_limits(self) -> None:
        n, secs = ev.MAX_TOOLS, ev.MAX_SECONDS
        looks = [("grep", {"pattern": "x"}, 10.0 + i) for i in range(n)]
        p = ev.practical(obs_with("opencode", *looks, ("write", {}, 30.0)))
        self.assertEqual((p.decision, p.looks, p.reason), (ev.UNDECIDED, n, f"{n} tool calls"))
        p = ev.practical(obs_with("opencode", *looks[:n - 1], ("write", {}, 30.0)))
        self.assertEqual((p.decision, p.looks, p.seconds), (ev.SELF_WRITE, n - 1, 20.0))
        p = ev.practical(obs_with("pi", ("read", {}, 20.0), ("write", {}, 10.0 + secs + 1)))
        self.assertEqual((p.decision, p.looks), (ev.UNDECIDED, 1))
        o = obs_with("pi", ("read", {}, 20.0))
        self.assertEqual(ev.practical(o, now=200.0).decision, ev.PENDING)
        self.assertEqual(ev.practical(o, now=10.0 + secs + 1).decision, ev.UNDECIDED)
        self.assertEqual(ev.practical(o, max_tools=1).decision, ev.UNDECIDED)
        self.assertEqual(ev.practical(o, now=1000.0, max_seconds=None).decision, ev.PENDING)

    def test_turn_end_error_and_order(self) -> None:
        self.assertEqual(ev.practical(obs_with("pi", ("read", {}, 11.0), ended=True)).decision, ev.ANSWER)
        o = obs_with("pi", ("read", {}, 11.0), ended=True)
        o.error = "Connection error."
        self.assertEqual(ev.practical(o).decision, ev.ERROR)
        p = ev.practical(obs_with("opencode", ("todowrite", {}, 11.0), ("webfetch", {}, 12.0),
                                  ("task", {"subagent_type": "explore"}, 13.0),
                                  ("task", {"subagent_type": "coder"}, 14.0), ("write", {}, 15.0)))
        self.assertEqual((p.decision, p.looks), (ev.DELEGATED, 3))      # another subagent is a look

    def test_write_tools(self) -> None:
        for tool in ("write", "edit", "patch", "multiedit", "apply_patch"):
            self.assertEqual(ev.tool_class("opencode", tool, {}), ev.WRITE, tool)
        for tool in ("write", "edit"):
            self.assertEqual(ev.tool_class("pi", tool, {}), ev.WRITE, tool)
        for tool in ("read", "glob", "grep", "list", "ls", "find", "tool_search", "webfetch", "todowrite", "lsp"):
            self.assertEqual(ev.tool_class("opencode", tool, {}), ev.LOOK, tool)
            self.assertEqual(ev.tool_class("pi", tool, {}), ev.LOOK, tool)
        self.assertEqual(ev.tool_class("pi", "subagent", {"agent": "coder"}), ev.DELEGATED)


class GateTest(unittest.TestCase):
    """V5: a write the gate stopped (GATE_MARK in its result) is not the decision; the run goes on."""

    def test_opencode_blocked_write_then_coder(self) -> None:
        p = ev.OpenCodeParser()
        p.feed({"type": "step_start", "part": {}}, 1.0)
        err = f"Error: {ev.GATE_MARK}: x.py is a new file"
        p.feed({"type": "tool_use", "part": {"tool": "write", "callID": "c1",
                "state": {"status": "error", "input": {"filePath": "x.py"}, "error": err}}}, 2.0)
        self.assertTrue(p.obs.tools[0].blocked)
        self.assertEqual(ev.practical(p.obs, now=3.0).decision, ev.PENDING)
        p.feed({"type": "tool_use", "part": {"tool": "task", "callID": "c2",
                "state": {"status": "completed", "input": {"subagent_type": "coder"}}}}, 4.0)
        prac = ev.practical(p.obs, now=4.0)
        self.assertEqual((prac.decision, prac.looks), (ev.DELEGATED, 1))

    def test_opencode_write_that_failed_otherwise_still_decides(self) -> None:
        p = ev.OpenCodeParser()
        p.feed({"type": "tool_use", "part": {"tool": "edit", "callID": "c1",
                "state": {"status": "error", "input": {}, "error": "oldString not found"}}}, 2.0)
        self.assertFalse(p.obs.tools[0].blocked)
        self.assertEqual(ev.practical(p.obs, now=2.5).decision, ev.SELF_WRITE)

    def test_pi_waits_for_the_write_result(self) -> None:
        p = ev.PiParser()
        p.feed({"type": "message_start", "message": {"role": "assistant"}}, 1.0)
        p.feed({"type": "message_end", "message": {"role": "assistant", "stopReason": "toolUse", "content": [
            {"type": "toolCall", "id": "t1", "name": "write", "arguments": {"path": "new.py"}}]}}, 2.0)
        self.assertEqual(ev.practical(p.obs, now=3.0).decision, ev.PENDING)
        p.feed({"type": "tool_execution_end", "toolCallId": "t1", "toolName": "write",
                "result": {"content": [{"type": "text", "text": f"{ev.GATE_MARK}: new.py is a new file"}]}}, 3.5)
        self.assertEqual(ev.practical(p.obs, now=4.0).decision, ev.PENDING)          # blocked: no decision yet
        p.feed({"type": "message_end", "message": {"role": "assistant", "stopReason": "toolUse", "content": [
            {"type": "toolCall", "id": "t2", "name": "write", "arguments": {"path": "old.py"}}]}}, 5.0)
        p.feed({"type": "tool_execution_end", "toolCallId": "t2", "toolName": "write",
                "result": {"content": [{"type": "text", "text": "Wrote old.py"}]}}, 5.5)
        prac = ev.practical(p.obs, now=6.0)
        self.assertEqual((prac.decision, prac.looks), (ev.SELF_WRITE, 1))
        q = ev.PiParser()                                  # no result in time: the write decides
        q.feed({"type": "message_end", "message": {"role": "assistant", "content": [
            {"type": "toolCall", "id": "t1", "name": "edit", "arguments": {}}]}}, 2.0)
        self.assertEqual(ev.practical(q.obs, now=2.0 + ev.WRITE_RESULT_WAIT).decision, ev.SELF_WRITE)
        self.assertEqual(ev.practical(q.obs).decision, ev.SELF_WRITE)                 # the final result


class CoderWorkTest(unittest.TestCase):
    """A coder that came back with no write and no test run stalled (the E4B in OpenCode, 2026-10-06)."""

    def test_counts_and_stalled(self) -> None:
        tools = [ev.ToolCall("read", {"filePath": "a.py"}, 1.0), ev.ToolCall("write", {"filePath": "b.py"}, 2.0),
                 ev.ToolCall("bash", {"command": "python3 -m pytest -q"}, 3.0),
                 ev.ToolCall("bash", {"command": "cat > c.py <<'EOF'\nx\nEOF"}, 4.0)]
        w = ev.coder_work("opencode", tools)
        self.assertEqual((w.writes, w.test_runs), (2, 1))
        self.assertFalse(w.stalled(1, 1))
        idle = ev.coder_work("opencode", [ev.ToolCall("glob", {"pattern": "*"}, 1.0)])
        self.assertTrue(idle.stalled(1, 1))
        self.assertFalse(idle.stalled(0, 0))                 # no coder: not a stall
        self.assertFalse(idle.stalled(1, 0))                 # still out: not known yet


class BashWriteTest(unittest.TestCase):
    ROOT = "/tmp/agent-bench/notes"

    def test_writes(self) -> None:
        for cmd in ("echo 'x = 1' > notes/a.py", "printf 'a' >> log.txt", "cat > notes/due.py <<'EOF'\nx > 1\nEOF",
                    "cat <<EOF > a.py\nprint(1)\nEOF", "ls &> out.txt", "echo hi >| f", "echo a | tee notes/a.py",
                    "echo a | tee -a /tmp/agent-bench/notes/b.txt", "sed -i '' 's/a/b/' notes/store.py",
                    "sed -i.bak 's/a/b/' x.py", "sed -Ei 's/a/b/' x.py", "perl -pi -e 's/a/b/' x.py",
                    "mkdir -p notes/sub", "touch notes/__init__.py", "cp notes/store.py notes/store2.py",
                    "mv a.py b.py", "cd notes && echo 1 > x.txt", "X=1 echo a > b", "sudo touch f",
                    "patch -p1 < fix.diff", "git apply fix.diff", "ls; echo done > status.txt",
                    "python3 -m pytest -q > /tmp/agent-bench/notes/out.txt 2>&1"):
            self.assertTrue(ev.bash_writes(cmd, self.ROOT), cmd)
            self.assertEqual(ev.tool_class("opencode", "bash", {"command": cmd}, self.ROOT), ev.WRITE, cmd)

    def test_looks(self) -> None:
        for cmd in ("ls -la", "cat notes/store.py", "find . -name '*.py'", "rg -n Note", "grep -n '>' notes/cli.py",
                    "grep -c 'a>b' x", "python3 -c \"print(1>0)\"", "python3 -m pytest -q", "ls 2>/dev/null",
                    "python3 -m pytest 2>&1 | tail -5", "cat notes/store.py > /dev/null", "echo hi >&2",
                    "echo x > /tmp/elsewhere.txt", "echo x > ~/x.txt", "echo x > \"$OUT\"", "cp a.py /tmp/b.py",
                    "python3 - <<'EOF'\nprint(1 > 0)\nopen('x', 'w')\nEOF", "cat <<EOF\nline > here\nEOF",
                    "git apply --check fix.diff", "git diff > /dev/null", "sed -n '1,20p' x.py", "echo 'unclosed",
                    "head -5 README.md | cat", "echo 'a > b.txt'", "mkdir -p /tmp/other", "touch ../outside.txt",
                    "wc -l < notes/store.py", "cat <<<'a > b'"):
            self.assertFalse(ev.bash_writes(cmd, self.ROOT), cmd)

    def test_no_root(self) -> None:
        self.assertTrue(ev.bash_writes("echo a > a.py"))
        self.assertFalse(ev.bash_writes("echo a > /abs/a.py"))
        self.assertEqual(ev.strip_heredocs("cat > a <<EOF\nx\nEOF\nls"), "cat > a <<EOF\nls")


if __name__ == "__main__":
    unittest.main()


class StalledTest(unittest.TestCase):
    """A turn that ends by announcing an action, with no action (user, 2026-10-05: the 12B said it would research
    and never started)."""

    def test_announcements_are_stalled(self) -> None:
        from agentbench.events import stalled
        for t in ("I will research the options and report back.", "Sure. Let me look at the code first.",
                  "Good idea. I'll now create the module and its tests:", "First, I need to read the README. "
                  "Next, I will check the tests."):
            self.assertTrue(stalled(t), t)

    def test_real_answers_are_not(self) -> None:
        from agentbench.events import stalled
        for t in ("The parser splits the line on commas and returns a dict.", "It is handled in cli.py, line 42.",
                  "Let me know if you want me to change it?", "I will explain: the error means the key is missing. "
                  "Add a default in config.py.", ""):
            self.assertFalse(stalled(t), t)

    def test_stalled_is_never_right(self) -> None:
        from agentbench.events import STALLED, expected_ok
        self.assertFalse(expected_ok("delegate", STALLED))
        self.assertFalse(expected_ok("keep", STALLED))
