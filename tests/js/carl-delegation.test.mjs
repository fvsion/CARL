// client/shared/carl-delegation.js: the hand-off rules both clients' carl-delegation use (Phase 23).
// Run: node --test tests/js (tests/scripts/test_js.py runs it with the other suites).
import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
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

test("the gate comes from the dashboard: config.json on the server Mac, the API elsewhere, else off", async () => {
  const home = mkdtempSync(join(tmpdir(), "gate-"));
  assert.equal(await new D.GateSetting({ home }).get(), 0);                     // nothing: off
  const asked = [];
  const fetch = async (url, init) => {
    asked.push([url, init?.headers?.Authorization ?? ""]);
    return { ok: true, json: async () => ({ gate: 3, move: "off" }) };
  };
  mkdirSync(join(home, ".config", "carl"), { recursive: true });
  writeFileSync(join(home, ".config", "carl", "api-key"), "k3y\n");
  const remote = new D.GateSetting({ home, cacheApi: "http://10.0.0.2:8081", fetch });
  assert.equal(await remote.get(), 3);                                          // another computer: the API
  assert.deepEqual(asked, [["http://10.0.0.2:8081/carl/cache/settings", "Bearer k3y"]]);
  assert.equal(await remote.get(), 3);
  assert.equal(asked.length, 1);                                                // read again only after 10 s
  writeFileSync(join(home, ".config", "carl", "config.json"), JSON.stringify({ delegation: { gate: 5 } }));
  assert.equal(await new D.GateSetting({ home, cacheApi: "http://10.0.0.2:8081", fetch }).get(), 5);  // local wins
  const down = new D.GateSetting({ home: mkdtempSync(join(tmpdir(), "gate-")), cacheApi: "http://x:1",
                                   fetch: async () => { throw new Error("down"); } });
  assert.equal(await down.get(), 0);                                            // no answer: off
});

// ------------------------------------------------------------------ Phase 23.4.3: the brief check and the gates

const BRIEF = `mode = "code"
tests = "new"
goal = "Add a --csv option to the report command."

[scope]
out = [{ text = "the text table" }]

[[file]]
path = "report/csv_export.py"
action = "create"

[[file]]
path = "report/cli.py"
action = "change"

[[file]]
path = "tests/test_csv_export.py"
action = "create"

[[file]]
path = "report/model.py"
action = "read"

[[requirement]]
id = "R1"
text = "report --csv FILE writes CSV"

[[check]]
id = "A1"
covers = ["R1"]
run = "python -m pytest tests/test_csv_export.py -q"
`;
const { parseBrief } = await import("../../client/shared/carl-brief.js");
const brief = (t = BRIEF) => parseBrief(t).brief;

test("the coder's tasks in each client's call form; a continued task is not one", () => {
  assert.deepEqual(D.coderTasks("task", { subagent_type: "coder", prompt: "P" }), ["P"]);
  assert.deepEqual(D.coderTasks("task", { subagent_type: "carl-coder", prompt: "P", task_id: "t1" }), []);
  assert.deepEqual(D.coderTasks("task", { subagent_type: "explore", prompt: "P" }), []);
  assert.deepEqual(D.coderTasks("subagent", { agent: "coder", task: "A" }), ["A"]);
  assert.deepEqual(D.coderTasks("subagent", { tasks: [{ agent: "coder", task: "A" }, { agent: "browser", task: "B" }],
                                              chain: [{ agent: "carl-coder", task: "C {previous}" }] }),
                   ["A", "C the output of the step before"]);
  assert.deepEqual(D.coderTasks("bash", { command: "ls" }), []);
  assert.ok(D.isCoderName("carl-coder") && !D.isCoderName("browser"));
});

