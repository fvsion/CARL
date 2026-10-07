// tools/agent-bench/variants/plugins/carl-delegate-hooks/delegate-hooks.js: the rules of the Phase 23 hook variants.
// Run: node --test tests/agent_bench (test_variants.py runs it too).
import assert from "node:assert/strict";
import { test } from "node:test";

const H = await import("../../tools/agent-bench/variants/plugins/carl-delegate-hooks/delegate-hooks.js");
const none = () => false;                                     // no file exists
const all = () => true;                                       // every file exists

test("nudge: once, on the 2nd look, before a decision", () => {
  const t = new H.Turn();
  assert.equal(t.after("nudge", "opencode", "read"), "");
  const line = t.after("nudge", "opencode", "grep");
  assert.match(line, /hand the whole task to the coder now/);
  assert.match(line, /subagent_type "coder"/);
  assert.equal(t.after("nudge", "opencode", "read"), "");   // once a turn
  t.reset();
  t.before("nudge", "pi", "subagent", { agent: "coder", task: "x" }, "/p", none);
  t.after("nudge", "pi", "read");
  assert.equal(t.after("nudge", "pi", "read"), "");         // decided: no nudge
});

test("nudge: not after a write went through, not in gate or remind mode", () => {
  const t = new H.Turn();
  assert.equal(t.before("nudge", "opencode", "write", { filePath: "new.py" }, "/p", none), "");   // no gate
  t.after("nudge", "opencode", "read");
  assert.equal(t.after("nudge", "opencode", "read"), "");
  for (const mode of ["gate", "remind"]) {
    const u = new H.Turn();
    u.after(mode, "pi", "read");
    assert.equal(u.after(mode, "pi", "read"), "");
  }
});

test("gate: a new file is stopped, an existing one passes", () => {
  const t = new H.Turn();
  const why = t.before("gate", "opencode", "write", { filePath: "pkg/new.py" }, "/p", none);
  assert.ok(why.startsWith(H.GATE_MARK));
  assert.match(why, /pkg\/new\.py/);
  assert.equal(t.before("gate", "opencode", "edit", { filePath: "a.py" }, "/p", none), "");      // an edit passes
  const u = new H.Turn();
  assert.equal(u.before("gate", "pi", "write", { path: "old.py" }, "/p", all), "");             // overwrite passes
  assert.ok(u.before("gate", "pi", "write", { path: "new.py" }, "/p", none).startsWith(H.GATE_MARK)); // still gated
});

test("gate: an apply_patch that adds a file is stopped; the coder is never stopped", () => {
  const t = new H.Turn();
  const patch = "*** Begin Patch\n*** Add File: src/x.ts\n+a\n*** End Patch";
  assert.ok(t.before("gate", "opencode", "apply_patch", { patchText: patch }, "/p", none).startsWith(H.GATE_MARK));
  assert.equal(t.before("gate", "opencode", "task", { subagent_type: "coder" }, "/p", none), "");
  assert.equal(t.before("gate", "opencode", "write", { filePath: "y.py" }, "/p", none), "");    // after the coder
});

test("paths are resolved against the project folder", () => {
  const seen = [];
  H.newFiles("write", { filePath: "a/b.py" }, "/proj", (p) => (seen.push(p), false));
  H.newFiles("write", { filePath: "/abs/c.py" }, "/proj", (p) => (seen.push(p), false));
  assert.deepEqual(seen, ["/proj/a/b.py", "/abs/c.py"]);
});

test("the coder in each client's call form", () => {
  assert.ok(H.isCoderCall("task", { subagent_type: "carl-coder" }));
  assert.ok(!H.isCoderCall("task", { subagent_type: "explore" }));
  assert.ok(H.isCoderCall("subagent", { tasks: [{ agent: "coder", task: "a" }] }));
  assert.ok(!H.isCoderCall("subagent", { agent: "browser" }));
});

test("remind: the same line once at the end", () => {
  const once = H.withReminder("Add a CLI", "pi");
  assert.ok(once.startsWith("Add a CLI\n\n[CARL reminder]"));
  assert.equal(H.withReminder(once, "pi"), once);
});

test("gate: shell commands that create files", () => {
  const abs = (p) => (p.startsWith("/") ? p : "/p/" + p);
  const nf = (c, ex = () => false) => H.bashNewFiles(c, "/p", ex, abs);
  assert.deepEqual(nf("cat > wordstat/__init__.py <<'EOF'\nx = 1 > y\nEOF"), ["wordstat/__init__.py"]);
  assert.deepEqual(nf("mkdir -p wordstat tests && touch wordstat/cli.py"), ["wordstat", "tests", "wordstat/cli.py"]);
  assert.deepEqual(nf("echo hi | tee notes.txt"), ["notes.txt"]);
  assert.deepEqual(nf("cp a.py b.py"), ["b.py"]);
  assert.deepEqual(nf("ls -la > /dev/null 2>&1; cat README.md"), []);
  assert.deepEqual(nf("echo x >> log.txt", () => true), []);                // an existing file: not new
  assert.deepEqual(nf("grep -n foo src/*.py"), []);
  const t = new H.Turn();
  assert.ok(t.before("gate", "pi", "bash", { command: "cat > new.py <<'EOF'\nprint(1)\nEOF" }, "/p", () => false)
    .startsWith(H.GATE_MARK));
  assert.equal(t.before("gate", "pi", "bash", { command: "python3 -m pytest" }, "/p", () => false), "");
  assert.equal(t.before("nudge", "pi", "bash", { command: "cat > new.py" }, "/p", () => false), "");
});

test("modes combine: remind,gate has the gate and the reminder, not the nudge", () => {
  assert.ok(H.on("remind,gate", "gate") && H.on("remind,gate", "remind") && !H.on("remind,gate", "nudge"));
  assert.ok(H.on("both", "nudge") && H.on("both", "gate") && !H.on("both", "remind"));
  const t = new H.Turn();
  t.after("remind,gate", "pi", "read");
  assert.equal(t.after("remind,gate", "pi", "read"), "");
  assert.ok(t.before("remind,gate", "pi", "write", { path: "n.py" }, "/p", () => false).startsWith(H.GATE_MARK));
});

test("gate:N: the first N-1 new files of a turn pass, the Nth is stopped", () => {
  assert.equal(H.gateFrom("remind,gate:3"), 3);
  assert.equal(H.gateFrom("gate"), 1);
  assert.equal(H.gateFrom("gate:0"), 1);
  assert.ok(H.on("gate:2", "gate"));
  const t = new H.Turn();
  assert.equal(t.before("gate:2", "opencode", "write", { filePath: ".gitignore" }, "/p", () => false), "");
  assert.ok(t.before("gate:2", "opencode", "write", { filePath: "b.py" }, "/p", () => false).startsWith(H.GATE_MARK));
  assert.ok(t.before("gate:2", "pi", "bash", { command: "touch c.py" }, "/p", () => false).startsWith(H.GATE_MARK));
  t.reset();                                                   // a new turn counts again
  assert.equal(t.before("gate:2", "opencode", "write", { filePath: "x.py" }, "/p", () => false), "");
  assert.ok(t.before("gate:2", "pi", "bash", { command: "mkdir -p pkg && touch pkg/a.py" }, "/p", () => false)
    .startsWith(H.GATE_MARK));
});
