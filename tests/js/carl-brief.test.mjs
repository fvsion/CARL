// client/shared/carl-brief.js: the coder's TOML brief, revision 4 (Phase 23.4.3): the reader, the check, the chain's helpers,
// the coder's report. Run: node --test tests/js (tests/scripts/test_js.py runs it with the other suites).
import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import * as B from "../../client/shared/carl-brief.js";

const REPO = fileURLToPath(new URL("../../", import.meta.url));

/** A complete brief of revision 4 (made up: due dates for a notes CLI). */
const FULL = `work_mode = "code"
work_type = "new_feature"
existing_tests = ["tests/test_store.py"]

task_summary = """
Notes are getting due dates, so the user can see what is late and what comes next.
"""
expected_outcome = """
A note can have a due date. \`notes list\` shows it after the text.
"""
current_state = "notes/model.py has the Note dataclass; notes/store.py loads and saves notes.json."
design_notes = "The date is parsed and checked in the model; the CLI only formats."
exact_interfaces = [
  "notes add TEXT --due 2026-11-01",
  "a bad date is rejected with exit code 2",
]
scope_limits = "No API changes."

[[reference_doc]]
doc_path = "docs/architecture.md"
doc_purpose = "the ports and adapters layout"

[[known_file]]
file_path = "notes/due.py"
file_action = "create"

[[known_file]]
file_path = "./notes/model.py"
file_action = "change"

[[known_file]]
file_path = "notes/store.py"
file_action = "read"

[[task_requirement]]
requirement_id = "R1"
requirement_text = "A note has an optional due date (YYYY-MM-DD)."

[[task_requirement]]
requirement_id = "R2"
requirement_text = "notes list shows the date after the text."

[[acceptance_check]]
check_id = "C1"
covers_requirements = ["R1", "R2"]
run_command = "python -m pytest tests/test_model.py -q"
expected_result = "all tests pass"

[[project_rule]]
rule_text = "Functions have type hints."
rule_source = "AGENTS.md"

[[input_example]]
example_source = "notes.json"
example_text = """
[{"id": 1, "text": "buy milk", "done": false}]
"""
`;

/** FULL as a bug fix that already failed. */
const FIX = FULL.replace('work_type = "new_feature"', 'work_type = "bug_fix"') +
  '\n[failed_attempt]\nrun_command = "python -m pytest tests/test_model.py"\nerror_output = """\nE   AssertionError\n"""\n\n' +
  '[[tried_fix]]\nfix_change = "parsed with strptime"\nfix_result = "same error"\n';

/** The brief with one change: a [section] or key replaced by the text given. */
const brief = (text = FULL) => {
  const r = B.parseBrief(text);
  assert.ok(r.brief, r.error);
  return r.brief;
};
const problems = (text, o) => B.checkBrief(brief(text), "toml", o);

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
  assert.deepEqual(brief("```\n" + FULL + "```"), want);                       // a plain fence with work_mode = in it
  const r = B.parseBrief("Intro line.\n\n```toml\nwork_mode = \"code\"\ntask_summary = oops\n```");
  assert.equal(r.brief, null);
  assert.match(r.error, /^line 5: /);                                           // the line in the whole text
  assert.equal(B.parseBrief("Intro.\nwork_mode = \"code\"\ntask_summary = oops").error.slice(0, 7), "line 3:");
});

test("not TOML at all: the old Key: value form, prose, code with = in it", () => {
  for (const t of ["Mode: code\nGoal: add a CSV export\nFiles: report/csv_export.py",
                   "Please add a CSV export to the report command.",
                   "Fix this:\n    total = sum(rows)\n"]) {
    assert.equal(B.parseBrief(t).toml, false, t);
  }
});

test("the brief's fixed shape: every key of revision 4; lenient forms read the same", () => {
  const b = brief();
  assert.equal(b.workMode, "code");
  assert.equal(b.workType, "new_feature");
  assert.deepEqual(b.existingTests, ["tests/test_store.py"]);
  assert.equal(b.taskSummary, "Notes are getting due dates, so the user can see what is late and what comes next.");
  assert.equal(b.expectedOutcome, "A note can have a due date. `notes list` shows it after the text.");
  assert.match(b.currentState, /^notes\/model\.py has the Note dataclass/);
  assert.match(b.designNotes, /^The date is parsed/);
  assert.deepEqual(b.exactInterfaces, ["notes add TEXT --due 2026-11-01", "a bad date is rejected with exit code 2"]);
  assert.equal(b.scopeLimits, "No API changes.");
  assert.deepEqual(b.referenceDocs, [{ path: "docs/architecture.md", purpose: "the ports and adapters layout" }]);
  assert.deepEqual(b.files, [{ path: "notes/due.py", action: "create" }, { path: "notes/model.py", action: "change" },
                             { path: "notes/store.py", action: "read" }]);
  assert.deepEqual(b.requirements.map((r) => r.id), ["R1", "R2"]);
  assert.deepEqual(b.checks, [{ id: "C1", covers: ["R1", "R2"], run: "python -m pytest tests/test_model.py -q", expect: "all tests pass" }]);
  assert.deepEqual(b.projectRules, [{ text: "Functions have type hints.", source: "AGENTS.md" }]);
  assert.deepEqual(b.inputExamples, [{ source: "notes.json", text: '[{"id": 1, "text": "buy milk", "done": false}]' }]);
  assert.equal(b.failedAttempt, null);
  assert.deepEqual(b.triedFixes, []);
  assert.deepEqual(b.unknown, []);
  const fix = brief(FIX);
  assert.deepEqual(fix.failedAttempt, { run: "python -m pytest tests/test_model.py", output: "E   AssertionError" });
  assert.deepEqual(fix.triedFixes, [{ change: "parsed with strptime", result: "same error" }]);
  const loose = brief('work_mode = "Code"\nwork_type = "NEW_FEATURE"\nexisting_tests = "./tests/a.py"\n' +
                      '[known_file]\nfile_path = "a.py"\nfile_action = "Create"\n[[acceptance_check]]\ncheck_id = "C1"\n' +
                      'covers_requirements = "R1"\nrun_command = "x"\n');
  assert.equal(loose.workMode, "code");
  assert.equal(loose.workType, "new_feature");
  assert.deepEqual(loose.existingTests, ["tests/a.py"]);
  assert.deepEqual(loose.files, [{ path: "a.py", action: "create" }]);
  assert.deepEqual(loose.checks[0].covers, ["R1"]);
});

