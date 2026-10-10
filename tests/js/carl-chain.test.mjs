// client/shared/carl-chain.js (Phase 23.4.3, item 3): the chain of a test session and a code session, with a fake
// session runner and a fake check runner (no model, no client). Run: node --test tests/js.
import assert from "node:assert/strict";
import { test } from "node:test";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import * as B from "../../client/shared/carl-brief.js";
import * as C from "../../client/shared/carl-chain.js";

// The check: a real node --test run of the test file that the test session writes (red: it fails before the code).
const RED = 'import { existsSync } from "node:fs";\nimport { test } from "node:test";\nimport assert from "node:assert";\ntest("R1", () => assert.ok(existsSync("src/feature.txt")));\n';
const GREEN = 'import { test } from "node:test";\ntest("R1", () => {});\n';
const TEST_FILE = "tests/check.test.mjs";
const RUN = `node --test ${TEST_FILE}`;

const BRIEF = (type = "new_feature", run = RUN, existing = "") => `work_mode = "code"
work_type = "${type}"
${existing ? `existing_tests = ${JSON.stringify(existing.split(" "))}\n` : ""}task_summary = "Write the feature file."
expected_outcome = "src/feature.txt is there."
current_state = "src/ is empty."
design_notes = "One file, no code."
scope_limits = "Leave the README alone."

[[known_file]]
file_path = "src/feature.txt"
file_action = "create"

[[task_requirement]]
requirement_id = "R1"
requirement_text = "src/feature.txt exists"

[[acceptance_check]]
check_id = "C1"
covers_requirements = ["R1"]
run_command = "${run}"
expected_result = "exit 0"
`;

const REPORT_TEST = `\`\`\`toml
outcome_summary = "One test for R1; it fails: no feature yet."
task_status = "done"

[[check_result]]
check_id = "C1"
run_result = "fail"
run_summary = "exit 1"

[[changed_file]]
file_path = "tests/check.test.mjs"
change_summary = "new: the test of R1"

[[test_finding]]
test_name = "tests/check.test.mjs"
requirement_id = "R1"
failure_reason = "src/feature.txt is not there"

[[open_issue]]
issue_text = "R1 says nothing about the content: I test that the file exists."
\`\`\``;

const project = () => {
  const dir = mkdtempSync(join(tmpdir(), "chain-"));
  mkdirSync(join(dir, "src"));
  mkdirSync(join(dir, "node_modules", "tests"), { recursive: true });       // a tool folder: not walked
  writeFileSync(join(dir, "node_modules", "tests", "x.py"), "");
  return dir;
};

/** A fake session runner: what each session writes and says. */
function fakeRun(dir, { testBody = RED, tamper = false, failTest = false, failCode = false, noCode = false } = {}) {
  const calls = [];
  const run = async (text, step) => {
    calls.push({ step, text });
    const { brief } = B.parseBrief(text);
    if (brief?.workMode === "tests-only") {
      if (failTest) return { ok: false, output: "the model stopped: context size" };
      mkdirSync(join(dir, "tests"), { recursive: true });
      writeFileSync(join(dir, TEST_FILE), testBody);
      return { ok: true, output: REPORT_TEST };
    }
    if (!noCode) writeFileSync(join(dir, "src", "feature.txt"), "ok\n");
    if (tamper) writeFileSync(join(dir, TEST_FILE), GREEN);
    return failCode ? { ok: false, output: "the code session crashed" } : { ok: true, output: 'task_status = "done"\noutcome_summary = "Wrote it."' };
  };
  return { run, calls };
}

