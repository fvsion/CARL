// client/shared/carl-delegation.js: the hand-off rules both clients' carl-delegation use (Phase 23).
// Run: node --test tests/js (tests/scripts/test_js.py runs it with the other suites).
import assert from "node:assert/strict";
import { test } from "node:test";
import * as D from "../../client/shared/carl-delegation.js";

const none = () => false;                                     // no file exists
const all = () => true;                                       // every file exists

test("the rule comes out whole, every copy; a cut rule never leaves half", () => {
  const rule = `${D.RULE_BEGIN}\nDelegate.\n${D.RULE_END}`;
  assert.equal(D.withoutRule(`a\n${rule}\nb\n${rule}`), "a\n\nb");
  assert.equal(D.withoutRule(`keep\n${D.RULE_BEGIN}\nhalf`), "keep");
  assert.equal(D.withoutRule("plain"), "plain");
});

test("the gate's number: 1-99, else off", () => {
  for (const [v, n] of [[2, 2], ["3", 3], [0, 0], [-1, 0], [100, 0], [1.5, 0], [undefined, 0], ["x", 0]]) {
    assert.equal(D.gateNumber(v), n, String(v));
  }
});

test("the reminder: the same line once, with the coder's installed name", () => {
  const once = D.withReminder("Add a CLI", "pi", "carl-coder");
  assert.ok(once.startsWith("Add a CLI\n\n[CARL reminder]"));
  assert.match(once, /agent "carl-coder"/);
  assert.equal(D.withReminder(once, "pi", "carl-coder"), once);
  assert.match(D.reminderText("opencode"), /subagent_type "coder"/);
});

test("the gate: off at 0; at N the first N-1 new files of a turn pass", () => {
  const t = new D.Turn();
  assert.equal(t.before(0, "opencode", "write", { filePath: "a.py" }, "/p", none), "");
  assert.ok(t.before(1, "opencode", "write", { filePath: "a.py" }, "/p", none).startsWith(D.GATE_MARK));
  assert.equal(t.before(1, "opencode", "edit", { filePath: "a.py" }, "/p", none), "");          // an edit passes
  assert.equal(t.before(1, "pi", "write", { path: "old.py" }, "/p", all), "");                  // an overwrite too
  const two = new D.Turn();
  assert.equal(two.before(2, "opencode", "write", { filePath: ".gitignore" }, "/p", none), "");
  assert.match(two.before(2, "pi", "bash", { command: "touch b.py" }, "/p", none, "carl-coder"), /carl-coder/);
  two.reset();
  assert.equal(two.before(2, "opencode", "write", { filePath: "c.py" }, "/p", none), "");
  assert.equal(two.before(2, "opencode", "task", { subagent_type: "coder" }, "/p", none), "");
  assert.equal(two.before(2, "opencode", "write", { filePath: "d.py" }, "/p", none), "");       // after the coder
});

test("new files: write, apply_patch, shell commands; paths against the project folder", () => {
  const seen = [];
  D.newFiles("write", { filePath: "a/b.py" }, "/proj", (p) => (seen.push(p), false));
  assert.deepEqual(seen, ["/proj/a/b.py"]);
  const patch = "*** Begin Patch\n*** Add File: src/x.ts\n+a\n*** End Patch";
  assert.deepEqual(D.newFiles("apply_patch", { patchText: patch }, "/p", none), ["src/x.ts"]);
  const abs = (p) => (p.startsWith("/") ? p : "/p/" + p);
  const nf = (c, ex = none) => D.bashNewFiles(c, "/p", ex, abs);
  assert.deepEqual(nf("cat > pkg/__init__.py <<'EOF'\nx = 1 > y\nEOF"), ["pkg/__init__.py"]);
  assert.deepEqual(nf("mkdir -p pkg tests && touch pkg/cli.py"), ["pkg", "tests", "pkg/cli.py"]);
  assert.deepEqual(nf("echo hi | tee notes.txt"), ["notes.txt"]);
  assert.deepEqual(nf("cp a.py b.py"), ["b.py"]);
  assert.deepEqual(nf("ls -la > /dev/null 2>&1; cat README.md"), []);
  assert.deepEqual(nf("echo x >> log.txt", all), []);
});

test("the coder in each client's call form", () => {
  assert.ok(D.isCoderCall("task", { subagent_type: "carl-coder" }));
  assert.ok(!D.isCoderCall("task", { subagent_type: "explore" }));
  assert.ok(D.isCoderCall("subagent", { tasks: [{ agent: "coder", task: "a" }] }));
  assert.ok(!D.isCoderCall("subagent", { agent: "browser" }));
});