// ------------------------------------------------------------------ the check

test("checkBrief: a complete brief has no problem; so has the template in delegation.md as written, and the user guide's example", () => {
  assert.deepEqual(problems(FULL), []);
  assert.deepEqual(problems(FIX), []);
  const rule = readFileSync(join(REPO, "client/agents/delegation.md"), "utf8");
  const template = rule.slice(rule.indexOf("```toml"), rule.indexOf("```", rule.indexOf("```toml") + 7) + 3);
  // the bug-fix tables are commented out under one line (user, 2026-10-09: "A"); tests never read docs/ (git-ignored)
  assert.match(template, /^# For work_type = "bug_fix" only \(a fix that already failed\):\n# \[failed_attempt\]/m);
  assert.match(template, /^# \[\[tried_fix\]\]/m);
  const ex = brief(template);                                                   // the template as written: valid
  assert.deepEqual(ex.unknown, []);
  assert.equal(ex.exactInterfaces.length, 3);
  assert.ok(!ex.failedAttempt && ex.triedFixes.length === 0 && ex.referenceDocs.length === 1);
  assert.deepEqual(B.checkBrief(ex), []);
  // the bug-fix tables uncommented, with work_type = "bug_fix": valid too; with new_feature: refused
  const uncommented = template.replace(/^# For work_type = "bug_fix" only.*\n/m, "").replace(/^# /gm, "");
  const fix = brief(uncommented.replace('"new_feature"', '"bug_fix"'));
  assert.ok(fix.failedAttempt && fix.triedFixes.length === 1);
  assert.deepEqual(B.checkBrief(fix), []);
  assert.deepEqual(B.checkBrief(brief(uncommented)), ['[failed_attempt] and [[tried_fix]] are only for work_type = "bug_fix" ' +
                                                      '(a fix that already failed): set work_type = "bug_fix", or remove them.']);
  const guide = readFileSync(join(REPO, "USERGUIDE.md"), "utf8");                // the user guide's short example
  assert.deepEqual(B.checkBrief(brief(guide.slice(guide.indexOf("```toml\nwork_mode = ")))), []);
});

test("checkBrief: work_mode, work_type, task_summary, expected_outcome, current_state", () => {
  assert.deepEqual(problems(FULL.replace('work_mode = "code"\n', "")), [
    'work_mode is missing: write work_mode = "code" (the hand-off writes or changes program code) or work_mode = "tests-only" (it writes or changes tests only).']);
  assert.deepEqual(problems(FULL.replace('work_mode = "code"', 'work_mode = "test"')), [
    'work_mode = "test" is not valid: write work_mode = "code" or work_mode = "tests-only".']);
  assert.deepEqual(problems(FULL.replace('work_type = "new_feature"\n', "")), [
    'work_type is missing: write work_type = "new_feature" (new behaviour), "follow_up" (changes to the work the coder just did) or "bug_fix" (a fix).']);
  assert.deepEqual(problems(FULL.replace('work_type = "new_feature"', 'work_type = "refactor"')), [
    'work_type = "refactor" is not valid: write work_type = "new_feature", "follow_up" or "bug_fix".']);
  assert.deepEqual(problems(FULL.replace(/task_summary = """[\s\S]*?"""/, 'task_summary = "  "')),
                   ["task_summary is empty: write the request distilled into a few sentences: what and why."]);
  assert.deepEqual(problems(FULL.replace(/expected_outcome = """[\s\S]*?"""\n/, "")),
                   ["expected_outcome is empty: write what the finished work looks like from the user's side."]);
  const noState = FULL.replace(/current_state = .*\n/, "");
  assert.deepEqual(problems(noState), []);                                     // new_feature: current_state optional
  assert.deepEqual(problems(noState.replace('"new_feature"', '"follow_up"')), [
    'current_state is empty: with work_type = "follow_up", write what exists now that the coder builds on.']);
  assert.deepEqual(problems(noState.replace('"new_feature"', '"bug_fix"')), [
    'current_state is empty: with work_type = "bug_fix", write what exists now that the coder builds on.']);
  // optional: design_notes, exact_interfaces, scope_limits, reference_doc, project_rule, input_example, existing_tests
  const bare = FULL.replace(/design_notes = .*\n/, "").replace(/exact_interfaces = \[[\s\S]*?\]\n/, "")
    .replace(/scope_limits = .*\n/, "").replace(/existing_tests = .*\n/, "")
    .replace(/\[\[reference_doc\]\][\s\S]*?(?=\[\[known_file\]\])/, "").replace(/\[\[project_rule\]\][\s\S]*$/, "");
  assert.deepEqual(problems(bare), []);
  const b = brief(bare);
  assert.deepEqual([b.designNotes, b.exactInterfaces, b.scopeLimits, b.referenceDocs, b.projectRules, b.inputExamples],
                   ["", [], "", [], [], []]);
});

test("checkBrief: existing_tests must be in the project (with the project folder; without it, not checked)", () => {
  const root = mkdtempSync(join(tmpdir(), "brief-root-"));
  mkdirSync(join(root, "tests"));
  writeFileSync(join(root, "tests", "test_store.py"), "");
  assert.deepEqual(problems(FULL, { root }), []);
  assert.deepEqual(problems(FULL.replace("tests/test_store.py", "tests/"), { root }), []);    // a folder is there too
  const gone = FULL.replace('existing_tests = ["tests/test_store.py"]', 'existing_tests = ["tests/test_store.py", "tests/test_gone.py", "../other/test_x.py"]');
  assert.deepEqual(problems(gone, { root }), [
    'existing_tests has "tests/test_gone.py", which is not in the project: give the path of a test file that exists, or remove it.',
    'existing_tests has "../other/test_x.py", which is not in the project: give the path of a test file that exists, or remove it.']);
  assert.deepEqual(problems(gone), []);                                        // no project folder: not checked
  assert.deepEqual(problems(gone, { root: "/p", exists: (p) => p === "/p/tests/test_store.py" }).length, 2);
});

test("checkBrief: known_file: its path and file_action; in work_mode code one to create or change; in tests-only test files only", () => {
  const readOnly = FULL.replace(/file_action = "create"|file_action = "change"/g, 'file_action = "read"');
  assert.deepEqual(problems(readOnly), ['The brief has no file to create or change: add a [[known_file]] with file_path and ' +
                                        'file_action = "create" or "change" for a file you know the coder will write (the list does not have to be complete).']);
  assert.deepEqual(problems(FULL.replace('file_action = "change"', 'file_action = "edit"')),
                   ['Known file notes/model.py has file_action = "edit": use "create", "change" or "read".']);
  assert.deepEqual(problems(FULL.replace('file_action = "change"\n', "")),
                   ['Known file notes/model.py has no file_action: write file_action = "create", "change" or "read".']);
  assert.deepEqual(problems(FULL.replace('file_path = "notes/store.py"\n', "")),
                   ['Known file number 3 has no file_path: give it file_path = "the file\'s path in the project".']);
  const tests = FULL.replace('work_mode = "code"', 'work_mode = "tests-only"');
  assert.deepEqual(problems(tests), [
    'Known file notes/due.py has file_action = "create", but with work_mode = "tests-only" the coder writes test files only: set its file_action to "read", or remove it.',
    'Known file notes/model.py has file_action = "change", but with work_mode = "tests-only" the coder writes test files only: set its file_action to "read", or remove it.']);
  const noFiles = tests.replace(/\[\[known_file\]\][\s\S]*?(?=\[\[task_requirement\]\])/, "");
  assert.deepEqual(problems(noFiles), []);                                     // tests-only: no known_file needed
  assert.deepEqual(problems(noFiles.replace("[[task_requirement]]", '[[known_file]]\nfile_path = "tests/test_due.py"\nfile_action = "create"\n\n[[task_requirement]]')), []);
});

test("checkBrief: task_requirement and acceptance_check: ids, texts, run_command, every requirement covered", () => {
  const noReq = FULL.replace(/\[\[task_requirement\]\][\s\S]*?(?=\[\[acceptance_check\]\])/, "").replace('covers_requirements = ["R1", "R2"]', "covers_requirements = []");
  assert.deepEqual(problems(noReq), [
    'The brief has no requirement: add a [[task_requirement]] with requirement_id = "R1" and requirement_text = "..." for each point of the request.',
    'Check C1 covers no requirement: write covers_requirements = ["R1"] with the requirement ids it checks.']);
  assert.ok(problems(FULL.replace('requirement_id = "R2"', 'requirement_id = "R1"')).includes(
    'The requirement_id "R1" is on more than one requirement: give each requirement its own id.'));
  assert.ok(problems(FULL.replace('requirement_id = "R2"\n', "")).includes(
    'Requirement number 2 has no requirement_id: give it requirement_id = "R2".'));
  assert.deepEqual(problems(FULL.replace('requirement_text = "notes list shows the date after the text."', 'requirement_text = ""')),
                   ["Requirement R2 has no requirement_text: say in one point what it must do."]);
  assert.deepEqual(problems(FULL.replace('covers_requirements = ["R1", "R2"]', 'covers_requirements = ["R1"]')),
                   ["Requirement R2 is in no check's covers_requirements: add it to a check, or add a check for it."]);
  assert.deepEqual(problems(FULL.replace('covers_requirements = ["R1", "R2"]', 'covers_requirements = ["R1", "R2", "R9"]')),
                   ['Check C1 covers "R9", but no requirement has that requirement_id: fix the id, or add the requirement.']);
  assert.deepEqual(problems(FULL.replace('run_command = "python -m pytest tests/test_model.py -q"\n', "")),
                   ['Check C1 has no run_command: give the command that checks it, for example run_command = "python -m pytest tests/test_x.py".']);
  assert.deepEqual(problems(FULL.replace('check_id = "C1"\n', "")), ['Check number 1 has no check_id: give it check_id = "C1".']);
  assert.deepEqual(problems(FULL.replace('expected_result = "all tests pass"\n', "")), []);   // expected_result: optional
  const noCheck = FULL.replace(/\[\[acceptance_check\]\][\s\S]*?(?=\[\[project_rule\]\])/, "");
  assert.deepEqual(problems(noCheck), ["The brief has no check: add a [[acceptance_check]] with check_id, covers_requirements, " +
                                       "run_command and expected_result, so that every requirement is in the covers_requirements of a check."]);
  const twice = FULL.replace("[[project_rule]]", '[[acceptance_check]]\ncheck_id = "C1"\ncovers_requirements = ["R1"]\nrun_command = "x"\n\n[[project_rule]]');
  assert.deepEqual(problems(twice), ['The check_id "C1" is on more than one check: give each check its own id.']);
});

test("checkBrief: [failed_attempt] only for bug_fix, with run_command and error_output; [[tried_fix]] only with it", () => {
  assert.deepEqual(problems(FIX), []);
  assert.deepEqual(problems(FIX.replace('run_command = "python -m pytest tests/test_model.py"\nerror_output', "error_output")),
                   ["[failed_attempt] has no run_command: give the command that fails."]);
  assert.deepEqual(problems(FIX.replace('error_output = """\nE   AssertionError\n"""', 'error_output = ""')),
                   ["[failed_attempt] has no error_output: copy the exact output of that command."]);
  assert.deepEqual(problems(FIX.replace('fix_result = "same error"\n', "")), ["Tried fix number 1 has no fix_result: say what happened."]);
  assert.deepEqual(problems(FIX.replace('fix_change = "parsed with strptime"\n', "")), ["Tried fix number 1 has no fix_change: say what was changed."]);
  const noAttempt = FIX.replace(/\[failed_attempt\][\s\S]*?(?=\[\[tried_fix\]\])/, "");
  assert.deepEqual(problems(noAttempt), ["[[tried_fix]] is only for a fix that already failed: add the [failed_attempt] with " +
                                         "run_command and error_output, or remove [[tried_fix]]."]);
  assert.deepEqual(problems(FIX.replace('"bug_fix"', '"new_feature"')), [
    '[failed_attempt] and [[tried_fix]] are only for work_type = "bug_fix" (a fix that already failed): set work_type = "bug_fix", or remove them.']);
  assert.deepEqual(problems(FIX.replace('"bug_fix"', '"follow_up"').replace(/\[\[tried_fix\]\][\s\S]*$/, "")), [
    '[failed_attempt] is only for work_type = "bug_fix" (a fix that already failed): set work_type = "bug_fix", or remove it.']);
  assert.deepEqual(problems(FIX.replace(/\[failed_attempt\][\s\S]*$/, "")), []);  // a bug_fix that did not fail before
});

test("checkBrief: keys that are not in the schema; revision 1's keys name revision 4's", () => {
  const v1 = `mode = "code"\ntests = "new"\ngoal = "g"\n[scope]\nout = []\n[[file]]\npath = "a.py"\naction = "create"\n` +
    `[[requirement]]\nid = "R1"\ntext = "t"\n[[check]]\nid = "A1"\ncovers = ["R1"]\nrun = "x"\n[[constraint]]\ntext = "c"\n` +
    `[[example]]\nsource = "s"\ntext = "t"\n[error]\nrun = "r"\noutput = "o"\n[[tried]]\nchange = "c"\nresult = "r"\n`;
  const p = problems(v1);
  assert.deepEqual(p.slice(0, 11), [
    'The brief has the key "mode", which is not in the schema: use "work_mode".',
    'The brief has the key "tests", which is not in the schema: use "work_type" and "existing_tests".',
    'The brief has the key "goal", which is not in the schema: use "task_summary".',
    'The brief has the key "scope", which is not in the schema: use "scope_limits".',
    'The brief has the key "file", which is not in the schema: use "known_file".',
    'The brief has the key "requirement", which is not in the schema: use "task_requirement".',
    'The brief has the key "check", which is not in the schema: use "acceptance_check".',
    'The brief has the key "constraint", which is not in the schema: use "project_rule".',
    'The brief has the key "example", which is not in the schema: use "input_example".',
    'The brief has the key "error", which is not in the schema: use "failed_attempt".',
    'The brief has the key "tried", which is not in the schema: use "tried_fix".']);
  assert.ok(p.includes('work_mode is missing: write work_mode = "code" (the hand-off writes or changes program code) or work_mode = "tests-only" (it writes or changes tests only).'));
  const plural = FULL.replaceAll("[[task_requirement]]", "[[task_requirements]]").replace('covers_requirements = ["R1", "R2"]', "covers_requirements = []");
  assert.equal(problems(plural)[0], 'The brief has the key "task_requirements", which is not in the schema: use "task_requirement".');
  assert.deepEqual(problems('notes = "x"\n' + FULL), [
    'The brief has the key "notes", which is not in the schema: remove it, or put its content in a key of the schema.']);
  // a key in a table that is not that table's: named with its table
  assert.deepEqual(problems(FULL.replace('file_path = "notes/store.py"', 'file_path = "notes/store.py"\nwhy = "context"')), [
    'The brief has the key "known_file.why", which is not in the schema: remove it, or put its content in a key of the schema.']);
  assert.ok(problems(FULL.replace('file_path = "notes/due.py"', 'path = "notes/due.py"')).includes(
    'The brief has the key "known_file.path", which is not in the schema: remove it, or put its content in a key of the schema.'));
});

test("briefRefusal: one sentence for a text that is no TOML; the reader's error; the problems as a list; the project folder", () => {
  assert.equal(B.briefRefusal(FULL), "");
  assert.equal(B.briefRefusal("Mode: code\nGoal: add it"),
               "[CARL] Brief refused: the coder takes its task only as a TOML brief, so write the task in the brief's " +
               "format from your instructions and send it again.");
  assert.match(B.briefRefusal('work_mode = "code"\ntask_summary = add it'),
               /^\[CARL\] Brief refused: the brief is not valid TOML \(line 2: .*\)\. Fix it and send the whole brief again\. Each item of a list is its own block/);
  const r = B.briefRefusal(FULL.replace('covers_requirements = ["R1", "R2"]', 'covers_requirements = ["R1"]'));
  assert.equal(r, "[CARL] Brief refused: the coder did not start. Fix these points and send the whole brief again:\n" +
                  "- Requirement R2 is in no check's covers_requirements: add it to a check, or add a check for it.");
  assert.match(B.briefRefusal(FULL, { root: mkdtempSync(join(tmpdir(), "empty-")) }),
               /- existing_tests has "tests\/test_store\.py", which is not in the project/);
  for (const p of B.checkBrief(brief('work_type = "x"\n[[known_file]]\nfile_action = "edit"\n[[task_requirement]]\n[[acceptance_check]]\n[failed_attempt]\n[[tried_fix]]\n'))) {
    assert.match(p, /^[A-Z[`a-z].*[.]$/, p);                                     // whole sentences
  }
});

// ------------------------------------------------------------------ the chain's helpers

test("the chain's helpers: files by action, ids, when a test session is due", () => {
  const b = brief();
  assert.deepEqual(B.filesByAction(b), { create: ["notes/due.py"], change: ["notes/model.py"], read: ["notes/store.py"] });
  assert.deepEqual(B.requirementIds(b), ["R1", "R2"]);
  assert.deepEqual(B.checkIds(b), ["C1"]);
  assert.equal(B.testsSetting(), "before");                                    // Phase 23.4.6 plugs in here
  assert.equal(B.testSessionDue(b), true);
  assert.equal(B.testSessionDue(b, true), false);                               // continues an earlier task
  assert.equal(B.testSessionDue({ ...b, workType: "follow_up" }), false);
  assert.equal(B.testSessionDue({ ...b, workType: "bug_fix" }), false);
  assert.equal(B.testSessionDue({ ...b, workMode: "tests-only" }), false);
  assert.equal(B.testSessionDue({ ...b, failedAttempt: { run: "x", output: "y" } }), false);
  assert.equal(B.testSessionDue({ ...b, triedFixes: [{ change: "a", result: "b" }] }), false);
  assert.equal(B.testSessionDue({ ...b, testSession: { files: [], summary: "", failing: [], notes: [] } }), false);
});

test("testSessionBrief: tests-only, without design_notes and known_file; the behaviour's keys stay", () => {
  const b = brief();
  const t = B.testSessionBrief(b);
  assert.equal(t.workMode, "tests-only");
  assert.equal(t.workType, "new_feature");
  assert.equal(t.designNotes, "");
  assert.deepEqual(t.files, []);
  assert.deepEqual(t.existingTests, []);
  for (const k of ["taskSummary", "expectedOutcome", "currentState", "exactInterfaces", "requirements", "checks",
                   "scopeLimits", "referenceDocs", "projectRules", "inputExamples"]) assert.deepEqual(t[k], b[k], k);
  assert.deepEqual(B.checkBrief(t), []);
  const text = B.writeBrief(t);
  assert.doesNotMatch(text, /design_notes|known_file|notes\/due\.py|The date is parsed/);
  assert.match(text, /^work_mode = "tests-only"\nwork_type = "new_feature"\n/);
  for (const key of ["task_summary", "expected_outcome", "current_state", "exact_interfaces", "scope_limits",
                     "[[reference_doc]]", "[[task_requirement]]", "[[acceptance_check]]", "[[project_rule]]", "[[input_example]]"]) {
    assert.ok(text.includes(key), key);
  }
});

test("writeBrief: TOML in the template's order that reads back to the same brief", () => {
  for (const b of [brief(), brief(FIX), B.testSessionBrief(brief())]) {
    const text = B.writeBrief(b);
    assert.deepEqual(brief(text), b, text);
  }
  const keys = B.writeBrief(brief(FIX)).split("\n").map((l) => /^(\[*[a-z_]+)/.exec(l)?.[1]).filter(Boolean);
  assert.deepEqual([...new Set(keys)], ["work_mode", "work_type", "existing_tests", "task_summary", "expected_outcome",
    "current_state", "design_notes", "exact_interfaces", "scope_limits", "[[reference_doc", "doc_path", "doc_purpose",
    "[[known_file", "file_path", "file_action", "[[task_requirement", "requirement_id", "requirement_text",
    "[[acceptance_check", "check_id", "covers_requirements", "run_command", "expected_result", "[[project_rule",
    "rule_text", "rule_source", "[[input_example", "example_source", "example_text", "[failed_attempt", "error_output",
    "[[tried_fix", "fix_change", "fix_result"]);
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

const REPORT = `outcome_summary = """
Tests for R1 and R2; R2 fails: the list has no date yet.
"""
brief_deviations = "The test file is tests/test_due.py, not in the brief."
task_status = "partly"

[[requirement_result]]
requirement_id = "R1"
requirement_status = "done"
requirement_note = "tests/test_due.py::test_r1_due_date"

[[check_result]]
check_id = "C1"
run_result = "FAIL"
run_summary = "1 failed, 1 passed in 0.03s"

[[changed_file]]
file_path = "./tests/test_due.py"
change_summary = "new"

[[test_finding]]
test_name = "tests/test_due.py::test_r2_list_shows_date"
requirement_id = "R2"
failure_reason = "notes list prints no date"

[[open_issue]]
issue_text = "R2 does not say the date format in the list."
`;
const REPORT_WANT = {
  status: "partly", outcomeSummary: "Tests for R1 and R2; R2 fails: the list has no date yet.",
  deviations: "The test file is tests/test_due.py, not in the brief.",
  requirements: [{ id: "R1", status: "done", note: "tests/test_due.py::test_r1_due_date" }],
  checks: [{ id: "C1", result: "fail", summary: "1 failed, 1 passed in 0.03s" }],
  files: [{ path: "tests/test_due.py", what: "new" }],
  findings: [{ test: "tests/test_due.py::test_r2_list_shows_date", requirement: "R2", why: "notes list prints no date" }],
  openIssues: ["R2 does not say the date format in the list."],
};

test("parseReport: the coder's TOML block, in a fence or plain, with a browser section after it", () => {
  assert.deepEqual(B.parseReport("```toml\n" + REPORT + "```\n\n## Needs a browser check\nNone."), REPORT_WANT);
  assert.deepEqual(B.parseReport("My report:\n\n" + REPORT + "\n## Needs a browser check\nStart it with python -m http.server 8765."), REPORT_WANT);
  assert.equal(B.parseReport("## Result\nDone."), null);
  assert.equal(B.parseReport('task_status = "done\n'), null);
  assert.equal(B.parseReport('outcome_summary = "no status"\n'), null);               // task_status is needed
  assert.equal(B.parseReport('status = "done"\nsummary = "revision 1"\n'), null);       // revision 1's report: not read
});

test("parseReport: the example report in coder.md reads, with every key", () => {
  const coder = readFileSync(join(REPO, "client/agents/coder.md"), "utf8");
  const r = B.parseReport(coder.slice(coder.indexOf("```toml")));
  assert.ok(r);
  assert.equal(r.status, "done");
  assert.ok(r.outcomeSummary && r.deviations);
  for (const k of ["requirements", "checks", "files", "findings", "openIssues"]) assert.equal(r[k].length, 1, k);
  assert.ok(r.requirements[0].id && r.requirements[0].status && r.requirements[0].note);
  assert.ok(r.checks[0].id && r.checks[0].result && r.checks[0].summary);
  assert.ok(r.files[0].path && r.files[0].what);
  assert.ok(r.findings[0].test && r.findings[0].requirement && r.findings[0].why);
});

// ------------------------------------------------------------------ the same brief as JSON (the reader stays)

/** FULL as JSON: the same keys. */
const FULL_JSON = {
  work_mode: "code", work_type: "new_feature", existing_tests: ["tests/test_store.py"],
  task_summary: "Notes are getting due dates, so the user can see what is late and what comes next.",
  expected_outcome: "A note can have a due date. `notes list` shows it after the text.",
  current_state: "notes/model.py has the Note dataclass; notes/store.py loads and saves notes.json.",
  design_notes: "The date is parsed and checked in the model; the CLI only formats.",
  exact_interfaces: ["notes add TEXT --due 2026-11-01", "a bad date is rejected with exit code 2"],
  scope_limits: "No API changes.",
  reference_doc: [{ doc_path: "docs/architecture.md", doc_purpose: "the ports and adapters layout" }],
  known_file: [{ file_path: "notes/due.py", file_action: "create" }, { file_path: "./notes/model.py", file_action: "change" },
               { file_path: "notes/store.py", file_action: "read" }],
  task_requirement: [{ requirement_id: "R1", requirement_text: "A note has an optional due date (YYYY-MM-DD)." },
                     { requirement_id: "R2", requirement_text: "notes list shows the date after the text." }],
  acceptance_check: [{ check_id: "C1", covers_requirements: ["R1", "R2"], run_command: "python -m pytest tests/test_model.py -q",
                       expected_result: "all tests pass" }],
  project_rule: [{ rule_text: "Functions have type hints.", rule_source: "AGENTS.md" }],
  input_example: [{ example_source: "notes.json", example_text: '\n[{"id": 1, "text": "buy milk", "done": false}]\n' }],
};
const JSON_TEXT = JSON.stringify(FULL_JSON, null, 2);

test("JSON: the reader; lenient where small models slip; errors name the line", () => {
  assert.deepEqual(B.parseJson('{"a": "x", "b": [1, 2.5, -3e2, true, false], "c": {"d": "\\u00e9\\n\\"q\\""}}'),
                   { a: "x", b: [1, 2.5, -300, true, false], c: { d: 'é\n"q"' } });
  assert.deepEqual(B.parseJson('{"a": ["x", "y",], "b": "one\ntwo", "c": "a \\d b",}'), { a: ["x", "y"], b: "one\ntwo", c: "a \\d b" });
  assert.deepEqual(B.parseJson('{"failed_attempt": null, "tried_fix": [null, {"fix_change": "a"}]}'), { tried_fix: [{ fix_change: "a" }] });
  const err = (text, line, re, o) => {
    assert.throws(() => B.parseJson(text, o), (e) => e instanceof B.JsonError && e.line === line && re.test(e.message), text);
  };
  err('{\n  "work_mode": "code",\n  "task_summary": Add it\n}', 3, /^line 3: this value has no quotes/);
  err('{\n  "work_mode": "code"\n  "task_summary": "x"\n}', 3, /needs a comma between its keys/);
  err('{"a": ["x" "y"]}', 1, /a list needs a comma/);
  err('{\n  work_mode: "code"\n}', 2, /a key needs quotes/);
  err('{"a" "x"}', 1, /a key needs : and a value/);
  err('{"a": "x",\n"a": "y"}', 2, /the key "a" is there twice/);
  err('{"a": "open\n}', 1, /no closing "/);
  err('{"a": {"b": 1}', 1, /no closing \}/);
  err('{"a": }', 1, /the value is missing/);
  err('{"a": 1} {"b": 2}', 1, /more text after the closing \}/);
  err('["a"]', 1, /not a JSON object: write it as \{ "work_mode": "code", \.\.\. \}/);
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
  assert.equal(B.parseBrief("```toml\n" + FULL + "```\n\n```json\n{\"work_mode\": \"x\"}\n```").format, "toml");   // the first fence
  for (const t of ["Mode: code\nGoal: add it", "Use {x} here.", '{"name": "not a brief"}']) assert.equal(B.parseBrief(t).format, "", t);
  const bad = B.parseBrief('Intro.\n\n```json\n{\n  "work_mode": "code",\n  "task_summary": oops\n}\n```');
  assert.equal(bad.brief, null);
  assert.equal(bad.format, "json");
  assert.match(bad.error, /^line 6: this value has no quotes/);                   // the line in the whole text
  const open = B.parseBrief('Intro.\n{\n  "work_mode": "code",\n  "task_summary": "x"\n');   // no closing }: the reader says so
  assert.match(open.error, /no closing \}/);
});

test("JSON: the check and the refusal name the keys the JSON way", () => {
  const json = (o) => B.checkBrief(B.parseBrief(JSON.stringify(o)).brief, "json");
  assert.deepEqual(json(FULL_JSON), []);
  const { work_mode, ...noMode } = FULL_JSON;
  assert.deepEqual(json(noMode), ['work_mode is missing: write "work_mode": "code" (the hand-off writes or changes program code) or "work_mode": "tests-only" (it writes or changes tests only).']);
  assert.deepEqual(json({ ...FULL_JSON, work_type: "some" }), ['"work_type": "some" is not valid: write "work_type": "new_feature", "follow_up" or "bug_fix".']);
  assert.deepEqual(json({ ...FULL_JSON, known_file: [{ file_path: "a.py", file_action: "read" }] }), [
    'The brief has no file to create or change: add an item to "known_file" with file_path and "file_action": "create" or "change" for a file you know the coder will write (the list does not have to be complete).']);
  assert.deepEqual(json({ ...FULL_JSON, acceptance_check: [{ check_id: "C1", covers_requirements: [], run_command: "" }] }), [
    'Check C1 has no run_command: give the command that checks it, for example "run_command": "python -m pytest tests/test_x.py".',
    'Check C1 covers no requirement: write "covers_requirements": ["R1"] with the requirement ids it checks.',
    "Requirement R1 is in no check's covers_requirements: add it to a check, or add a check for it.",
    "Requirement R2 is in no check's covers_requirements: add it to a check, or add a check for it."]);
  assert.deepEqual(json({ ...FULL_JSON, tried_fix: [{ fix_change: "a", fix_result: "b" }] }), [
    '"tried_fix" is only for "work_type": "bug_fix" (a fix that already failed): set "work_type": "bug_fix", or remove it.',
    '"tried_fix" is only for a fix that already failed: add the "failed_attempt" with run_command and error_output, or remove "tried_fix".']);
  assert.deepEqual(json({ ...FULL_JSON, task_requirements: FULL_JSON.task_requirement }), [
    'The brief has the key "task_requirements", which is not in the schema: use "task_requirement".']);
  assert.deepEqual(json({ ...noMode, mode: "code" }).slice(0, 1), [
    'The brief has the key "mode", which is not in the schema: use "work_mode".']);
  assert.equal(B.briefRefusal(JSON_TEXT), "");
  assert.match(B.briefRefusal('```json\n{"work_mode": "code", "task_summary": oops}\n```'),
               /^\[CARL\] Brief refused: the brief is not valid JSON \(line 2: this value has no quotes.*\)\. Fix it and send the whole brief again\.$/);
  assert.equal(B.briefRefusal("Mode: code\nGoal: add it", { format: "json" }),
               "[CARL] Brief refused: the coder takes its task only as a JSON brief, so write the task in the brief's " +
               "format from your instructions and send it again.");
  assert.match(B.briefRefusal("Mode: code"), /only as a TOML brief/);
});

test("writeBriefJson: JSON with the TOML's keys that reads back to the same brief", () => {
  const ts = { ...brief(), testSession: { files: ["tests/t.py"], summary: "two tests", failing: [{ test: "t", requirement: "R1", why: "w" }], notes: ["n"] } };
  for (const b of [brief(), brief(FIX), B.testSessionBrief(brief()), ts]) {
    const text = B.writeBriefJson(b);
    const r = B.parseBrief(text);
    assert.equal(r.format, "json");
    assert.deepEqual(r.brief, b, text);
    assert.deepEqual(B.parseBrief(B.writeBrief(b)).brief, r.brief);              // TOML and JSON: one brief
  }
  const o = JSON.parse(B.writeBriefJson(brief()));
  assert.deepEqual(Object.keys(o), ["work_mode", "work_type", "existing_tests", "task_summary", "expected_outcome",
    "current_state", "design_notes", "exact_interfaces", "scope_limits", "reference_doc", "known_file",
    "task_requirement", "acceptance_check", "project_rule", "input_example"]);
});

test("parseReport: the same report as JSON, in a fence or plain", () => {
  const raw = {
    outcome_summary: "Tests for R1 and R2; R2 fails: the list has no date yet.",
    brief_deviations: "The test file is tests/test_due.py, not in the brief.", task_status: "partly",
    requirement_result: [{ requirement_id: "R1", requirement_status: "done", requirement_note: "tests/test_due.py::test_r1_due_date" }],
    check_result: [{ check_id: "C1", run_result: "FAIL", run_summary: "1 failed, 1 passed in 0.03s" }],
    changed_file: [{ file_path: "./tests/test_due.py", change_summary: "new" }],
    test_finding: [{ test_name: "tests/test_due.py::test_r2_list_shows_date", requirement_id: "R2", failure_reason: "notes list prints no date" }],
    open_issue: [{ issue_text: "R2 does not say the date format in the list." }],
  };
  const text = JSON.stringify(raw, null, 2);
  assert.deepEqual(B.parseReport("```json\n" + text + "\n```\n\n## Needs a browser check\nNone."), REPORT_WANT);
  assert.deepEqual(B.parseReport("My report:\n" + text + "\nThat is all."), REPORT_WANT);
  assert.equal(B.parseReport('```json\n{"task_status": "done",\n```'), null);
});

test("1.13.2: a list of tables written as key = [ { ... } ] reads like [[key]] blocks, also with JSON-style \"key\": value", () => {
  const t = `work_mode = "code"
work_type = "new_feature"
task_summary = """Add a."""
expected_outcome = """a works."""
task_requirement = [
  {
    "requirement_id": "R1",
    "requirement_text": "Implement a."
  },
  {"requirement_id": "R2", "requirement_text": "Document a."}
]
acceptance_check = [ { check_id = "C1", covers_requirements = ["R1", "R2"], run_command = "python -m pytest" } ]

[[known_file]]
file_path = "a.py"
file_action = "create"
`;
  const p = B.parseBrief(t);
  assert.equal(p.error, "");
  assert.deepEqual(p.brief.requirements.map((r) => r.id), ["R1", "R2"]);
  assert.deepEqual(B.checkBrief(p.brief), []);
});

test("1.13.2: a refusal for broken TOML shows the [[table]] form of a list", () => {
  const said = B.briefRefusal('work_mode = "code"\ntask_requirement = [ { requirement_id "R1" } ]\n');
  assert.match(said, /not valid TOML/);
  assert.match(said, /Each item of a list is its own block, for example:\n\[\[task_requirement\]\]\nrequirement_id = "R1"/);
});
