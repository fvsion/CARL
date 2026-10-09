// client/shared/carl-brief.js: the coder's TOML brief (Phase 23.4.3): the reader, the check, the chain's helpers,
// the coder's report. Run: node --test tests/js (tests/scripts/test_js.py runs it with the other suites).
import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import * as B from "../../client/shared/carl-brief.js";

const REPO = fileURLToPath(new URL("../../", import.meta.url));

/** A complete brief (made up: a CSV export for a report CLI). */
const FULL = `mode = "code"
tests = "new"
goal = "Add a --csv option to the report command."

[scope]
in = [{ text = "the --csv option" }]
out = [
  { text = "the text table", why = "the user wants it as it is" },
]

[[file]]
path = "report/csv_export.py"
action = "create"

[[file]]
path = "./report/cli.py"
action = "change"

[[file]]
path = "report/model.py"
action = "read"

[[requirement]]
id = "R1"
text = "report --csv FILE writes CSV"

[[requirement]]
id = "R2"
text = "the header is month,orders,total"

[[check]]
id = "A1"
covers = ["R1", "R2"]
run = "python -m pytest tests/test_csv_export.py -q"
expect = "all tests pass"

[[constraint]]
text = "Standard library only."
source = "AGENTS.md"

[[example]]
source = "data/orders.csv"
text = """
order_id,date,amount
1001,2026-01-04,19.90
"""
`;

/** The brief with one change: a [section] or key replaced by the text given. */
const brief = (text = FULL) => {
  const r = B.parseBrief(text);
  assert.ok(r.brief, r.error);
  return r.brief;
};
const problems = (text) => B.checkBrief(brief(text));

// ------------------------------------------------------------------ the reader

test("TOML: each construct of the subset", () => {
  const t = B.parseToml(`# a comment
a = "basic \\"q\\" \\t \\u00e9" # after a value
b = 'literal \\n stays'
c = """
first
second \\
   joined"""
d = '''
raw \\d+'''
e = ["x", 'y', ] # a trailing comma
f = [
  { text = "one", why = "two" },  # a comment in a list
  { text = "three" },
]
g = true
h = 42
"quoted key" = "q"
x.y = "dotted"

[scope]
in = []

[[file]]
path = "a.py"

[[file]]
path = "b.py"
`);
  assert.equal(t.a, 'basic "q" \t é');
  assert.equal(t.b, "literal \\n stays");
  assert.equal(t.c, "first\nsecond joined");
  assert.equal(t.d, "raw \\d+");
  assert.deepEqual(t.e, ["x", "y"]);
  assert.deepEqual(t.f, [{ text: "one", why: "two" }, { text: "three" }]);
  assert.equal(t.g, true);
  assert.equal(t.h, 42);
  assert.equal(t["quoted key"], "q");
  assert.deepEqual(t.x, { y: "dotted" });
  assert.deepEqual(t.scope, { in: [] });
  assert.deepEqual(t.file, [{ path: "a.py" }, { path: "b.py" }]);
});

test("TOML: lenient where small models slip: an unknown escape keeps its backslash; a { table } over lines", () => {
  const t = B.parseToml('run = "grep \\d+ x"\ns = { text = "a",\n  why = "b" }\nq = """say ""hi"""""');
  assert.equal(t.run, "grep \\d+ x");
  assert.deepEqual(t.s, { text: "a", why: "b" });
  assert.equal(t.q, 'say ""hi""');
});

