// client/pi/extensions/carl-delegation/index.ts (Phase 23.4.3): the brief check in the main session and the coder's
// gates in its own process, against a fake Pi API. Staged as the installer lays it out (index.ts next to the shared
// carl-delegation.js and carl-brief.js); node strips the types. Run: node --test tests/js.
import assert from "node:assert/strict";
import { copyFileSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const REPO = fileURLToPath(new URL("../../", import.meta.url));
const dir = mkdtempSync(join(tmpdir(), "carl-delegation-pi-"));
copyFileSync(join(REPO, "client/pi/extensions/carl-delegation/index.ts"), join(dir, "index.ts"));
for (const f of ["carl-delegation.js", "carl-brief.js"]) copyFileSync(join(REPO, "client/shared", f), join(dir, f));
const agentDir = mkdtempSync(join(tmpdir(), "carl-pi-agent-"));     // carl.json: none at first (the defaults)
process.env.PI_CODING_AGENT_DIR = agentDir;
process.env.HOME = mkdtempSync(join(tmpdir(), "carl-pi-home-"));     // no dashboard setting: the gate is off

let ext;
let why = "";
try {
  ext = (await import(pathToFileURL(join(dir, "index.ts")).href)).default;
} catch (e) {
  why = String(e?.code ?? e);
  if (!/UNKNOWN_FILE_EXTENSION|ERR_MODULE_NOT_FOUND|Cannot find package/.test(why)) throw e;
}

/** A fake Pi: the handlers by event; fire() calls them in order and returns the last result. */
function fakePi() {
  const on = {};
  return {
    pi: { on: (name, f) => (on[name] ??= []).push(f) },
    fire: async (name, event, ctx = { cwd: "/p" }) => {
      let out;
      for (const f of on[name] ?? []) out = await f(event, ctx);
      return out;
    },
    on,
  };
}

const BRIEF = `work_mode = "code"
work_type = "follow_up"
task_summary = "Write the header row of the CSV export."
expected_outcome = "The CSV starts with the header month,orders,total."
current_state = "report/csv_export.py writes the rows, with no header."

[[known_file]]
file_path = "report/csv_export.py"
file_action = "change"

[[known_file]]
file_path = "report/model.py"
file_action = "read"

[[task_requirement]]
requirement_id = "R1"
requirement_text = "the first row is month,orders,total"

[[acceptance_check]]
check_id = "C1"
covers_requirements = ["R1"]
run_command = "python -m report --csv /tmp/r.csv data/orders.csv"
`;

test("Pi, the main session: a coder task with a gap is blocked with what to fix; a complete brief runs", async (t) => {
  if (!ext) return t.skip(`needs type stripping: ${why}`);
  delete process.env.CARL_AGENT;
  const { pi, fire } = fakePi();
  ext(pi);
  const call = (input) => fire("tool_call", { toolName: "subagent", input });
  assert.equal(await call({ agent: "coder", task: BRIEF, background: true }), undefined);
  const r = await call({ agent: "coder", task: "Mode: code\nGoal: add the header" });
  assert.equal(r.block, true);
  assert.match(r.reason, /^\[CARL\] Brief refused: the coder takes its task only as a TOML brief/);
  const gap = await call({ chain: [{ agent: "coder", task: BRIEF.replace('check_id = "C1"\n', "") }] });
  assert.match(gap.reason, /^\[CARL\] Brief refused: the coder did not start\.[\s\S]*Check number 1 has no check_id/);
  const tests = BRIEF.replace('work_type = "follow_up"', 'work_type = "follow_up"\nexisting_tests = ["tests/test_gone.py"]');
  assert.match((await call({ agent: "coder", task: tests })).reason, /existing_tests has "tests\/test_gone\.py", which is not in the project/);
  assert.equal(await call({ agent: "browser", task: "open the page" }), undefined);
  assert.equal(await fire("tool_call", { toolName: "write", input: { path: "notes.md" } }), undefined);
  writeFileSync(join(agentDir, "carl.json"), JSON.stringify({ delegation: { reminder: true, brief: false } }));
  const off = fakePi();
  ext(off.pi);
  assert.equal(await off.fire("tool_call", { toolName: "subagent", input: { agent: "coder", task: "Mode: code" } }), undefined);
  writeFileSync(join(agentDir, "carl.json"), "{}");
});

test("Pi, the coder's process: its prompt gives the brief; writes against its work_mode or of a read file are blocked", async (t) => {
  if (!ext) return t.skip(`needs type stripping: ${why}`);
  process.env.CARL_AGENT = "coder";
  try {
    const { pi, fire, on } = fakePi();
    ext(pi);
    assert.equal(on.input, undefined);                                         // a subagent: no reminder
    const write = (toolName, input) => fire("tool_call", { toolName, input });
    assert.equal(await write("write", { path: "x.py" }), undefined);          // no brief yet: nothing to keep to
    await fire("before_agent_start", { prompt: `Task: ${BRIEF}` });
    assert.equal(await write("edit", { path: "report/csv_export.py" }), undefined);
    assert.equal(await write("write", { path: "report/new.py" }), undefined);   // a new module file: the list is a start
    const out = await write("edit", { path: "report/model.py" });
    assert.equal(out.block, true);
    assert.match(out.reason, /^\[CARL\] Blocked: report\/model\.py is in your brief to read only/);
    assert.match((await write("bash", { command: "cat > tests/test_x.py <<'EOF'\nx\nEOF" })).reason, /is a test file/);
    assert.equal(await write("bash", { command: "python -m pytest -q" }), undefined);
    assert.equal(await fire("tool_call", { toolName: "subagent", input: { agent: "coder", task: "x" } }), undefined);
  } finally {
    delete process.env.CARL_AGENT;
  }
  process.env.CARL_AGENT = "browser";                                          // another subagent: nothing
  try {
    const { pi, on } = fakePi();
    ext(pi);
    assert.deepEqual(Object.keys(on), []);
  } finally {
    delete process.env.CARL_AGENT;
  }
});
