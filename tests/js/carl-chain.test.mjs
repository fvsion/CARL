// client/shared/carl-chain.js (Phase 23.4.3, item 3): the chain of a test session and a code session, with a fake
// session runner and a fake check runner (no model, no client). Run: node --test tests/js.
import assert from "node:assert/strict";
import { test } from "node:test";
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

const BRIEF = (tests = "new", run = RUN) => `mode = "code"
tests = "${tests}"
goal = "Write the feature file."

[scope]
out = [{ text = "the README" }]

[[file]]
path = "src/feature.txt"
action = "create"

[[requirement]]
id = "R1"
text = "src/feature.txt exists"

[[check]]
id = "A1"
covers = ["R1"]
run = "${run}"
expect = "exit 0"
`;

const REPORT_TEST = `\`\`\`toml
status = "done"
mode = "test"
summary = "One test for R1; it fails: no feature yet."

[[check]]
id = "A1"
result = "fail"
summary = "exit 1"

[[file]]
path = "tests/check.test.mjs"
what = "new: the test of R1"

[[finding]]
test = "tests/check.test.mjs"
requirement = "R1"
why = "src/feature.txt is not there"

[[open_issue]]
text = "R1 says nothing about the content: I test that the file exists."
\`\`\``;

const project = () => {
  const dir = mkdtempSync(join(tmpdir(), "chain-"));
  mkdirSync(join(dir, "src"));
  mkdirSync(join(dir, "node_modules", "tests"), { recursive: true });       // a tool folder: not walked
  writeFileSync(join(dir, "node_modules", "tests", "x.py"), "");
  return dir;
};

/** A fake session runner: what each session writes and says. */
function fakeRun(dir, { testBody = RED, tamper = false, failTest = false, failCode = false } = {}) {
  const calls = [];
  const run = async (text, step) => {
    calls.push({ step, text });
    const { brief } = B.parseBrief(text);
    if (brief?.mode === "test") {
      if (failTest) return { ok: false, output: "the model stopped: context size" };
      mkdirSync(join(dir, "tests"), { recursive: true });
      writeFileSync(join(dir, TEST_FILE), testBody);
      return { ok: true, output: REPORT_TEST };
    }
    writeFileSync(join(dir, "src", "feature.txt"), "ok\n");
    if (tamper) writeFileSync(join(dir, TEST_FILE), GREEN);
    return failCode ? { ok: false, output: "the code session crashed" } : { ok: true, output: 'status = "done"\nmode = "code"\nsummary = "Wrote it."' };
  };
  return { run, calls };
}

test("coderPlan: the chain only for mode code, tests new, no error, not continued; a watch for tests existing", () => {
  assert.equal(C.coderPlan(BRIEF()).kind, "chain");
  assert.equal(C.coderPlan(BRIEF(), true).kind, "one");
  assert.equal(C.coderPlan(BRIEF("existing")).kind, "watch");
  assert.equal(C.coderPlan(BRIEF("none")).kind, "one");
  assert.equal(C.coderPlan(BRIEF() + '\n[error]\nrun = "x"\noutput = "y"\n').kind, "one");
  assert.equal(C.coderPlan("no brief at all").kind, "one");
  assert.equal(C.coderPlan('mode = "code"').kind, "one");                     // incomplete: the brief check's case
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
  assert.equal(testBrief.mode, "test");
  assert.deepEqual(B.checkBrief(testBrief), []);
  const codeBrief = B.parseBrief(calls[1].text).brief;
  assert.equal(codeBrief.mode, "code");
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
  assert.match(out.text, /## The test session's report \(mode test\)\n\n```toml\nstatus = "done"\nmode = "test"/);
  assert.match(out.text, /## The code session's report \(mode code\)\n\nstatus = "done"\nmode = "code"/);
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
  const out = await C.runCoderTask(BRIEF("new", "sh tests/check.test.mjs"), dir, run, { runCheck: async (cmd) => (ran.push(cmd), { code: 1, timedOut: false, output: "" }) });
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
  for (const [text, o] of [[BRIEF("none"), {}], [BRIEF(), { chain: false }], ["Task: fix the typo", {}]]) {
    const dir = project();
    const { run, calls } = fakeRun(dir);
    const out = await C.runCoderTask(text, dir, run, o);
    assert.equal(out.kind, "one");
    assert.deepEqual(calls.map((c) => c.step), ["one"]);
    assert.equal(calls[0].text, text);                                         // the task as it was
    assert.doesNotMatch(out.text, /\[CARL\]/);
  }
});

test("tests = existing: the named test files are hashed before and after; a change is in the result", async () => {
  const dir = project();
  mkdirSync(join(dir, "tests"));
  writeFileSync(join(dir, TEST_FILE), RED);
  writeFileSync(join(dir, "tests", "other_test.py"), "");
  const same = await C.runCoderTask(BRIEF("existing"), dir, fakeRun(dir).run);
  assert.equal(same.kind, "watch");
  assert.match(same.text, /\[CARL\] Tests unchanged: tests\/check\.test\.mjs\.$/);
  const changed = await C.runCoderTask(BRIEF("existing"), dir, fakeRun(dir, { tamper: true }).run);
  assert.match(changed.text, /\[CARL\] Warning: the coder changed these test files: tests\/check\.test\.mjs\./);
  assert.equal(readFileSync(join(dir, TEST_FILE), "utf8"), GREEN);
});

test("the test files: tool folders are not walked; a check's folder gives the files under it", () => {
  const dir = project();
  mkdirSync(join(dir, "tests", "unit"), { recursive: true });
  writeFileSync(join(dir, "tests", "unit", "a.py"), "");
  writeFileSync(join(dir, "src", "b_test.py"), "");
  writeFileSync(join(dir, "src", "main.py"), "");
  assert.deepEqual(C.projectTestFiles(dir), ["src/b_test.py", "tests/unit/a.py"]);
  const b = B.parseBrief(BRIEF("existing", "python -m pytest tests/unit -q")).brief;
  assert.deepEqual(C.namedTestFiles(b, dir), ["tests/unit/a.py"]);
  const none = B.parseBrief(BRIEF("existing", "pytest tests/gone.py")).brief;
  assert.deepEqual(C.namedTestFiles(none, dir), ["src/b_test.py", "tests/unit/a.py"]);   // none found: all of them
});

test("[test_session] reads back the same; ``` in a report never reaches the brief (it would end a fence)", () => {
  const b = B.parseBrief(BRIEF()).brief;
  const withTs = { ...b, testSession: { files: ["tests/a.py"], summary: "Two tests.\nBoth fail.", notes: ['say "x"'],
                                        failing: [{ test: "tests/a.py::t", requirement: "R1", why: "no module" }] } };
  assert.deepEqual(B.parseBrief(B.writeBrief(withTs)).brief, withTs);
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
  assert.equal(codeBrief.mode, "code");
  assert.deepEqual(codeBrief.testSession.files, [TEST_FILE]);
  assert.deepEqual(B.checkBrief(codeBrief, "json"), []);
});
