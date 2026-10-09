// client/shared/carl-brief-check.mjs: the brief check as a command (Phase 23.4.4), run with node from a folder laid
// out as the setup installs it (next to carl-brief.js): a file or the standard input, the format and the problems as
// sentences, the exit codes; --json for agent-bench. Run: node --test tests/js.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const REPO = fileURLToPath(new URL("../../", import.meta.url));
const DIR = mkdtempSync(join(tmpdir(), "carl-brief-check-"));
for (const f of ["carl-brief.js", "carl-brief-check.mjs"]) copyFileSync(join(REPO, "client/shared", f), join(DIR, f));
const CLI = join(DIR, "carl-brief-check.mjs");

/** The template of CARL's delegation rule (client/agents/delegation.md, revision 4), as written (its bug-fix tables
 * commented out): a valid brief. */
const rule = readFileSync(join(REPO, "client/agents/delegation.md"), "utf8");
const TEMPLATE = rule.slice(rule.indexOf("```toml"), rule.indexOf("```", rule.indexOf("```toml") + 7) + 3);
const VALID = TEMPLATE;
/** The template with its bug-fix tables uncommented. */
const UNCOMMENTED = TEMPLATE.replace(/^# For work_type = "bug_fix" only.*\n/m, "").replace(/^# /gm, "");

const run = (args, input = "") => spawnSync(process.execPath, [CLI, ...args], { input, encoding: "utf8", cwd: DIR });

test("a valid brief: the format, the brief is valid, exit code 0 (a file, the standard input, -)", () => {
  writeFileSync(join(DIR, "ok.toml"), VALID);
  for (const r of [run(["ok.toml"]), run([], VALID), run(["-"], `Here is the task:\n\n${VALID}\n`)]) {
    assert.equal(r.status, 0, r.stderr);
    assert.equal(r.stdout, "Format: TOML\nThe brief is valid.\n");
  }
});

test("problems: each one a sentence, exit code 1; no brief at all: one sentence", () => {
  const noCheck = VALID.replace(/\[\[acceptance_check\]\][\s\S]*?(?=\[\[project_rule\]\])/, "");
  let r = run([], noCheck);
  assert.equal(r.status, 1);
  assert.match(r.stdout, /^Format: TOML\nThe brief has \d+ problems?:\n- /);
  assert.match(r.stdout, /The brief has no check: add a \[\[acceptance_check\]\]/);
  assert.equal(run([], UNCOMMENTED.replace('"new_feature"', '"bug_fix"')).status, 0);   // the bug-fix tables: bug_fix
  r = run([], UNCOMMENTED);                                                      // ... not with new_feature
  assert.deepEqual([r.status, r.stdout], [1, 'Format: TOML\nThe brief has 1 problem:\n- [failed_attempt] and [[tried_fix]] ' +
    'are only for work_type = "bug_fix" (a fix that already failed): set work_type = "bug_fix", or remove them.\n']);
  r = run([], 'mode = "code"\ngoal = "g"\n[[file]]\npath = "a.py"\naction = "create"\n');   // revision 1: the new keys
  assert.equal(r.status, 1);
  assert.match(r.stdout, /- The brief has the key "mode", which is not in the schema: use "work_mode"\.\n/);
  assert.match(r.stdout, /- The brief has the key "goal", which is not in the schema: use "task_summary"\.\n/);
  assert.match(r.stdout, /- The brief has the key "file", which is not in the schema: use "known_file"\.\n/);
  r = run([], 'work_mode = "code"\ntask_summary = oops\n');
  assert.equal(r.status, 1);
  assert.match(r.stdout, /^Format: TOML\nThe brief has 1 problem:\n- line 2: this value has no quotes/);
  r = run([], "Goal: add it\nFiles: a.py\n");
  assert.deepEqual([r.status, r.stdout], [1, "Format: none (Key: value lines)\nThe text has no TOML or JSON brief.\n"]);
  r = run([], "Please add it.");
  assert.deepEqual([r.status, r.stdout], [1, "Format: none\nThe text has no TOML or JSON brief.\n"]);
});

test("a wrong command or an input that cannot be read: exit code 2; --help: 0", () => {
  let r = run(["nope.toml"]);
  assert.equal(r.status, 2);
  assert.match(r.stderr, /cannot read nope\.toml/);
  assert.equal(run(["a", "b"]).status, 2);
  assert.equal(run(["--bad"]).status, 2);
  assert.equal(run(["--json"], "{}").status, 2);
  assert.equal(run(["--root"]).status, 2);
  assert.equal(run(["--root", "--json"]).status, 2);
  r = run(["--help"]);
  assert.equal(r.status, 0);
  assert.match(r.stdout, /^Usage: node carl-brief-check\.mjs \[--root DIR\] \[FILE\]/);
});

test("--root DIR: each existing_tests path must be in the project DIR; without --root it is not checked", () => {
  const proj = mkdtempSync(join(tmpdir(), "carl-brief-root-"));
  writeFileSync(join(DIR, "brief.toml"), VALID);                               // existing_tests = ["tests/test_store.py"]
  assert.equal(run(["brief.toml"]).status, 0);
  let r = run(["--root", proj, "brief.toml"]);
  assert.deepEqual([r.status, r.stdout], [1, 'Format: TOML\nThe brief has 1 problem:\n- existing_tests has "tests/test_store.py", ' +
    "which is not in the project: give the path of a test file that exists, or remove it.\n"]);
  mkdirSync(join(proj, "tests"));
  writeFileSync(join(proj, "tests", "test_store.py"), "");
  assert.equal(run(["--root", proj, "brief.toml"]).status, 0);
  assert.equal(run(["brief.toml", "--root", proj]).status, 0);
  r = run(["--root", join(proj, "nowhere"), "--json"], JSON.stringify([VALID]));
  assert.equal(r.status, 0);
  assert.equal(JSON.parse(r.stdout)[0].valid, false);
});

test("--json: a list of texts in, a list of { format, valid, problems } out (agent-bench)", () => {
  const r = run(["--json"], JSON.stringify([VALID, "Goal: x\nFiles: y\n", "x"]));
  assert.equal(r.status, 0, r.stderr);
  const out = JSON.parse(r.stdout);
  assert.deepEqual(out.map((o) => [o.format, o.valid]), [["toml", true], ["kv", false], ["other", false]]);
  assert.deepEqual(out[0].problems, []);
});

test("imported, it runs nothing (agent-bench's brief_check.mjs calls its main)", async () => {
  const m = await import(new URL(`file://${CLI}`).href);
  assert.equal(typeof m.main, "function");
  assert.deepEqual(m.checkText("x"), { format: "other", valid: false, problems: ["The text has no TOML or JSON brief."] });
  const said = [];
  assert.equal(m.main([], { out: (s) => said.push(s), input: () => VALID }), 0);
  assert.deepEqual(said, ["Format: TOML\n", "The brief is valid.\n"]);
});