test("TOML: errors name the line", () => {
  const err = (text, line, re, o) => {
    assert.throws(() => B.parseToml(text, o), (e) => e instanceof B.TomlError && e.line === line && re.test(e.message),
                  text);
  };
  err('mode = "code"\ngoal = Add it', 2, /^line 2: this value has no quotes/);
  err('a = "open\nb = 1', 1, /no closing "/);
  err('a = """never closed\n\nb = 1', 1, /no closing """/);
  err('a = ["x" "y"]', 1, /comma between its items/);
  err('a = ["x",\n"y"\n', 1, /no closing \]/);
  err('a = "x"\na = "y"', 2, /the key a is there twice/);
  err('[scope]\nin = []\n[scope]', 3, /\[scope\] is there twice/);
  err('a = "x" "y"', 1, /more text after the value/);
  err('a = "x"\nThis is prose.\nb = "y"', 2, /this line is not TOML/);
  err("a =\n", 1, /value is missing/);
  err('[[file]\npath = "a"', 1, /needs \]\] at its end/);
  err('a = "x"\nb = 1 2', 12, /more text/, { firstLine: 11 });           // the line in the whole task text
});

test("the brief inside a ```toml fence, with prose around it, after Pi's 'Task:', or as the whole text", () => {
  const want = brief();
  assert.deepEqual(brief("Here is the brief for the coder.\n\n```toml\n" + FULL + "```\n\nThanks!"), want);
  assert.deepEqual(brief("Some words first.\n\n" + FULL + "\nThat is all, go ahead."), want);
  assert.deepEqual(brief("Task: " + FULL), want);
  assert.deepEqual(brief("```\n" + FULL + "```"), want);                       // a plain fence with mode = in it
  const r = B.parseBrief("Intro line.\n\n```toml\nmode = \"code\"\ngoal = oops\n```");
  assert.equal(r.brief, null);
  assert.match(r.error, /^line 5: /);                                           // the line in the whole text
  assert.equal(B.parseBrief("Intro.\nmode = \"code\"\ngoal = oops").error.slice(0, 7), "line 3:");
});

test("not TOML at all: the old Key: value form, prose, code with = in it", () => {
  for (const t of ["Mode: code\nGoal: add a CSV export\nFiles: report/csv_export.py",
                   "Please add a CSV export to the report command.",
                   "Fix this:\n    total = sum(rows)\n"]) {
    assert.equal(B.parseBrief(t).toml, false, t);
  }
});

test("the brief's fixed shape: lenient forms read the same", () => {
  const b = brief();
  assert.deepEqual(b.files, [{ path: "report/csv_export.py", action: "create" }, { path: "report/cli.py", action: "change" },
                             { path: "report/model.py", action: "read" }]);
  assert.deepEqual(b.scope.out, [{ text: "the text table", why: "the user wants it as it is" }]);
  assert.equal(b.examples[0].text, "order_id,date,amount\n1001,2026-01-04,19.90");
  const loose = brief('mode = "Code"\ntests = "NEW"\ngoal = "g"\n[scope]\nout = ["the tests"]\n[file]\npath = "a.py"\n' +
                      'action = "Create"\n[[check]]\nid = "A1"\ncovers = "R1"\nrun = "x"\n[error]\nrun = "r"\noutput = "o"');
  assert.equal(loose.mode, "code");
  assert.equal(loose.tests, "new");
  assert.deepEqual(loose.scope.out, [{ text: "the tests", why: "" }]);
  assert.deepEqual(loose.files, [{ path: "a.py", action: "create" }]);
  assert.deepEqual(loose.checks[0].covers, ["R1"]);
  assert.deepEqual(loose.error, { run: "r", output: "o" });
});

// ------------------------------------------------------------------ the check

test("checkBrief: a complete brief has no problem; so have the examples in delegation.md and the user guide, and the full plan's schema", () => {
  assert.deepEqual(problems(FULL), []);
  const rule = readFileSync(join(REPO, "client/agents/delegation.md"), "utf8");
  const ex = brief(rule.slice(rule.indexOf("```toml")));
  assert.deepEqual(B.checkBrief(ex), []);
  assert.equal(ex.requirements.length, 3);
  const guide = readFileSync(join(REPO, "USERGUIDE.md"), "utf8");                // the user guide's short example
  assert.deepEqual(B.checkBrief(brief(guide.slice(guide.indexOf("```toml\nmode = ")))), []);
});

test("checkBrief: mode, tests and goal", () => {
  assert.deepEqual(problems(FULL.replace('mode = "code"\n', "")), [
    'mode is missing: write mode = "code" (write the program code) or mode = "test" (write the tests).']);
  assert.match(problems(FULL.replace('mode = "code"', 'mode = "fix"')).join(), /mode = "fix" is not valid/);
  assert.deepEqual(problems(FULL.replace('tests = "new"\n', "")), [
    'tests is missing: write tests = "new", "existing" or "none" (the rule for tests is in your instructions).']);
  assert.match(problems(FULL.replace('tests = "new"', 'tests = "some"')).join(), /tests = "some" is not valid/);
  assert.deepEqual(problems(FULL.replace('goal = "Add a --csv option to the report command."', 'goal = "  "')),
                   ["goal is empty: write the goal in one or two sentences."]);
});

test("checkBrief: requirements, ids and the checks that cover them", () => {
  const noReq = FULL.replace(/\[\[requirement\]\][\s\S]*?(?=\[\[check\]\])/, "").replace('covers = ["R1", "R2"]', "covers = []");
  assert.ok(problems(noReq).some((p) => p.startsWith("The brief has no requirement: add a [[requirement]]")));
  assert.ok(problems(FULL.replace('id = "R2"', 'id = "R1"')).includes(
    'The id "R1" is on more than one requirement: give each requirement its own id.'));
  assert.ok(problems(FULL.replace('id = "R2"\n', "")).includes('Requirement number 2 has no id: give it id = "R2".'));
  assert.ok(problems(FULL.replace('text = "the header is month,orders,total"', 'text = ""')).includes(
    "Requirement R2 has no text: say in one point what it must do."));
  assert.deepEqual(problems(FULL.replace('covers = ["R1", "R2"]', 'covers = ["R1"]')),
                   ["Requirement R2 is in no check's covers: add it to a check, or add a check for it."]);
  assert.deepEqual(problems(FULL.replace('covers = ["R1", "R2"]', 'covers = ["R1", "R2", "R9"]')),
                   ['Check A1 covers "R9", but no requirement has that id: fix the id, or add the requirement.']);
  assert.deepEqual(problems(FULL.replace('run = "python -m pytest tests/test_csv_export.py -q"\n', "")),
                   ['Check A1 has no run: give the command that checks it, for example run = "python -m pytest tests/test_x.py".']);
  assert.ok(problems(FULL.replace('id = "A1"\n', "")).includes('Check number 1 has no id: give it id = "A1".'));
  const noCheck = FULL.replace(/\[\[check\]\][\s\S]*?(?=\[\[constraint\]\])/, "");
  assert.deepEqual(problems(noCheck), ["The brief has no check: add a [[check]] with id, covers, run and expect, so " +
                                       "that every requirement is in the covers of a check."]);
  const twice = FULL.replace("[[constraint]]", '[[check]]\nid = "A1"\ncovers = ["R1"]\nrun = "x"\n\n[[constraint]]');
  assert.ok(problems(twice).includes('The id "A1" is on more than one check: give each check its own id.'));
});

test("checkBrief: mode code needs a file to create or change and a scope.out entry; actions are checked", () => {
  const readOnly = FULL.replace(/action = "create"|action = "change"/g, 'action = "read"');
  assert.deepEqual(problems(readOnly), ['The brief has no file to create or change: add a [[file]] with path and ' +
                                        'action = "create" or "change" for each file the coder may write.']);
  assert.deepEqual(problems(FULL.replace(/out = \[\n.*\n\]/, "out = []")), [
    'scope.out is empty: add at least one thing to leave alone under [scope], for example out = [{ text = "...", why = "..." }].']);
  assert.deepEqual(problems(FULL.replace('action = "change"', 'action = "edit"')),
                   ['File report/cli.py has action = "edit": use "create", "change" or "read".']);
  assert.deepEqual(problems(FULL.replace('path = "report/model.py"\n', "")),
                   ['File number 3 has no path: give it path = "the file\'s path in the project".']);
});

test("checkBrief: mode test needs no file or scope.out, but writes test files only", () => {
  const t = FULL.replace('mode = "code"', 'mode = "test"').replace('tests = "new"\n', "")
    .replace(/\[\[file\]\][\s\S]*?(?=\[\[requirement\]\])/, "").replace(/out = \[\n.*\n\]/, "out = []");
  assert.deepEqual(problems(t), []);
  const withCode = t.replace("[[requirement]]", '[[file]]\npath = "report/cli.py"\naction = "change"\n\n[[file]]\n' +
                                                'path = "tests/test_cli.py"\naction = "create"\n\n[[requirement]]');
  assert.deepEqual(problems(withCode), ['File report/cli.py has action = "change", but in mode test the coder writes ' +
                                        'test files only: set its action to "read", or remove it.']);
});

test("checkBrief: [error] needs run and output; [[tried]] only with an [error]", () => {
  const fix = FULL.replace('tests = "new"', 'tests = "existing"') +
    '\n[error]\nrun = "python -m pytest tests/test_csv_export.py"\noutput = """\nE   AssertionError\n"""\n\n' +
    '[[tried]]\nchange = "rounded with round()"\nresult = "same error"\n';
  assert.deepEqual(problems(fix), []);
  assert.deepEqual(problems(fix.replace('run = "python -m pytest tests/test_csv_export.py"\noutput', "output")),
                   ["[error] has no run: give the command that fails."]);
  assert.deepEqual(problems(fix.replace('output = """\nE   AssertionError\n"""', 'output = ""')),
                   ["[error] has no output: copy the exact output of that command."]);
  const noError = FULL + '\n[[tried]]\nchange = "x"\nresult = "y"\n';
  assert.deepEqual(problems(noError), ["[[tried]] is only for a fix that failed: add the [error] with run and output, or remove [[tried]]."]);
  assert.deepEqual(problems(fix.replace('result = "same error"\n', "")), ["Tried number 1 has no result: say what happened."]);
});

test('checkBrief: tests = "existing" needs a check that names the existing tests', () => {
  const existing = FULL.replace('tests = "new"', 'tests = "existing"');
  assert.deepEqual(problems(existing), []);
  for (const run of ["python -m pytest tests/", "pytest tests/test_x.py::test_header", "npx vitest run src/a.test.ts",
                     "python -m unittest discover -s tests", "node --test test/"]) {
    assert.deepEqual(problems(existing.replace("python -m pytest tests/test_csv_export.py -q", run)), [], run);
  }
  assert.deepEqual(problems(existing.replace("python -m pytest tests/test_csv_export.py -q", "npm test")), [
    'tests = "existing", but no check names the existing tests: put the test file or folder in a check\'s run, ' +
    'for example run = "python -m pytest tests/test_x.py".']);
});

test("checkBrief: keys that are not in the schema", () => {
  const plural = FULL.replaceAll("[[requirement]]", "[[requirements]]").replace('covers = ["R1", "R2"]', "covers = []");
  const p = problems(plural);
  assert.equal(p[0], 'The brief has the key "requirements", which is not in the schema: use "requirement".');
  assert.ok(problems(FULL + 'notes = "x"\n').length === 0);                    // under [[example]]: its own key
  assert.deepEqual(problems('notes = "x"\n' + FULL), [
    'The brief has the key "notes", which is not in the schema: remove it, or put its content in a key of the schema.']);
  assert.ok(problems(FULL.replace("[scope]", "[scope]\ninclude = []")).includes(
    'The brief has the key "scope.include", which is not in the schema: remove it, or put its content in a key of the schema.'));
});

test("briefRefusal: one sentence for a text that is no TOML; the reader's error; the problems as a list", () => {
  assert.equal(B.briefRefusal(FULL), "");
  assert.equal(B.briefRefusal("Mode: code\nGoal: add it"),
               "[CARL] Brief refused: the coder takes its task only as a TOML brief, so write the task in the brief's " +
               "format from your instructions and send it again.");
  assert.match(B.briefRefusal('mode = "code"\ngoal = add it'),
               /^\[CARL\] Brief refused: the brief is not valid TOML \(line 2: .*\)\. Fix it and send the whole brief again\.$/);
  const r = B.briefRefusal(FULL.replace('covers = ["R1", "R2"]', 'covers = ["R1"]'));
  assert.equal(r, "[CARL] Brief refused: the coder did not start. Fix these points and send the whole brief again:\n" +
                  "- Requirement R2 is in no check's covers: add it to a check, or add a check for it.");
  for (const p of B.checkBrief(brief('tests = "x"\n[[file]]\naction = "edit"\n[[requirement]]\n[[check]]\n[error]\n'))) {
    assert.match(p, /^[A-Z[`a-z].*[.]$/, p);                                     // whole sentences
  }
});

// ------------------------------------------------------------------ the chain's helpers

test("the chain's helpers: files by action, ids, when a test session is due, its brief", () => {
  const b = brief();
  assert.deepEqual(B.filesByAction(b), { create: ["report/csv_export.py"], change: ["report/cli.py"], read: ["report/model.py"] });
  assert.deepEqual(B.requirementIds(b), ["R1", "R2"]);
  assert.deepEqual(B.checkIds(b), ["A1"]);
  assert.equal(B.testSessionDue(b), true);
  assert.equal(B.testSessionDue(b, true), false);                               // continues an earlier task
  assert.equal(B.testSessionDue({ ...b, tests: "existing" }), false);
  assert.equal(B.testSessionDue({ ...b, mode: "test" }), false);
  assert.equal(B.testSessionDue({ ...b, error: { run: "x", output: "y" } }), false);
  assert.equal(B.testSessionDue({ ...b, tried: [{ change: "a", result: "b" }] }), false);
  const withTest = brief(FULL.replace("[[requirement]]", '[[file]]\npath = "tests/test_csv_export.py"\naction = "create"\n\n[[requirement]]'));
  const t = B.testSessionBrief(withTest);
  assert.equal(t.mode, "test");
  assert.deepEqual(t.files, [{ path: "tests/test_csv_export.py", action: "create" }]);
  assert.deepEqual(t.requirements, withTest.requirements);
  assert.deepEqual(B.checkBrief(t), []);
});

test("writeBrief: TOML that reads back to the same brief", () => {
  const fix = brief(FULL + '\n[error]\nrun = "pytest"\noutput = """\nE  "x" \\\\ y\n"""\n\n[[tried]]\nchange = "a"\nresult = "b"\n');
  for (const b of [brief(), fix, B.testSessionBrief(brief())]) {
    const text = B.writeBrief(b);
    assert.deepEqual(brief(text), b, text);
  }
});

test("isTestFile: under tests/ or test/, or test_*.py, *_test.py, *.test.*, *.spec.*", () => {
  for (const p of ["tests/test_a.py", "tests/conftest.py", "test/helpers.js", "pkg/tests/data/x.csv", "test_cli.py",
                   "src/cli_test.py", "src/a.test.ts", "web/b.spec.js", "./tests/x.py"]) assert.ok(B.isTestFile(p), p);
  for (const p of ["report/cli.py", "testing.py", "latest/a.py", "src/contest.py", "test.py", "attest/test.md", "_test.py"]) {
    assert.ok(!B.isTestFile(p), p);
  }
});

test("hashFiles and changedFiles: the test-file freeze", () => {
  const dir = mkdtempSync(join(tmpdir(), "freeze-"));
  writeFileSync(join(dir, "a.py"), "x = 1\n");
  writeFileSync(join(dir, "b.py"), "y = 2\n");
  const before = B.hashFiles(["a.py", "b.py", "gone.py"], dir);
  assert.match(before["a.py"], /^[0-9a-f]{64}$/);
  assert.equal(before["gone.py"], null);
  assert.deepEqual(B.changedFiles(before, B.hashFiles(["a.py", "b.py", "gone.py"], dir)), []);
  writeFileSync(join(dir, "b.py"), "y = 3\n");
  writeFileSync(join(dir, "gone.py"), "");
  assert.deepEqual(B.changedFiles(before, B.hashFiles(["a.py", "b.py", "gone.py"], dir)), ["b.py", "gone.py"]);
  assert.deepEqual(B.changedFiles({ "a.py": "h" }, {}), ["a.py"]);
});

// ------------------------------------------------------------------ the coder's report

test("parseReport: the coder's TOML block, in a fence or plain, with a browser section after it", () => {
  const block = `status = "partly"
mode = "test"
summary = "Tests for R1 and R2."

[[requirement]]
id = "R1"
status = "done"
note = "tests/test_csv_export.py::test_r1_writes_csv"

[[check]]
id = "A1"
result = "fail"
summary = "1 failed, 1 passed in 0.03s"

[[file]]
path = "tests/test_csv_export.py"
what = "new"

[[finding]]
test = "tests/test_csv_export.py::test_r2_header"
requirement = "R2"
why = "no header row"

[[open_issue]]
text = "report/__init__.py is needed"
`;
  const want = {
    status: "partly", mode: "test", summary: "Tests for R1 and R2.", rootCause: "",
    requirements: [{ id: "R1", status: "done", note: "tests/test_csv_export.py::test_r1_writes_csv" }],
    checks: [{ id: "A1", result: "fail", summary: "1 failed, 1 passed in 0.03s" }],
    files: [{ path: "tests/test_csv_export.py", what: "new" }],
    findings: [{ test: "tests/test_csv_export.py::test_r2_header", requirement: "R2", why: "no header row" }],
    openIssues: ["report/__init__.py is needed"],
  };
  assert.deepEqual(B.parseReport("```toml\n" + block + "```\n\n## Needs a browser check\nNone."), want);
  assert.deepEqual(B.parseReport(block + "\n## Needs a browser check\nStart it with python -m http.server 8765."), want);
  assert.equal(B.parseReport("## Result\nDone."), null);
  assert.equal(B.parseReport('status = "done\n'), null);
});

// ------------------------------------------------------------------ the same brief as JSON (agent-bench's brief_json)

/** FULL as JSON: the same keys and structure. */
const FULL_JSON = {
  mode: "code", tests: "new", goal: "Add a --csv option to the report command.",
  scope: { in: [{ text: "the --csv option" }], out: [{ text: "the text table", why: "the user wants it as it is" }] },
  file: [{ path: "report/csv_export.py", action: "create" }, { path: "./report/cli.py", action: "change" },
         { path: "report/model.py", action: "read" }],
  requirement: [{ id: "R1", text: "report --csv FILE writes CSV" }, { id: "R2", text: "the header is month,orders,total" }],
  check: [{ id: "A1", covers: ["R1", "R2"], run: "python -m pytest tests/test_csv_export.py -q", expect: "all tests pass" }],
  constraint: [{ text: "Standard library only.", source: "AGENTS.md" }],
  example: [{ source: "data/orders.csv", text: "\norder_id,date,amount\n1001,2026-01-04,19.90\n" }],
};
const JSON_TEXT = JSON.stringify(FULL_JSON, null, 2);

test("JSON: the reader; lenient where small models slip; errors name the line", () => {
  assert.deepEqual(B.parseJson('{"a": "x", "b": [1, 2.5, -3e2, true, false], "c": {"d": "\\u00e9\\n\\"q\\""}}'),
                   { a: "x", b: [1, 2.5, -300, true, false], c: { d: 'é\n"q"' } });
  assert.deepEqual(B.parseJson('{"a": ["x", "y",], "b": "one\ntwo", "c": "a \\d b",}'), { a: ["x", "y"], b: "one\ntwo", c: "a \\d b" });
  assert.deepEqual(B.parseJson('{"error": null, "tried": [null, {"change": "a"}]}'), { tried: [{ change: "a" }] });
  const err = (text, line, re, o) => {
    assert.throws(() => B.parseJson(text, o), (e) => e instanceof B.JsonError && e.line === line && re.test(e.message), text);
  };
  err('{\n  "mode": "code",\n  "goal": Add it\n}', 3, /^line 3: this value has no quotes/);
  err('{\n  "mode": "code"\n  "goal": "x"\n}', 3, /needs a comma between its keys/);
  err('{"a": ["x" "y"]}', 1, /a list needs a comma/);
  err('{\n  mode: "code"\n}', 2, /a key needs quotes/);
  err('{"a" "x"}', 1, /a key needs : and a value/);
  err('{"a": "x",\n"a": "y"}', 2, /the key "a" is there twice/);
  err('{"a": "open\n}', 1, /no closing "/);
  err('{"a": {"b": 1}', 1, /no closing \}/);
  err('{"a": }', 1, /the value is missing/);
  err('{"a": 1} {"b": 2}', 1, /more text after the closing \}/);
  err('["a"]', 1, /not a JSON object/);
  err('{\n"a": x}', 12, /no quotes/, { firstLine: 11 });
});

test("JSON: the brief in a ```json fence, a plain fence, with prose around it, after Pi's 'Task:': the same brief as the TOML", () => {
  const want = brief();
  for (const t of ["Here is the brief.\n\n```json\n" + JSON_TEXT + "\n```\n\nThanks!", "```\n" + JSON_TEXT + "\n```",
                   "Some words first.\n\n" + JSON_TEXT + "\nThat is all, go ahead.", "Task: " + JSON_TEXT, JSON_TEXT]) {
    const r = B.parseBrief(t);
    assert.equal(r.format, "json", t);
    assert.equal(r.toml, false);
    assert.deepEqual(r.brief, want, t);
  }
  assert.equal(B.parseBrief(FULL).format, "toml");
  assert.equal(B.parseBrief("```toml\n" + FULL + "```\n\n```json\n{\"mode\": \"x\"}\n```").format, "toml");   // the first fence
  // a TOML brief whose inline tables start lines is TOML, not JSON
  assert.equal(B.parseBrief(FULL.replace('in = [{ text = "the --csv option" }]', 'in = [\n  { text = "the --csv option" },\n]')).format, "toml");
  for (const t of ["Mode: code\nGoal: add it", "Use {x} here.", '{"name": "not a brief"}']) assert.equal(B.parseBrief(t).format, "", t);
  const bad = B.parseBrief('Intro.\n\n```json\n{\n  "mode": "code",\n  "goal": oops\n}\n```');
  assert.equal(bad.brief, null);
  assert.equal(bad.format, "json");
  assert.match(bad.error, /^line 6: this value has no quotes/);                   // the line in the whole text
  const open = B.parseBrief('Intro.\n{\n  "mode": "code",\n  "goal": "x"\n');      // no closing }: the reader says so
  assert.match(open.error, /no closing \}/);
});

test("JSON: the check and the refusal name the keys the JSON way", () => {
  const json = (o) => B.checkBrief(B.parseBrief(JSON.stringify(o)).brief, "json");
  assert.deepEqual(json(FULL_JSON), []);
  const { mode, ...noMode } = FULL_JSON;
  assert.deepEqual(json(noMode), ['mode is missing: write "mode": "code" (write the program code) or "mode": "test" (write the tests).']);
  assert.deepEqual(json({ ...FULL_JSON, tests: "some" }), ['"tests": "some" is not valid: write "tests": "new", "existing" or "none".']);
  assert.deepEqual(json({ ...FULL_JSON, scope: { in: [] } }), [
    'scope.out is empty: add at least one thing to leave alone in "scope", for example "out": [{ "text": "...", "why": "..." }].']);
  assert.deepEqual(json({ ...FULL_JSON, file: [{ path: "a.py", action: "read" }] }), [
    'The brief has no file to create or change: add an item to "file" with path and "action": "create" or "change" for each file the coder may write.']);
  assert.deepEqual(json({ ...FULL_JSON, check: [{ id: "A1", covers: [], run: "" }] }), [
    'Check A1 has no run: give the command that checks it, for example "run": "python -m pytest tests/test_x.py".',
    'Check A1 covers no requirement: write "covers": ["R1"] with the ids it checks.',
    "Requirement R1 is in no check's covers: add it to a check, or add a check for it.",
    "Requirement R2 is in no check's covers: add it to a check, or add a check for it."]);
  assert.deepEqual(json({ ...FULL_JSON, tried: [{ change: "a", result: "b" }] }), [
    '"tried" is only for a fix that failed: add the "error" with run and output, or remove "tried".']);
  assert.deepEqual(json({ ...FULL_JSON, requirements: FULL_JSON.requirement }), [
    'The brief has the key "requirements", which is not in the schema: use "requirement".']);
  // the same gaps in TOML keep the TOML sentences
  assert.deepEqual(B.checkBrief(brief(FULL.replace('mode = "code"\n', ""))), [
    'mode is missing: write mode = "code" (write the program code) or mode = "test" (write the tests).']);
  assert.equal(B.briefRefusal(JSON_TEXT), "");
  assert.match(B.briefRefusal('```json\n{"mode": "code", "goal": oops}\n```'),
               /^\[CARL\] Brief refused: the brief is not valid JSON \(line 2: this value has no quotes.*\)\. Fix it and send the whole brief again\.$/);
  assert.equal(B.briefRefusal(JSON.stringify({ ...FULL_JSON, check: [{ ...FULL_JSON.check[0], covers: ["R1"] }] })),
               "[CARL] Brief refused: the coder did not start. Fix these points and send the whole brief again:\n" +
               "- Requirement R2 is in no check's covers: add it to a check, or add a check for it.");
  assert.equal(B.briefRefusal("Mode: code\nGoal: add it", { format: "json" }),
               "[CARL] Brief refused: the coder takes its task only as a JSON brief, so write the task in the brief's " +
               "format from your instructions and send it again.");
  assert.match(B.briefRefusal("Mode: code"), /only as a TOML brief/);
});

test("writeBriefJson: JSON with the TOML's structure that reads back to the same brief", () => {
  const fix = brief(FULL + '\n[error]\nrun = "pytest"\noutput = """\nE  "x" \\\\ y\n"""\n\n[[tried]]\nchange = "a"\nresult = "b"\n');
  const ts = { ...brief(), testSession: { files: ["tests/t.py"], summary: "two tests", failing: [{ test: "t", requirement: "R1", why: "w" }], notes: ["n"] } };
  for (const b of [brief(), fix, B.testSessionBrief(brief()), ts]) {
    const text = B.writeBriefJson(b);
    const r = B.parseBrief(text);
    assert.equal(r.format, "json");
    assert.deepEqual(r.brief, b, text);
    assert.deepEqual(B.parseBrief(B.writeBrief(b)).brief, r.brief);              // TOML and JSON: one brief
  }
  const o = JSON.parse(B.writeBriefJson(brief()));
  assert.deepEqual(Object.keys(o), ["mode", "tests", "goal", "scope", "file", "requirement", "check", "constraint", "example"]);
  assert.deepEqual(o.scope.in, [{ text: "the --csv option" }]);                    // an empty why is left out
});

test("parseReport: the same report as JSON, in a fence or plain", () => {
  const raw = {
    status: "partly", mode: "test", summary: "Tests for R1.",
    requirement: [{ id: "R1", status: "done", note: "n" }], check: [{ id: "A1", result: "FAIL", summary: "1 failed" }],
    file: [{ path: "./tests/t.py", what: "new" }], finding: [{ test: "t", requirement: "R1", why: "w" }],
    open_issue: [{ text: "x" }],
  };
  const want = {
    status: "partly", mode: "test", summary: "Tests for R1.", rootCause: "",
    requirements: [{ id: "R1", status: "done", note: "n" }], checks: [{ id: "A1", result: "fail", summary: "1 failed" }],
    files: [{ path: "tests/t.py", what: "new" }], findings: [{ test: "t", requirement: "R1", why: "w" }], openIssues: ["x"],
  };
  const text = JSON.stringify(raw, null, 2);
  assert.deepEqual(B.parseReport("```json\n" + text + "\n```\n\n## Needs a browser check\nNone."), want);
  assert.deepEqual(B.parseReport("My report:\n" + text + "\nThat is all."), want);
  assert.equal(B.parseReport('```json\n{"status": "done",\n```'), null);
});