test("the brief check: a complete brief passes; a gap or no TOML is refused with what to fix", () => {
  assert.equal(D.briefCheck("task", { subagent_type: "coder", prompt: BRIEF }), "");
  assert.equal(D.briefCheck("task", { subagent_type: "coder", prompt: "Mode: code\nGoal: x", task_id: "t1" }), "");
  assert.match(D.briefCheck("task", { subagent_type: "coder", prompt: "Mode: code\nGoal: x" }),
               /^\[CARL\] Brief refused: the coder takes its task only as a TOML brief/);
  const gap = BRIEF.replace('covers = ["R1"]', "covers = []");
  assert.match(D.briefCheck("subagent", { agent: "coder", task: gap }),
               /^\[CARL\] Brief refused: the coder did not start\.[\s\S]*- Requirement R1 is in no check's covers/);
  assert.match(D.briefCheck("subagent", { tasks: [{ agent: "coder", task: BRIEF }, { agent: "coder", task: gap }] }),
               /^\[CARL\] Brief refused: coder task 2: the coder did not start/);
  assert.equal(D.briefCheck("subagent", { agent: "browser", task: "look at the page" }), "");
  assert.equal(D.briefCheck("write", { filePath: "a.py" }), "");
});

test("the files a call writes: file tools, patches, shell writes (no mkdir); here-documents are data", () => {
  assert.deepEqual(D.writtenFiles("edit", { filePath: "a.py" }), ["a.py"]);
  assert.deepEqual(D.writtenFiles("write", { path: "b.py" }), ["b.py"]);
  const patch = "*** Begin Patch\n*** Add File: n.py\n+x\n*** Update File: o.py\n*** Move to: p.py\n*** Delete File: q.py\n*** End Patch";
  assert.deepEqual(D.writtenFiles("apply_patch", { patchText: patch }), ["n.py", "o.py", "p.py", "q.py"]);
  assert.deepEqual(D.writtenFiles("bash", { command: "mkdir -p report && cat > report/a.py <<'EOF'\nx > y\nEOF" }), ["report/a.py"]);
  assert.deepEqual(D.writtenFiles("bash", { command: "sed -i '' 's/a b/c/' report/cli.py; python -m pytest -q 2>&1 | tee /tmp/log" }),
                   ["report/cli.py", "/tmp/log"]);
  assert.deepEqual(D.writtenFiles("read", { filePath: "a.py" }), []);
});

test("the coder's gates: mode code writes the brief's files, no test file, nothing else in the project", () => {
  const g = new D.CoderGate(brief(), "/p");
  assert.equal(g.before("write", { filePath: "report/csv_export.py" }), "");
  assert.equal(g.before("edit", { filePath: "/p/report/cli.py" }), "");                  // an absolute path
  assert.equal(g.before("bash", { command: "python -m pytest -q > /tmp/out.txt" }), "");   // outside the project
  assert.equal(g.before("read", { filePath: "anything.py" }), "");
  assert.equal(g.before("write", { filePath: "tests/test_csv_export.py" }),
               "[CARL] Blocked: tests/test_csv_export.py is a test file, and in mode code you do not change tests; " +
               "if a test looks wrong, write why under open issues in your report.");
  assert.equal(g.before("edit", { filePath: "report/model.py" }),
               "[CARL] Blocked: report/model.py is in your brief to read only; write the change it needs under open " +
               "issues in your report.");
  assert.equal(g.before("bash", { command: "touch report/__init__.py" }),
               "[CARL] Blocked: report/__init__.py is not in your brief's files to create or change; keep to those " +
               "files, and write what report/__init__.py needs under open issues in your report.");
  for (const why of [g.path("tests/conftest.py"), g.path("report/__init__.py")]) {
    assert.equal(why.split(". ").length, 1, why);                                         // one sentence
  }
});

test("the coder's gates: mode test writes test files only; with none in the brief, any test file", () => {
  const listed = new D.CoderGate(brief(BRIEF.replace('mode = "code"', 'mode = "test"')), "/p");
  assert.equal(listed.before("write", { filePath: "tests/test_csv_export.py" }), "");
  assert.match(listed.before("write", { filePath: "tests/test_other.py" }), /is not in your brief's files/);
  assert.equal(listed.before("edit", { filePath: "report/cli.py" }),
               "[CARL] Blocked: report/cli.py is not a test file, and in mode test you write tests only; a test that " +
               "fails because the code is wrong is a finding in your report.");
  const free = new D.CoderGate(brief('mode = "test"\ngoal = "g"\n[[requirement]]\nid = "R1"\ntext = "t"\n'), "/p");
  assert.equal(free.before("write", { filePath: "tests/test_any.py" }), "");
  assert.equal(free.before("write", { filePath: "src/x.spec.ts" }), "");
  assert.match(free.before("write", { filePath: "src/x.ts" }), /is not a test file/);
});
