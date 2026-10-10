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

const BRIEF = `work_mode = "code"
work_type = "new_feature"
task_summary = "A --csv option for the report command."
expected_outcome = "report --csv FILE writes the report as CSV."

[[known_file]]
file_path = "report/csv_export.py"
file_action = "create"

[[known_file]]
file_path = "report/cli.py"
file_action = "change"

[[known_file]]
file_path = "tests/test_csv_export.py"
file_action = "create"

[[known_file]]
file_path = "report/model.py"
file_action = "read"

[[task_requirement]]
requirement_id = "R1"
requirement_text = "report --csv FILE writes CSV"

[[acceptance_check]]
check_id = "C1"
covers_requirements = ["R1"]
run_command = "python -m pytest tests/test_csv_export.py -q"
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
  const gap = BRIEF.replace('covers_requirements = ["R1"]', "covers_requirements = []");
  assert.match(D.briefCheck("subagent", { agent: "coder", task: gap }),
               /^\[CARL\] Brief refused: the coder did not start\.[\s\S]*- Requirement R1 is in no check's covers_requirements/);
  assert.match(D.briefCheck("subagent", { tasks: [{ agent: "coder", task: BRIEF }, { agent: "coder", task: gap }] }),
               /^\[CARL\] Brief refused: coder task 2: the coder did not start/);
  assert.equal(D.briefCheck("subagent", { agent: "browser", task: "look at the page" }), "");
  assert.equal(D.briefCheck("write", { filePath: "a.py" }), "");
  assert.match(D.briefCheck("task", { subagent_type: "coder", prompt: 'mode = "code"\ngoal = "x"' }),
               /- The brief has the key "mode", which is not in the schema: use "work_mode"\./);
});

test("the brief check: existing_tests in the project folder, a Pi task's own cwd in it", () => {
  const withTests = BRIEF.replace('work_type = "new_feature"', 'work_type = "new_feature"\nexisting_tests = ["tests/test_store.py"]');
  const exists = (p) => p === "/p/tests/test_store.py" || p === "/p/sub/tests/test_store.py";
  assert.equal(D.briefCheck("task", { subagent_type: "coder", prompt: withTests }, { root: "/p", exists }), "");
  assert.match(D.briefCheck("task", { subagent_type: "coder", prompt: withTests }, { root: "/q", exists }),
               /- existing_tests has "tests\/test_store\.py", which is not in the project/);
  assert.equal(D.briefCheck("task", { subagent_type: "coder", prompt: withTests }), "");      // no folder: not checked
  assert.equal(D.briefCheck("subagent", { agent: "coder", task: withTests, cwd: "sub" }, { root: "/p", exists }), "");
  assert.match(D.briefCheck("subagent", { tasks: [{ agent: "coder", task: withTests, cwd: "/elsewhere" }] }, { root: "/p", exists }),
               /not in the project/);
  assert.deepEqual(D.coderTaskItems("subagent", { cwd: "a", tasks: [{ agent: "coder", task: "T" }, { agent: "coder", task: "U", cwd: "b" }] }),
                   [{ text: "T", cwd: "a" }, { text: "U", cwd: "b" }]);
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

test("the coder's gates: work_mode code writes no test file and no read file; any other file, a new module too", () => {
  const g = new D.CoderGate(brief(), "/p");
  assert.equal(g.before("write", { filePath: "report/csv_export.py" }), "");
  assert.equal(g.before("edit", { filePath: "/p/report/cli.py" }), "");                  // an absolute path
  assert.equal(g.before("bash", { command: "python -m pytest -q > /tmp/out.txt" }), "");   // outside the project
  assert.equal(g.before("read", { filePath: "anything.py" }), "");
  // the known_file list is a start, not a limit: a module file that the design needs passes (user, 2026-10-09)
  assert.equal(g.before("bash", { command: "touch report/__init__.py" }), "");
  assert.equal(g.before("write", { filePath: "report/adapters/csv_port.py" }), "");
  assert.equal(g.before("write", { filePath: "tests/test_csv_export.py" }),
               "[CARL] Blocked: tests/test_csv_export.py is a test file, and in work_mode code you do not change tests; " +
               "if a test looks wrong, write why as an [[open_issue]] in your report.");
  assert.match(g.before("write", { filePath: "tests/conftest.py" }), /is a test file/);    // listed or not
  assert.equal(g.before("edit", { filePath: "report/model.py" }),
               "[CARL] Blocked: report/model.py is in your brief to read only (file_action read); write the change it " +
               "needs as an [[open_issue]] in your report.");
  for (const why of [g.path("tests/conftest.py"), g.path("report/model.py")]) {
    assert.equal(why.split(". ").length, 1, why);                                         // one sentence
  }
});

test("the coder's gates: work_mode tests-only writes test files only (any test file); never a read file", () => {
  const t = new D.CoderGate(brief(BRIEF.replace('work_mode = "code"', 'work_mode = "tests-only"')), "/p");
  assert.equal(t.mode, "tests-only");
  assert.equal(t.before("write", { filePath: "tests/test_csv_export.py" }), "");
  assert.equal(t.before("write", { filePath: "tests/test_other.py" }), "");               // not listed: passes
  assert.equal(t.before("write", { filePath: "src/x.spec.ts" }), "");
  assert.equal(t.before("edit", { filePath: "report/cli.py" }),
               "[CARL] Blocked: report/cli.py is not a test file, and in work_mode tests-only you write tests only; a " +
               "test that fails because the code is wrong is a [[test_finding]] in your report.");
  const readTest = new D.CoderGate(brief(BRIEF.replace('work_mode = "code"', 'work_mode = "tests-only"')
    .replace('file_path = "tests/test_csv_export.py"\nfile_action = "create"', 'file_path = "tests/test_csv_export.py"\nfile_action = "read"')), "/p");
  assert.match(readTest.before("write", { filePath: "tests/test_csv_export.py" }), /is in your brief to read only/);
});

test("addendum: after a refused code brief, a tests-only brief in the same turn is refused; a taken code brief or a new message ends it", () => {
  const turn = new D.Turn();
  const call = (prompt) => turn.brief("task", { subagent_type: "coder", prompt });
  const broken = BRIEF.replace(/\[\[acceptance_check\]\][\s\S]*$/, "");                 // no check
  const testsOnly = BRIEF.replace('work_mode = "code"', 'work_mode = "tests-only"').replace(/\[\[known_file\]\][\s\S]*?(?=\[\[task_requirement)/, "");
  assert.equal(call(testsOnly), "");                                                      // on its own: fine
  const first = call(broken);
  assert.match(first, /^\[CARL\] Brief refused: the coder did not start\. Fix these points/);
  const switched = call(testsOnly);
  assert.equal(switched, D.modeSwitchText(first));
  assert.match(switched, /^\[CARL\] Brief refused: the coder did not start\. The last brief, with work_mode = "code", was refused, and this one changes work_mode to "tests-only"\. .*Keep work_mode = "code" and fix the points of the last refusal:\n- The brief has no check/s);
  assert.equal(call(BRIEF), "");                                                          // the fixed code brief
  assert.equal(call(testsOnly), "");                                                      // then tests-only is fine
  call(broken);
  turn.reset();                                                                            // the user writes again
  assert.equal(call(testsOnly), "");
  call('work_mode = "code"\ntask_summary = not toml');                                   // a code brief that does not read
  assert.match(call(testsOnly), /changes work_mode to "tests-only"/);
  assert.equal(new D.Turn().brief("task", { subagent_type: "explore", prompt: "Find it." }), "");
});

test("addendum: the Request Check: single reminder once, on until each literal is in the brief or not_in_task, off; the user's messages since the last brief taken", () => {
  const call = (r, prompt, setting) => r.check("task", { subagent_type: "coder", prompt }, setting);
  const lacks = BRIEF;                                                          // has no "--csv-sep" nor "exit code 3"
  const r = new D.RequestCheck();
  r.user("Add `report --csv FILE`.");
  r.user("Also `--csv-sep`, and exit with code 3 on a bad path.");           // a second message of the same request
  const said = call(r, lacks, "reminder");
  assert.equal(said, "[CARL] Brief refused: the request check: these are in the user's request but not in the brief:\n" +
    "- `--csv-sep`\n- exit code 3\nCopy each into the brief where it belongs, word for word (a placeholder such as FILE or N may " +
    "stay): commands, signatures, output formats and messages in exact_interfaces, files in [[known_file]]. Then send the whole " +
    "brief again. This is a single reminder: the next brief is taken as it is.");
  assert.equal(call(r, lacks, "reminder"), "");                                // reminded once: taken
  assert.deepEqual(r.texts, []);                                               // the next request starts
  const on = new D.RequestCheck();
  on.user("Add `--csv-sep`.");
  assert.match(call(on, lacks, "on"), /- `--csv-sep`\n.*If one is not part of this task, list it in not_in_task = \["\.\.\."\]\.$/s);
  assert.match(call(on, lacks, "on"), /--csv-sep/);                            // on: again
  assert.equal(call(on, lacks.replace('expected_outcome = ', 'not_in_task = ["--csv-sep"]\nexpected_outcome = '), "on"), "");
  const off = new D.RequestCheck();
  off.user("Add `--csv-sep`.");
  assert.equal(call(off, lacks, "off"), "");
  const has = new D.RequestCheck();
  has.user("Add `report --csv FILE` writes CSV.");
  assert.equal(call(has, lacks.replace("report --csv FILE writes CSV", "`report --csv FILE` writes CSV"), "on"), "");
  assert.equal(new D.RequestCheck().check("task", { subagent_type: "explore", prompt: "x" }, "on"), "");
  assert.equal(D.requestSetting("nope"), "reminder");
  const dir = mkdtempSync(join(tmpdir(), "req-"));
  assert.equal(D.requestFrom(join(dir, "carl.json")), "reminder");
  writeFileSync(join(dir, "carl.json"), JSON.stringify({ request_check: "on" }));
  assert.equal(D.requestFrom(join(dir, "carl.json")), "on");
});

test("addendum: the free-form brief: per model (not CARL's, or a flagged CARL model), on, off; the check takes detailed_brief only when allowed", () => {
  const dir = mkdtempSync(join(tmpdir(), "free-"));
  const file = join(dir, "carl.json");
  writeFileSync(file, JSON.stringify({ providers: { llamacpp: "llamacpp" }, models: { "qwen3.6-35b-a3b": { free_form: true }, "gemma-4-e4b": {} } }));
  assert.equal(D.freeFormFor(file, "openrouter/some-model"), true);                     // not CARL's
  assert.equal(D.freeFormFor(file, "llamacpp/qwen3.6-35b-a3b"), true);                  // flagged
  assert.equal(D.freeFormFor(file, "llamacpp/gemma-4-e4b"), false);
  assert.equal(D.freeFormFor(file, ""), false);
  writeFileSync(file, JSON.stringify({ free_form: "off" }));
  assert.equal(D.freeFormFor(file, "openrouter/x"), false);
  writeFileSync(file, JSON.stringify({ free_form: "on" }));
  assert.equal(D.freeFormFor(file, "llamacpp/gemma-4-e4b"), true);
  const free = BRIEF.replace('expected_outcome = ', 'detailed_brief = """\nUse csv.writer.\n"""\nexpected_outcome = ');
  const turn = new D.Turn();
  assert.match(turn.brief("task", { subagent_type: "coder", prompt: free }, { freeForm: false }), /- The brief has the key "detailed_brief", which is not in the schema/);
  assert.equal(new D.Turn().brief("task", { subagent_type: "coder", prompt: free }, { freeForm: true }), "");
  assert.match(D.FREE_FORM_TEXT, /^\*\*Your own words in the brief\.\*\* You may add `detailed_brief = """\.\.\."""`/);
});
