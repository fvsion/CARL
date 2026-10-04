// client/shared/carl-tui.js: the helpers of the session-switcher and subagents-sidebar TUI plugins.
// Run: node --test tests/js (tests/scripts/test_js.py runs it with the other suites).
import assert from "node:assert/strict";
import { test } from "node:test";
import { cut, nodes, routeSessionID, rows } from "../../client/shared/carl-tui.js";

test("cut: one line of at most n columns, with an ellipsis when cut", () => {
  assert.equal(cut("fix  the\nparser ", 20), "fix the parser");
  assert.equal(cut("abcdef", 4), "abc…");
  assert.equal(cut(undefined, 4), "");
  assert.equal(cut("abc", 0), "…");
});

test("rows: the TUI client's { data } or a bare array", () => {
  assert.deepEqual(rows({ data: [1, 2] }), [1, 2]);
  assert.deepEqual(rows([3]), [3]);
  assert.deepEqual(rows(undefined), []);
  assert.deepEqual(rows({ error: "x" }), []);
});

test("routeSessionID: the session on the screen, none on the home screen", () => {
  const api = (current) => ({ route: { current } });
  assert.equal(routeSessionID(api({ name: "session", params: { sessionID: "ses_1" } })), "ses_1");
  assert.equal(routeSessionID(api({ name: "home" })), undefined);
  assert.equal(routeSessionID(api({ name: "session", params: { sessionID: 7 } })), undefined);
});

test("nodes: props without undefined values, children without empty ones", () => {
  const made = [];
  const solid = {
    createElement: (tag) => { const n = { tag, props: {}, kids: [] }; made.push(n); return n; },
    setProp: (node, k, v) => { node.props[k] = v; return v; },
    insert: (node, child) => { node.kids.push(child); return node; },
  };
  const { box, text } = nodes(solid);
  const t = text("red", "hi");
  const b = box({ width: "100%", onMouseDown: undefined }, [t, null, undefined, false, "plain"]);
  assert.deepEqual(t, { tag: "text", props: { fg: "red" }, kids: ["hi"] });
  assert.deepEqual(b.props, { width: "100%" });
  assert.deepEqual(b.kids, [t, "plain"]);
  assert.equal(made.length, 2);
});