test("coderPlan: the chain only for work_mode code, new_feature, not continued; a watch for follow_up and bug_fix with existing_tests", () => {
  assert.equal(C.coderPlan(BRIEF()).kind, "chain");
  assert.equal(C.coderPlan(BRIEF(), true).kind, "one");
  assert.equal(C.coderPlan(BRIEF("new_feature", RUN, TEST_FILE)).kind, "chain");  // existing tests too: still the chain
  assert.equal(C.coderPlan(BRIEF("follow_up", RUN, TEST_FILE)).kind, "watch");
  assert.equal(C.coderPlan(BRIEF("bug_fix", RUN, TEST_FILE)).kind, "watch");
  assert.equal(C.coderPlan(BRIEF("bug_fix", RUN, TEST_FILE), true).kind, "one");
  assert.equal(C.coderPlan(BRIEF("follow_up")).kind, "one");                  // no existing_tests: nothing to freeze
  assert.equal(C.coderPlan(BRIEF("bug_fix") + '\n[failed_attempt]\nrun_command = "x"\nerror_output = "y"\n').kind, "one");
  const testsOnly = BRIEF().replace('work_mode = "code"', 'work_mode = "tests-only"').replace(/\[\[known_file\]\][\s\S]*?(?=\[\[task_requirement)/, "");
  assert.deepEqual(B.checkBrief(B.parseBrief(testsOnly).brief), []);
  assert.equal(C.coderPlan(testsOnly).kind, "one");                           // tests-only: one session
  assert.equal(C.coderPlan("no brief at all").kind, "one");
  assert.equal(C.coderPlan('work_mode = "code"').kind, "one");                // incomplete: the brief check's case
});

test("the chain: a red start, the frozen tests unchanged, one result with both reports", async () => {
  const dir = project();
  const { run, calls } = fakeRun(dir);
  const ran = [];
  const runCheck = async (cmd, cwd) => {
    ran.push(cmd);
    return C.runCommand(cmd, cwd, 10_000);
  };
  const out = await C.runCoderTask(BRIEF(), dir, run, { runCheck });
  assert.equal(out.kind, "chain");
  assert.equal(out.ok, true);
  assert.deepEqual(calls.map((c) => c.step), ["test", "code"]);
  const testBrief = B.parseBrief(calls[0].text).brief;
  assert.equal(testBrief.workMode, "tests-only");
  assert.deepEqual(B.checkBrief(testBrief), []);
  assert.doesNotMatch(calls[0].text, /design_notes|known_file|One file, no code/);   // tests from the behaviour
  assert.match(calls[0].text, /expected_outcome = "src\/feature\.txt is there\."/);
  assert.match(calls[0].text, /scope_limits = "Leave the README alone\."/);
  const codeBrief = B.parseBrief(calls[1].text).brief;
  assert.equal(codeBrief.workMode, "code");
  assert.equal(codeBrief.designNotes, "One file, no code.");                  // the code session: the whole brief
  assert.deepEqual(B.checkBrief(codeBrief), []);                               // the code session's gates take it
  assert.equal(B.testSessionDue(codeBrief), false);                            // never a chain in a chain
  assert.deepEqual(codeBrief.testSession.files, [TEST_FILE]);
  assert.equal(codeBrief.testSession.summary, "One test for R1; it fails: no feature yet.");
  assert.deepEqual(codeBrief.testSession.failing, [{ test: TEST_FILE, requirement: "R1", why: "src/feature.txt is not there" }]);
  assert.deepEqual(codeBrief.testSession.notes, ["R1 says nothing about the content: I test that the file exists."]);
  assert.deepEqual(ran, [RUN]);                                                // CARL ran the check once, before the code
  assert.match(out.text, /^\[CARL\] Chain: the coder ran in two new sessions/);
  assert.match(out.text, /Red start: CARL ran `node --test tests\/check\.test\.mjs` before the code: it failed \(exit 1\)\./);
  assert.match(out.text, /Tests unchanged after the code session \(tests\/check\.test\.mjs\)\./);
  assert.match(out.text, /Run the checks yourself before you answer the user\./);
  assert.match(out.text, /^\[CARL\] Chain: the coder ran in two new sessions: first the tests \(work_mode tests-only\), then the code \(work_mode code\)\./);
  assert.match(out.text, /## The test session's report \(work_mode tests-only\)\n\n```toml\noutcome_summary = "One test/);
  assert.match(out.text, /## The code session's report \(work_mode code\)\n\ntask_status = "done"/);
  assert.doesNotMatch(out.text, /Warning/);
});

test("the chain: no red start (the new test passes before any code): the code session runs, the result warns", async () => {
  const dir = project();
  const { run, calls } = fakeRun(dir, { testBody: GREEN });
  const out = await C.runCoderTask(BRIEF(), dir, run);
  assert.deepEqual(calls.map((c) => c.step), ["test", "code"]);
  assert.equal(out.ok, true);
  assert.match(out.text, /\[CARL\] Warning: no new test failed before the code \(no red start\): CARL ran `node --test tests\/check\.test\.mjs` before the code: it passed\. The tests may not test the new behaviour/);
});

test("the red start from the report when CARL did not run the check, and a warning with neither", async () => {
  const report = B.parseReport(REPORT_TEST);
  assert.deepEqual(C.redStart(report, [{ run: "x", result: "not run", code: null, output: "" }]),
                   { red: true, why: "the test session's report has failing tests." });
  assert.deepEqual(C.redStart(null, []), { red: false, why: "the test session gave no report." });
  assert.equal(C.redStart(report, [{ run: "x", result: "pass", code: 0, output: "" }]).red, false);   // CARL's run wins
  assert.equal(C.notRunLine([{ run: "sh t.sh", result: "not run", code: null, output: "", refused: "x" }]),
               "CARL did not run `sh t.sh` itself (it runs only a plain test runner or ruff on the project's files).");
  assert.equal(C.notRunLine([{ run: "pytest", result: "not run", code: null, output: "", timedOut: true }]),
               "CARL could not run `pytest` (the time limit).");
  const slow = await C.runArgv(["sh", "-c", "sleep 5"], {}, tmpdir(), 200);
  assert.deepEqual([slow.code, slow.timedOut], [null, true]);
  const fail = await C.runArgv(["sh", "-c", "echo out; echo err >&2; exit 3"], {}, tmpdir(), 5_000);
  assert.equal(fail.code, 3);
  assert.match(fail.output, /out/);
  assert.match(fail.output, /err/);
  const env = await C.runArgv(["sh", "-c", "echo $CI"], { CI: "yes" }, tmpdir(), 5_000);
  assert.equal(env.output.trim(), "yes");
  assert.deepEqual(await C.runCommand("echo hi; exit 3", tmpdir(), 5_000),            // refused: never started
                   { code: null, timedOut: false, output: "", refused: "shell syntax" });
});

test("a check that is not a plain test runner or ruff: CARL does not run it, the report decides, one line says so", async () => {
  const dir = project();
  const { run } = fakeRun(dir);
  const ran = [];
  const out = await C.runCoderTask(BRIEF("new_feature", "sh tests/check.test.mjs"), dir, run, { runCheck: async (cmd) => (ran.push(cmd), { code: 1, timedOut: false, output: "" }) });
  assert.deepEqual(ran, []);                                                   // never started
  assert.match(out.text, /\nRed start: the test session's report has failing tests\.\nCARL did not run `sh tests\/check\.test\.mjs` itself \(it runs only a plain test runner or ruff on the project's files\)\.\n/);
});

test("the allowlist: the test runners and ruff on the project's files; anything else is refused", () => {
  const root = mkdtempSync(join(tmpdir(), "allow-"));
  const ok = (run) => C.allowedCheck(run, root);
  const yes = {
    "pytest": ["pytest"],
    "python -m pytest tests/test_a.py -q": ["python", "-m", "pytest", "tests/test_a.py", "-q"],
    "python3 -m pytest tests/test_a.py::test_one -x": ["python3", "-m", "pytest", "tests/test_a.py::test_one", "-x"],
    'pytest -k "csv and not slow" --tb=short': ["pytest", "-k", "csv and not slow", "--tb=short"],
    "node --test tests/": ["node", "--test", "tests/"],
    "npm test": ["npm", "test"],
    "npm run test -- --grep csv": ["npm", "run", "test", "--", "--grep", "csv"],
    "go test ./...": ["go", "test", "./..."],
    "cargo test parse": ["cargo", "test", "parse"],
    "ruff check .": ["ruff", "check", "."],
    "ruff format --check src": ["ruff", "format", "--check", "src"],
    "uv run pytest -q": ["uv", "run", "pytest", "-q"],
    "uv run python -m pytest": ["uv", "run", "python", "-m", "pytest"],
    "uvx ruff check src": ["uvx", "ruff", "check", "src"],
    [`pytest ${join(root, "tests")}`]: ["pytest", join(root, "tests")],          // an absolute path in the project
  };
  for (const [run, argv] of Object.entries(yes)) {
    const a = ok(run);
    assert.equal(a.ok, true, run);
    assert.deepEqual(a.argv, argv, run);
  }
  assert.deepEqual(ok("CI=1 PYTHONPATH=src pytest").env, { CI: "1", PYTHONPATH: "src" });
  assert.equal(ok("ruff check").runner, "ruff");
  assert.equal(ok("python -m pytest").runner, "pytest");
  const no = {
    "sh tests/check.sh": "not a plain test runner or ruff",
    "make test": "not a plain test runner or ruff",
    "npm run build": "not a plain test runner or ruff",
    "python tests/run.py": "not a plain test runner or ruff",
    "ruff format src": "not a plain test runner or ruff",                   // it would write the files
    "uv run --with x pytest": "not a plain test runner or ruff",
    "uvx black --check .": "uvx with another tool",
    "pytest; rm -rf ~": "shell syntax",
    "pytest && echo ok": "shell syntax",
    "pytest || true": "shell syntax",
    "pytest | tee log": "shell syntax",
    "pytest > log": "shell syntax",
    "pytest $(ls tests)": "shell syntax",
    "pytest `ls`": "shell syntax",
    "pytest tests/*.py": "shell syntax",
    "pytest ~/x": "shell syntax",
    'pytest "tests': "shell syntax",                                         // a quote that does not end
    "pytest /etc": "/etc is not in the project",
    "pytest ../other": "../other is not in the project",
    "pytest --basetemp=/tmp/x": "the value of --basetemp",
    "pytest -o cache_dir=/tmp/x": "cache_dir=/tmp/x is not in the project",
    "PATH=/tmp pytest": "the variable PATH",
    "NODE_OPTIONS=--require=x node --test": "the variable NODE_OPTIONS",
    "PYTHONPATH=/elsewhere pytest": "the value of PYTHONPATH",
    "ruff check --fix": "the flag --fix",
    "go test -exec=sudo ./...": "the flag -exec",
    "node --test -e x": "the flag -e",
    "cargo test --config x": "the flag --config",
    "npm --prefix /x test": "not a plain test runner or ruff",
  };
  for (const [run, why] of Object.entries(no)) assert.deepEqual(ok(run), { ok: false, why }, run);
  assert.equal(C.exitMeaning("pytest", 5, "no tests ran"), "not run");      // no test collected: the report decides
  assert.equal(C.exitMeaning("pytest", 1, "/usr/bin/python: No module named pytest"), "not run");
  assert.equal(C.exitMeaning("pytest", 1, "1 failed"), "fail");
  assert.equal(C.exitMeaning("pytest", 2, "ERROR collecting tests/test_a.py\nInterrupted: 1 error during collection"), "fail");
  assert.equal(C.exitMeaning("pytest", 2, "Interrupted: KeyboardInterrupt"), "not run");
  assert.equal(C.exitMeaning("pytest", 4, "ERROR: file or directory not found: tests/x.py"), "not run");
  assert.equal(C.exitMeaning("ruff", 2, ""), "not run");
  assert.equal(C.exitMeaning("go", 1, "undefined: Parse"), "fail");
  assert.equal(C.exitMeaning("node", 0, ""), "pass");
});

test("the chain: a test file changed by the code session is in the result", async () => {
  const dir = project();
  const { run } = fakeRun(dir, { tamper: true });
  const out = await C.runCoderTask(BRIEF(), dir, run);
  assert.match(out.text, /\[CARL\] Warning: the code session changed these test files after the test session: tests\/check\.test\.mjs\./);
  assert.doesNotMatch(out.text, /Tests unchanged/);
});

test("the chain stops when the test session fails; a failed code session still gives one result", async () => {
  const dir = project();
  const stop = fakeRun(dir, { failTest: true });
  const out = await C.runCoderTask(BRIEF(), dir, stop.run);
  assert.deepEqual(stop.calls.map((c) => c.step), ["test"]);
  assert.equal(out.ok, false);
  assert.match(out.text, /^\[CARL\] Chain: the test session failed, so the code session did not run\./);
  assert.match(out.text, /context size/);
  const crash = fakeRun(project(), { failCode: true });
  const out2 = await C.runCoderTask(BRIEF(), dir, crash.run);
  assert.equal(out2.ok, false);
  assert.match(out2.text, /The code session failed: its error is below\./);
  assert.match(out2.text, /the code session crashed/);
});

test("not due: one session as before; with chain: false too", async () => {
  for (const [text, o] of [[BRIEF("follow_up"), {}], [BRIEF(), { chain: false }], ["Task: fix the typo", {}]]) {
    const dir = project();
    const { run, calls } = fakeRun(dir);
    const out = await C.runCoderTask(text, dir, run, o);
    assert.equal(out.kind, "one");
    assert.deepEqual(calls.map((c) => c.step), ["one"]);
    assert.equal(calls[0].text, text);                                         // the task as it was
    assert.doesNotMatch(out.text, /\[CARL\]/);
  }
});

test("follow_up and bug_fix: no test session; the existing_tests are hashed before and after; a change is in the result", async () => {
  const dir = project();
  mkdirSync(join(dir, "tests"));
  writeFileSync(join(dir, TEST_FILE), RED);
  writeFileSync(join(dir, "tests", "other_test.py"), "");
  const run = fakeRun(dir);
  const same = await C.runCoderTask(BRIEF("follow_up", RUN, TEST_FILE), dir, run.run);
  assert.equal(same.kind, "watch");
  assert.deepEqual(run.calls.map((c) => c.step), ["one"]);
  assert.match(same.text, /\[CARL\] Tests unchanged: tests\/check\.test\.mjs\.$/);
  const changed = await C.runCoderTask(BRIEF("bug_fix", RUN, TEST_FILE), dir, fakeRun(dir, { tamper: true }).run);
  assert.match(changed.text, /\[CARL\] Warning: the coder changed these test files: tests\/check\.test\.mjs\./);
  assert.equal(readFileSync(join(dir, TEST_FILE), "utf8"), GREEN);
});

test("the test files: tool folders are not walked; an existing_tests folder gives the test files under it", () => {
  const dir = project();
  mkdirSync(join(dir, "tests", "unit"), { recursive: true });
  writeFileSync(join(dir, "tests", "unit", "a.py"), "");
  writeFileSync(join(dir, "src", "b_test.py"), "");
  writeFileSync(join(dir, "src", "main.py"), "");
  assert.deepEqual(C.projectTestFiles(dir), ["src/b_test.py", "tests/unit/a.py"]);
  const b = B.parseBrief(BRIEF("bug_fix", RUN, "tests/unit/ src/b_test.py")).brief;
  assert.deepEqual(C.existingTestFiles(b, dir), ["src/b_test.py", "tests/unit/a.py"]);
  const none = B.parseBrief(BRIEF("bug_fix", RUN, "tests/gone.py ../elsewhere.py")).brief;
  assert.deepEqual(C.existingTestFiles(none, dir), []);                         // not there: nothing to freeze
});

test("[test_session] reads back the same; ``` in a report never reaches the brief (it would end a fence)", () => {
  const b = B.parseBrief(BRIEF()).brief;
  const withTs = { ...b, testSession: { files: ["tests/a.py"], summary: "Two tests.\nBoth fail.", notes: ['say "x"'],
                                        failing: [{ test: "tests/a.py::t", requirement: "R1", why: "no module" }] } };
  assert.deepEqual(B.parseBrief(B.writeBrief(withTs)).brief, withTs);
  assert.deepEqual(B.checkBrief(withTs), []);
  // two- or three-word keys, as the rest of the brief (user, 2026-10-09: "give test_session two-word keys too")
  assert.ok(B.writeBrief(withTs).endsWith('[test_session]\ntest_files = ["tests/a.py"]\nsession_summary = """\nTwo tests.\nBoth fail."""\n' +
    'session_notes = ["say \\"x\\""]\n\n[[test_session.failing_test]]\ntest_name = "tests/a.py::t"\nrequirement_id = "R1"\n' +
    'failure_reason = "no module"\n'), B.writeBrief(withTs));
  const json = JSON.parse(B.writeBriefJson(withTs)).test_session;
  assert.deepEqual(json, { test_files: ["tests/a.py"], session_summary: "Two tests.\nBoth fail.", session_notes: ['say "x"'],
                           failing_test: [{ test_name: "tests/a.py::t", requirement_id: "R1", failure_reason: "no module" }] });
  assert.deepEqual(B.parseBrief(B.writeBriefJson(withTs)).brief, withTs);
  // the keys before the rename are not CARL's any more
  assert.deepEqual(B.checkBrief(B.parseBrief(B.writeBrief(b) + '\n[test_session]\nfiles = ["tests/a.py"]\n').brief), [
    'The brief has the key "test_session.files", which is not in the schema: remove it, or put its content in a key of the schema.']);
  const fenced = "```toml\n" + B.writeBrief(withTs) + "```\nThe brief.";
  assert.deepEqual(B.parseBrief(fenced).brief, withTs);
});

test("the chain with a JSON brief: the sessions' briefs are JSON too, with the same content", async () => {
  const dir = project();
  const { run, calls } = fakeRun(dir);
  const json = B.writeBriefJson(B.parseBrief(BRIEF()).brief);
  const plan = C.coderPlan("Here is the brief:\n```json\n" + json + "```");
  assert.equal(plan.kind, "chain");
  assert.equal(plan.format, "json");
  assert.equal(C.coderPlan(BRIEF()).format, "toml");
  const out = await C.runCoderTask(json, dir, run, { runCheck: async (cmd, cwd) => C.runCommand(cmd, cwd, 10_000) });
  assert.equal(out.kind, "chain");
  assert.deepEqual(calls.map((c) => c.step), ["test", "code"]);
  for (const c of calls) {
    assert.equal(B.parseBrief(c.text).format, "json", c.text);
    assert.doesNotThrow(() => JSON.parse(c.text));
  }
  const codeBrief = B.parseBrief(calls[1].text).brief;
  assert.equal(codeBrief.workMode, "code");
  assert.deepEqual(codeBrief.testSession.files, [TEST_FILE]);
  assert.deepEqual(B.checkBrief(codeBrief, "json"), []);
});

// ------------------------------------------------------------------ the Tests setting (Phase 23.4.6)

test("the Tests setting: coderPlan's order, off, and the state file's value (before when missing or not one)", () => {
  assert.deepEqual([C.coderPlan(BRIEF()).kind, C.coderPlan(BRIEF()).order], ["chain", "before"]);
  assert.deepEqual([C.coderPlan(BRIEF(), false, "after").kind, C.coderPlan(BRIEF(), false, "after").order], ["chain", "after"]);
  assert.deepEqual([C.coderPlan(BRIEF(), false, "off").kind, C.coderPlan(BRIEF(), false, "off").testsOff], ["one", true]);
  assert.equal(C.coderPlan(BRIEF(), false, "sometimes").order, "before");          // not a value: the default
  assert.equal(C.coderPlan(BRIEF(), true, "off").testsOff, undefined);             // continued: no test session was due
  assert.equal(C.coderPlan(BRIEF("follow_up", RUN, TEST_FILE), false, "off").kind, "watch");   // the watch stays
  assert.equal(C.coderPlan(BRIEF("follow_up"), false, "off").testsOff, undefined);
  assert.deepEqual(B.TESTS_SETTINGS, ["before", "after", "off"]);
  assert.equal(B.testsSetting("after"), "after");
  assert.equal(B.testsSetting(undefined), "before");
  const dir = project();
  const file = join(dir, "carl.json");
  assert.equal(C.testsFrom(file), "before");                                       // no file
  writeFileSync(file, JSON.stringify({ coder_tests: "off" }));
  assert.equal(C.testsFrom(file), "off");
  writeFileSync(file, JSON.stringify({ coder_tests: 3 }));
  assert.equal(C.testsFrom(file), "before");
  writeFileSync(file, "{broken");
  assert.equal(C.testsFrom(file), "before");
});

test("after code: the code session, then the test session; CARL runs the checks after both; one result", async () => {
  const dir = project();
  const { run, calls } = fakeRun(dir);
  const ran = [];
  const runCheck = async (cmd, cwd) => (ran.push(cmd), C.runCommand(cmd, cwd, 10_000));
  const out = await C.runCoderTask(BRIEF(), dir, run, { runCheck, tests: "after" });
  assert.deepEqual([out.kind, out.ok], ["chain", true]);
  assert.deepEqual(calls.map((c) => c.step), ["code", "test"]);
  const codeBrief = B.parseBrief(calls[0].text).brief;
  assert.deepEqual([codeBrief.workMode, codeBrief.designNotes, codeBrief.testSession], ["code", "One file, no code.", null]);
  const testBrief = B.parseBrief(calls[1].text).brief;
  assert.equal(testBrief.workMode, "tests-only");
  assert.doesNotMatch(calls[1].text, /design_notes|known_file|One file, no code/);   // tests from the behaviour
  assert.deepEqual(ran, [RUN]);                                                    // once, after both sessions
  assert.match(out.text, /^\[CARL\] Chain: the coder ran in two new sessions: first the code \(work_mode code\), then the tests \(work_mode tests-only\), as the Tests setting "after code" on this computer says\./);
  assert.match(out.text, /\nCARL ran `node --test tests\/check\.test\.mjs` after both sessions: it passed\./);
  assert.match(out.text, /The test session wrote: tests\/check\.test\.mjs\./);
  assert.ok(out.text.indexOf("## The code session's report") < out.text.indexOf("## The test session's report"));
  assert.doesNotMatch(out.text, /Warning|red start/i);
});

test("after code: failing new tests come back as findings; the code session's test-file change is a warning", async () => {
  const dir = project();
  const { run } = fakeRun(dir, { noCode: true });
  const out = await C.runCoderTask(BRIEF(), dir, run, { tests: "after", runCheck: (cmd, cwd) => C.runCommand(cmd, cwd, 10_000) });
  assert.match(out.text, /The new tests fail: CARL ran `node --test tests\/check\.test\.mjs` after both sessions: it failed \(exit 1\)\. Each failure is a finding \(the test session's report says why\): give the fixes to the coder \(work_type = "follow_up", the new test files in existing_tests\)\./);
  // the code session wrote a test file (its gates refuse that in a client): a warning
  const dir2 = project();
  mkdirSync(join(dir2, "tests"), { recursive: true });
  writeFileSync(join(dir2, TEST_FILE), RED);
  const tamper = fakeRun(dir2, { tamper: true });
  const out2 = await C.runCoderTask(BRIEF(), dir2, tamper.run, { tests: "after", runCheck: async () => ({ code: 0, timedOut: false, output: "" }) });
  assert.match(out2.text, /\[CARL\] Warning: the code session changed these test files: tests\/check\.test\.mjs\./);
});

test("after code: a failed code session stops the chain; the test session's own failure is said", async () => {
  const dir = project();
  const stop = fakeRun(dir, { failCode: true });
  const out = await C.runCoderTask(BRIEF(), dir, stop.run, { tests: "after" });
  assert.deepEqual([out.ok, stop.calls.map((c) => c.step)], [false, ["code"]]);
  assert.match(out.text, /^\[CARL\] Chain: the code session failed, so the test session did not run\. Its error is below\.\n\n## The code session \(work_mode code\)\n\nthe code session crashed/);
  const crash = fakeRun(project(), { failTest: true });
  const out2 = await C.runCoderTask(BRIEF(), project(), crash.run, { tests: "after", runCheck: async () => ({ code: 0, timedOut: false, output: "" }) });
  assert.equal(out2.ok, false);
  assert.match(out2.text, /The test session failed: its error is below\./);
  assert.match(out2.text, /\[CARL\] Warning: the test session wrote no test file\./);
});

test("off: one session, and the result says that no test session ran", async () => {
  const dir = project();
  const { run, calls } = fakeRun(dir);
  const out = await C.runCoderTask(BRIEF(), dir, run, { tests: "off" });
  assert.deepEqual([out.kind, calls.map((c) => c.step)], ["one", ["one"]]);
  assert.equal(calls[0].text, BRIEF());                                            // the task as the main agent wrote it
  assert.ok(out.text.endsWith(`\n\n${C.TESTS_OFF}`));
  const plain = await C.runCoderTask(BRIEF("follow_up"), dir, fakeRun(dir).run, { tests: "off" });
  assert.doesNotMatch(plain.text, /Tests setting/);                               // no test session was due
  const measured = await C.runCoderTask(BRIEF(), dir, fakeRun(dir).run, { tests: "off", chain: false });
  assert.doesNotMatch(measured.text, /Tests setting/);                            // the chain off: as before
});

test("after code: the snapshot keeps the order; an interrupted or unheld chain names its sessions", () => {
  const brief = B.parseBrief(BRIEF()).brief;
  const c = new C.Chain(brief, project(), { order: "after" });
  assert.deepEqual(c.steps, ["code", "test"]);
  c.firstOutcome({ ok: true, output: "the code report" });
  const back = C.Chain.from(JSON.parse(JSON.stringify(c.snapshot())));
  assert.deepEqual([back.order, back.codeOutput], ["after", "the code report"]);
  assert.equal(C.Chain.from({ brief, cwd: "/x" }).order, "before");               // a record from before 23.4.6
  assert.match(back.interrupted("first"), /the code session did not finish, and the test session did not run/);
  assert.match(back.interrupted("held"), /: the test session did not run\./);
  assert.match(back.interrupted("second", "half"), /the test session did not finish[\s\S]*## The code session's report \(work_mode code\)\n\nthe code report[\s\S]*## The test session's last answer \(work_mode tests-only, unfinished\)\n\nhalf/);
  assert.match(back.unheld("the code report"), /^\[CARL\] Chain: the code session ran in the background, where CARL cannot hold its result on this OpenCode, so the test session did not run\./);
  const before = new C.Chain(brief, "/x");
  assert.match(before.interrupted("first"), /the test session did not finish, and the code session did not run/);
});

// ------------------------------------------------------------------ the run gate (the 23.4.3 addendum)

const PYBRIEF = `work_mode = "code"
work_type = "follow_up"
task_summary = "Make add() handle three numbers."
expected_outcome = "add(1, 2, 3) is 6."
current_state = "calc/core.py has add(a, b)."

[[known_file]]
file_path = "calc/core.py"
file_action = "change"

[[task_requirement]]
requirement_id = "R1"
requirement_text = "add takes a third number, default 0"

[[acceptance_check]]
check_id = "C1"
covers_requirements = ["R1"]
run_command = "python3 -m pytest -q"
expected_result = "all pass"
`;

/** A Python project: calc/core.py and its tests (test_add passes; test_old fails before any work). */
function pyProject() {
  const dir = mkdtempSync(join(tmpdir(), "gate-py-"));
  mkdirSync(join(dir, "calc"));
  mkdirSync(join(dir, "tests"));
  writeFileSync(join(dir, "calc", "__init__.py"), "");
  writeFileSync(join(dir, "calc", "core.py"), "def add(a, b):\n    return a + b\n");
  writeFileSync(join(dir, "tests", "test_core.py"),
    "from calc.core import add\n\ndef test_add():\n    assert add(1, 2) == 3\n\ndef test_old():\n    assert add(1, 1) == 3\n");
  return dir;
}

const write = (dir, f, text) => writeFileSync(join(dir, f), text);
const gateOn = (rounds = 1) => ({ gate: { gate: true, rounds } });

test("run gate: a test that passed before and fails now is not done; a fix round mends it; a test failing before is not the coder's", async () => {
  const dir = pyProject();
  const steps = [];
  const run = async (text, step) => {
    steps.push([step, B.parseBrief(text).brief?.workType]);
    if (steps.length === 1) write(dir, "calc/core.py", "def add(a, b, c=0):\n    return a - b + c\n");    // breaks test_add
    else write(dir, "calc/core.py", "def add(a, b, c=0):\n    return a + b + c\n");                      // the fix
    return { ok: true, output: 'task_status = "done"' };
  };
  const out = await C.runCoderTask(PYBRIEF, dir, run, gateOn(1));
  assert.deepEqual(steps, [["one", "follow_up"], ["code", "bug_fix"]]);
  assert.match(out.text, /## Fix round 1 \(work_mode code, bug_fix\)/);
  assert.match(out.text, /\[CARL\] Run gate: done after 1 fix round\. CARL ran `python3 -m py_compile calc\/core\.py`.*`python3 -m pytest -q -rA/);
  assert.doesNotMatch(out.text, /test_old/);                                      // failing before: not the coder's
});

test("run gate: rounds used up: NOT DONE with the output and a ready bug_fix brief; 0 rounds: no fix session", async () => {
  const dir = pyProject();
  const calls = [];
  const run = async (text, step) => {
    calls.push(step);
    write(dir, "calc/core.py", "def add(a, b, c=0):\n    return a - b\n");
    return { ok: true, output: "done" };
  };
  const out = await C.runCoderTask(PYBRIEF, dir, run, gateOn(1));
  assert.deepEqual(calls, ["one", "code"]);
  assert.match(out.text, /\[CARL\] Run gate: NOT DONE after 1 fix round\.\n- a test that passed before fails now: tests\/test_core\.py::test_add: `python3 -m pytest/);
  const ready = /```toml\n([\s\S]*?)```$/.exec(out.text)[1];
  const fix = B.parseBrief(ready).brief;
  assert.deepEqual([fix.workMode, fix.workType, B.checkBrief(fix)], ["code", "bug_fix", []]);
  assert.match(fix.failedAttempt.run, /^python3 -m pytest/);
  assert.equal(fix.triedFixes.length, 2);                                         // both sessions
  const none = [];
  const dir2 = pyProject();
  await C.runCoderTask(PYBRIEF, dir2, async (t, step) => (none.push(step), write(dir2, "calc/core.py", "def add(a, b):\n    return 0\n"), { ok: true, output: "x" }), gateOn(0));
  assert.deepEqual(none, ["one"]);
});

test("run gate: compile errors, the language check, a __main__ that does not start, tests that do not collect; new failing tests are findings", async () => {
  const dir = pyProject();
  const out = await C.runCoderTask(PYBRIEF, dir, async () => {
    write(dir, "calc/core.py", "def add(a, b):\n    return a + b\n\ndef broken(:\n");
    return { ok: true, output: "x" };
  }, gateOn(0));
  assert.match(out.text, /- a changed Python file does not compile: `python3 -m py_compile calc\/core\.py`/);
  assert.match(out.text, /- the project's tests do not run: `python3 -m pytest/);
  const dir2 = pyProject();
  const out2 = await C.runCoderTask(PYBRIEF, dir2, async () => {
    write(dir2, "calc/core.py", "def add(a, b):\n    return a + b\n\ndef later():\n    return undefined_name\n");
    write(dir2, "calc/__main__.py", "raise SystemExit('no')\n");
    write(dir2, "tests/test_new.py", "from calc.core import add\n\ndef test_three():\n    assert add(1, 2) == 4\n");
    return { ok: true, output: "x" };
  }, { ...gateOn(0), has: (c) => c === "ruff" });
  assert.match(out2.text, /- the language check finds errors: `ruff check --no-cache --select E9,F63,F7,F82/);
  assert.match(out2.text, /- python -m calc does not start: `python3 -m calc --help`/);
  assert.match(out2.text, /New tests fail \(the code or the test can be wrong\): tests\/test_new\.py::test_three/);
  assert.doesNotMatch(out2.text, /test_three.*passed before/);
});

test("run gate: node, go and rust projects; off in the Coder Loop: no gate", async () => {
  const js = mkdtempSync(join(tmpdir(), "gate-js-"));
  mkdirSync(join(js, "test"));
  write(js, "lib.mjs", "export const two = () => 2;\n");
  write(js, "test/lib.test.mjs", 'import { test } from "node:test";\nimport assert from "node:assert";\nimport { two } from "../lib.mjs";\ntest("two", () => assert.equal(two(), 2));\n');
  const brief = PYBRIEF.replace("calc/core.py", "lib.mjs");
  const out = await C.runCoderTask(brief, js, async () => (write(js, "lib.mjs", "export const two = () => 3;\n"), { ok: true, output: "x" }), gateOn(0));
  assert.match(out.text, /- a test that passed before fails now: two: `node --test --test-reporter=tap`/);
  const syntax = await C.runCoderTask(brief, js, async () => (write(js, "lib.mjs", "export const two = ( => 2;\n"), { ok: true, output: "x" }), gateOn(0));
  assert.match(syntax.text, /- a changed JavaScript file does not compile: `node --check lib\.mjs`/);
  const off = await C.runCoderTask(brief, js, async () => ({ ok: true, output: "plain" }), { gate: { gate: false, rounds: 1 } });
  assert.equal(off.text, "plain");
  const has = (c) => spawnSync(c, ["--version"]).status === 0;
  if (has("go")) {
    const go = mkdtempSync(join(tmpdir(), "gate-go-"));
    write(go, "go.mod", "module calc\n\ngo 1.21\n");
    write(go, "calc.go", "package calc\n\nfunc Add(a, b int) int { return a + b }\n");
    write(go, "calc_test.go", 'package calc\n\nimport "testing"\n\nfunc TestAdd(t *testing.T) { if Add(1, 2) != 3 { t.Fatal("no") } }\n');
    const g = await C.runCoderTask(brief.replace("lib.mjs", "calc.go"), go, async () => (write(go, "calc.go", "package calc\n\nfunc Add(a, b int) int { return a - b }\n"), { ok: true, output: "x" }), gateOn(0));
    assert.match(g.text, /- a test that passed before fails now: calc\/TestAdd: `go test -json \.\/\.\.\.`/);
    const gb = await C.runCoderTask(brief.replace("lib.mjs", "calc.go"), go, async () => (write(go, "calc.go", "package calc\n\nfunc Add(a, b int) int { return a +  }\n"), { ok: true, output: "x" }), gateOn(0));
    assert.match(gb.text, /- the Go code does not build or vet: `go vet \.\/\.\.\.`/);
  }
  if (has("cargo")) {
    const rs = mkdtempSync(join(tmpdir(), "gate-rs-"));
    mkdirSync(join(rs, "src"));
    write(rs, "Cargo.toml", '[package]\nname = "calc"\nversion = "0.1.0"\nedition = "2021"\n');
    write(rs, "src/lib.rs", "pub fn add(a: i32, b: i32) -> i32 { a + b }\n#[cfg(test)]\nmod tests { #[test] fn adds() { assert_eq!(super::add(1, 2), 3); } }\n");
    const r = await C.runCoderTask(brief.replace("lib.mjs", "src/lib.rs"), rs, async () => (write(rs, "src/lib.rs", "pub fn add(a: i32, b: i32) -> i32 { a - b }\n#[cfg(test)]\nmod tests { #[test] fn adds() { assert_eq!(super::add(1, 2), 3); } }\n"), { ok: true, output: "x" }), gateOn(0));
    assert.match(r.text, /- a test that passed before fails now: tests::adds: `cargo test --no-fail-fast --color never`/);
  }
});

test("run gate: the parsers of each suite's output; the Coder Loop's settings", () => {
  assert.deepEqual(C.suiteResults("python", "PASSED tests/a.py::t1\nFAILED tests/a.py::t2 - x\n", 1), { ran: true, tests: { "tests/a.py::t1": "pass", "tests/a.py::t2": "fail" } });
  assert.equal(C.suiteResults("python", "ERROR tests/b.py - ImportError\n", 2).ran, false);
  assert.deepEqual(C.suiteResults("node", "ok 1 - a\nnot ok 2 - b\nok 3 - c # SKIP\n", 1).tests, { a: "pass", b: "fail", c: "pass" });
  assert.deepEqual(C.suiteResults("node", "✔ a (1.2ms)\n✖ b (0.3ms)\nℹ tests 2\n", 1).tests, { a: "pass", b: "fail" });
  assert.deepEqual(C.suiteResults("go", '{"Action":"pass","Package":"p","Test":"TestA"}\n{"Action":"fail","Package":"p","Test":"TestB"}\n', 1).tests, { "p/TestA": "pass", "p/TestB": "fail" });
  assert.equal(C.suiteResults("go", "FAIL p [build failed]\n", 1).ran, false);
  assert.deepEqual(C.suiteResults("rust", "test tests::a ... ok\ntest tests::b ... FAILED\n", 101).tests, { "tests::a": "pass", "tests::b": "fail" });
  assert.equal(C.suiteResults("rust", "error[E0308]: mismatched types\n", 101).ran, false);
  const dir = mkdtempSync(join(tmpdir(), "loop-"));
  assert.deepEqual(C.loopFrom(join(dir, "carl.json")), { gate: true, rounds: 1 });
  writeFileSync(join(dir, "carl.json"), JSON.stringify({ run_gate: false, fix_rounds: 3 }));
  assert.deepEqual(C.loopFrom(join(dir, "carl.json")), { gate: false, rounds: 3 });
  writeFileSync(join(dir, "carl.json"), JSON.stringify({ fix_rounds: 9 }));
  assert.deepEqual(C.loopFrom(join(dir, "carl.json")), { gate: true, rounds: 1 });
});
