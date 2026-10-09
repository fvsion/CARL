// client/shared/carl-brief-check.mjs: the brief check as a command (Phase 23.4.4), run with node from a folder laid
// out as the setup installs it (next to carl-brief.js): a file or the standard input, the format and the problems as
// sentences, the exit codes; --json for agent-bench. Run: node --test tests/js.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFileSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const REPO = fileURLToPath(new URL("../../", import.meta.url));
const DIR = mkdtempSync(join(tmpdir(), "carl-brief-check-"));
for (const f of ["carl-brief.js", "carl-brief-check.mjs"]) copyFileSync(join(REPO, "client/shared", f), join(DIR, f));
const CLI = join(DIR, "carl-brief-check.mjs");

/** The example brief of CARL's delegation rule (client/agents/delegation.md): a valid brief. */
const rule = readFileSync(join(REPO, "client/agents/delegation.md"), "utf8");
const VALID = rule.slice(rule.indexOf("```toml"), rule.indexOf("```", rule.indexOf("```toml") + 7) + 3);

const run = (args, input = "") => spawnSync(process.execPath, [CLI, ...args], { input, encoding: "utf8", cwd: DIR });

test("a valid brief: the format, the brief is valid, exit code 0 (a file, the standard input, -)", () => {
  writeFileSync(join(DIR, "ok.toml"), VALID);
  for (const r of [run(["ok.toml"]), run([], VALID), run(["-"], `Here is the task:\n\n${VALID}\n`)]) {
    assert.equal(r.status, 0, r.stderr);
    assert.equal(r.stdout, "Format: TOML\nThe brief is valid.\n");
  }
});

test("problems: each one a sentence, exit code 1; no brief at all: one sentence", () => {
  const noCheck = VALID.replace(/\[\[check\]\][\s\S]*?(?=\n```)/, "");
  let r = run([], noCheck);
  assert.equal(r.status, 1);
  assert.match(r.stdout, /^Format: TOML\nThe brief has \d+ problems?:\n- /);
  assert.match(r.stdout, /The brief has no check/);
  r = run([], 'mode = "code"\ngoal = oops\n');
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
  r = run(["--help"]);
  assert.equal(r.status, 0);
  assert.match(r.stdout, /^Usage: node carl-brief-check\.mjs \[FILE\]/);
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
