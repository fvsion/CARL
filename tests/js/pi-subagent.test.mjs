// client/pi/extensions/subagent/result.js: how Pi shows a background subagent's result (the message
// renderer in index.ts uses it; the model still reads the <subagent …> text).
// Run: node --test tests/js (tests/scripts/test_js.py runs it with the other suites).
import assert from "node:assert/strict";
import { test } from "node:test";
import { agentLabel, duration, parseResult, resultBody, resultTitle } from "../../client/pi/extensions/subagent/result.js";

const tag = (state, took, body) => `<subagent id="bg-1" agent="coder" state="${state}" took="${took}">\n${body}\n</subagent>`;

test("a finished result: its title and its text, from the details", () => {
  const r = parseResult(tag("done", "42 s", "All tests pass.\nTwo files changed."),
                        { results: [], background: { id: "bg-1", agent: "coder", state: "done", took: "42 s" } });
  assert.deepEqual(r, { id: "bg-1", agent: "coder", state: "done", took: "42 s", text: "All tests pass.\nTwo files changed." });
  assert.equal(resultTitle(r), "Coder finished (42 s)");
});

test("a failed or stopped result, parsed from the tag when there are no details (an older session)", () => {
  const failed = parseResult([{ type: "text", text: tag("failed", "3 min", "npm test: 2 failures") }], undefined);
  assert.equal(resultTitle(failed), "Coder failed (3 min)");
  assert.equal(failed.text, "npm test: 2 failures");
  const stopped = parseResult(tag("stopped", "12 s", "(no output)"), {});
  assert.equal(resultTitle(stopped), "Coder was stopped (12 s)");
  assert.equal(resultTitle(parseResult("plain text", undefined)), "Subagent finished");
  assert.equal(agentLabel("carl-coder"), "Coder");
  assert.equal(agentLabel("reviewer"), "Reviewer");
});

test("collapsed: the first 3 lines and how many more; expanded: all", () => {
  const text = "1\n2\n3\n4\n5";
  assert.deepEqual(resultBody(text, false), { lines: ["1", "2", "3"], more: 2 });
  assert.deepEqual(resultBody(text, true), { lines: ["1", "2", "3", "4", "5"], more: 0 });
  assert.deepEqual(resultBody("", false), { lines: [], more: 0 });
});

test("duration: the time format of every CARL screen", () => {
  assert.deepEqual([4_000, 42_000, 61_000, 3_600_000, 4_320_000].map(duration), ["4 s", "42 s", "1 min", "1 h", "1 h 12 min"]);
});
