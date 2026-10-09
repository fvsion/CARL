// client/pi/extensions/subagent/index.ts (Phase 23.4.3, item 3): CARL's subagent tool runs a coder brief with
// tests = "new" as two fresh coder processes (the test session, then the code session) and gives one result. The
// extension is staged as the installer lays it out (index.ts, agents.ts, result.js, carl-brief.js, carl-chain.js),
// with small stand-ins for Pi's packages, and a fake `pi` program: a node script that acts as the coder (it writes
// files and prints Pi's JSON events). No model. Run: node --test tests/js.
import assert from "node:assert/strict";
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const REPO = fileURLToPath(new URL("../../", import.meta.url));
const root = mkdtempSync(join(tmpdir(), "carl-pi-chain-"));
const ext = join(root, "extensions", "subagent");
mkdirSync(ext, { recursive: true });
for (const f of ["index.ts", "agents.ts", "result.js"]) copyFileSync(join(REPO, "client/pi/extensions/subagent", f), join(ext, f));
for (const f of ["carl-brief.js", "carl-chain.js"]) copyFileSync(join(REPO, "client/shared", f), join(ext, f));

/** Stand-ins for Pi's packages: only what the extension uses when it loads and runs a task. */
const stubs = {
  "@earendil-works/pi-coding-agent": `
export const CONFIG_DIR_NAME = ".pi";
export const getAgentDir = () => process.env.PI_CODING_AGENT_DIR;
export const getMarkdownTheme = () => ({});
export const withFileMutationQueue = async (_p, f) => f();
export function parseFrontmatter(text) {
  const m = /^---\\n([\\s\\S]*?)\\n---\\n?([\\s\\S]*)$/.exec(text);
  const frontmatter = {};
  for (const line of (m ? m[1] : "").split("\\n")) {
    const k = line.indexOf(":");
    if (k > 0) frontmatter[line.slice(0, k).trim()] = line.slice(k + 1).trim().replace(/^"(.*)"$/, "$1");
  }
  return { frontmatter, body: m ? m[2] : text };
}`,
  "@earendil-works/pi-ai": "export const StringEnum = (values, o = {}) => ({ enum: values, ...o });",
  "@earendil-works/pi-tui": `
export class Text { constructor(t) { this.t = t; } }
export class Box { addChild() {} }
export class Container { addChild() {} }
export class Markdown {}
export class Spacer {}`,
  typebox: `
export const Type = {
  Object: (properties, o = {}) => ({ type: "object", properties, ...o }),
  String: (o = {}) => ({ type: "string", ...o }),
  Boolean: (o = {}) => ({ type: "boolean", ...o }),
  Optional: (s) => ({ ...s, optional: true }),
  Array: (items, o = {}) => ({ type: "array", items, ...o }),
};`,
};
for (const [name, code] of Object.entries(stubs)) {
  const d = join(root, "node_modules", name);
  mkdirSync(d, { recursive: true });
  writeFileSync(join(d, "package.json"), JSON.stringify({ name, type: "module", main: "index.js" }));
  writeFileSync(join(d, "index.js"), code);
}

const agentDir = join(root, "agent");
mkdirSync(join(agentDir, "agents"), { recursive: true });
writeFileSync(join(agentDir, "agents", "coder.md"), "---\nname: coder\ndescription: the coder\n---\nYou are **coder**.\n");
writeFileSync(join(agentDir, "agents", "scout.md"), "---\nname: scout\ndescription: reads code\n---\nYou read.\n");
process.env.PI_CODING_AGENT_DIR = agentDir;

// The fake pi: the last argument is "Task: <brief>". Mode test writes tests/check.test.mjs, a node --test file
// (FAKE_PI: red = it fails before the code, green = it passes, failtest = the session fails); mode code writes
// src/feature.txt (tamper: it also changes the test). Each run is logged to FAKE_PI_LOG.
const RED = 'import { existsSync } from "node:fs";\nimport { test } from "node:test";\nimport assert from "node:assert";\ntest("R1", () => assert.ok(existsSync("src/feature.txt")));\n';
const GREEN = 'import { test } from "node:test";\ntest("R1", () => {});\n';
const fakePi = join(root, "fake-pi.mjs");
writeFileSync(fakePi, `
import { appendFileSync, mkdirSync, writeFileSync } from "node:fs";
const RED = ${JSON.stringify(RED)};
const GREEN = ${JSON.stringify(GREEN)};
const task = process.argv[process.argv.length - 1].replace(/^Task: /, "");
const mode = /^mode = "test"/m.test(task) ? "test" : /^mode = "code"/m.test(task) ? "code" : "other";
const how = process.env.FAKE_PI || "red";
appendFileSync(process.env.FAKE_PI_LOG, JSON.stringify({ agent: process.env.CARL_AGENT, mode, task }) + "\\n");
let text = "Done.";
if (mode === "test") {
  if (how === "failtest") { process.stderr.write("the server closed the connection"); process.exit(1); }
  mkdirSync("tests", { recursive: true });
  writeFileSync("tests/check.test.mjs", how === "green" ? GREEN : RED);
  text = 'status = "done"\\nmode = "test"\\nsummary = "One test for R1."';
} else if (mode === "code") {
  mkdirSync("src", { recursive: true });
  writeFileSync("src/feature.txt", "ok\\n");
  if (how === "tamper") writeFileSync("tests/check.test.mjs", GREEN);
  text = 'status = "done"\\nmode = "code"\\nsummary = "Wrote src/feature.txt."';
}
const message = { role: "assistant", content: [{ type: "text", text }], stopReason: "stop",
                  usage: { input: 10, output: 5, cacheRead: 0, cacheWrite: 0, cost: { total: 0 }, totalTokens: 15 } };
process.stdout.write(JSON.stringify({ type: "message_end", message }) + "\\n");
`);
process.argv[1] = fakePi;                                // the subagent tool starts "node <argv[1]> ..." as pi

const ext_ = (await import(pathToFileURL(join(ext, "index.ts")).href)).default;
let tool;
const sent = [];
let onSent = () => {};
ext_({
  registerMessageRenderer() {}, on() {}, registerCommand() {},
  registerTool(def) { tool = def; },
  sendMessage(msg) { sent.push(msg); onSent(); },
});

const BRIEF = (tests = "new") => `mode = "code"
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
run = "node --test tests/check.test.mjs"
expect = "exit 0"
`;

/** A fresh project, a fresh log, the fake pi's behaviour; then the tool's execute. */
async function call(params, how = "red", prepare = () => {}) {
  const cwd = mkdtempSync(join(tmpdir(), "carl-pi-chain-proj-"));
  prepare(cwd);
  process.env.FAKE_PI = how;
  process.env.FAKE_PI_LOG = join(cwd, "..", `log-${Date.now()}-${Math.random()}.jsonl`);
  writeFileSync(process.env.FAKE_PI_LOG, "");
  const ctx = { cwd, hasUI: false, isProjectTrusted: () => true, ui: { setStatus() {}, notify() {} } };
  const out = await tool.execute("call-1", params, undefined, undefined, ctx);
  const log = () => readFileSync(process.env.FAKE_PI_LOG, "utf8").trim().split("\n").filter(Boolean).map((l) => JSON.parse(l));
  return { out, cwd, log, text: out.content[0].text };
}

test("Pi: a coder brief with tests = new: the test session, then the code session, fresh processes; one result", async () => {
  const { out, cwd, log, text } = await call({ agent: "coder", task: BRIEF(), background: false });
  const runs = log();
  assert.deepEqual(runs.map((r) => [r.agent, r.mode]), [["coder", "test"], ["coder", "code"]]);
  assert.match(runs[1].task, /^\[test_session\]\nfiles = \["tests\/check\.test\.mjs"\]\nsummary = "One test for R1\."$/m);
  assert.ok(existsSync(join(cwd, "src", "feature.txt")));
  assert.ok(!out.isError);
  assert.match(text, /^\[CARL\] Chain: the coder ran in two new sessions/);
  assert.match(text, /Red start: CARL ran `node --test tests\/check\.test\.mjs` before the code: it failed \(exit 1\)\./);
  assert.match(text, /Tests unchanged after the code session \(tests\/check\.test\.mjs\)\./);
  assert.match(text, /## The test session's report \(mode test\)\n\nstatus = "done"\nmode = "test"/);
  assert.match(text, /## The code session's report \(mode code\)\n\nstatus = "done"\nmode = "code"/);
  assert.equal(out.details.results.length, 1);
  assert.equal(out.details.results[0].usage.turns, 2);                      // the two sessions' usage, summed
});

test("Pi: no red start warns; a changed test is named; a failed test session stops the chain", async () => {
  const green = await call({ agent: "coder", task: BRIEF(), background: false }, "green");
  assert.deepEqual(green.log().map((r) => r.mode), ["test", "code"]);
  assert.match(green.text, /\[CARL\] Warning: no new test failed before the code \(no red start\)/);
  const tamper = await call({ agent: "coder", task: BRIEF(), background: false }, "tamper");
  assert.match(tamper.text, /\[CARL\] Warning: the code session changed these test files after the test session: tests\/check\.test\.mjs\./);
  const fail = await call({ agent: "coder", task: BRIEF(), background: false }, "failtest");
  assert.deepEqual(fail.log().map((r) => r.mode), ["test"]);
  assert.equal(fail.out.isError, true);
  assert.match(fail.text, /\[CARL\] Chain: the test session failed, so the code session did not run\./);
  assert.match(fail.text, /the server closed the connection/);
});

test("Pi: in the background, the two sessions run as one job and send one message", async () => {
  sent.length = 0;
  const done = new Promise((r) => (onSent = r));
  const { text, log } = await call({ agent: "coder", task: BRIEF() });           // the coder: background by default
  assert.match(text, /^Started in the background: bg-\d+ \(coder\)\. CARL runs the coder in two sessions, one after the other: first the tests, then the code\. You get one result for both\./);
  await done;
  assert.equal(sent.length, 1);
  assert.match(sent[0].content, /^<subagent id="bg-\d+" agent="coder" state="done" took="[^"]+">\n\[CARL\] Chain:/);
  assert.match(sent[0].content, /Tests unchanged after the code session/);
  assert.deepEqual(log().map((r) => r.mode), ["test", "code"]);
});

test("Pi: not due, one process as before; tests = existing adds the test-file check; a chain step chains too", async () => {
  const none = await call({ agent: "coder", task: BRIEF("none"), background: false });
  assert.deepEqual(none.log().map((r) => r.mode), ["code"]);
  assert.equal(none.text, 'status = "done"\nmode = "code"\nsummary = "Wrote src/feature.txt."');
  const scout = await call({ agent: "scout", task: BRIEF(), background: false });
  assert.deepEqual(scout.log().map((r) => [r.agent, r.mode]), [["scout", "code"]]);  // another agent: as it is
  const existing = await call({ agent: "coder", task: BRIEF("existing"), background: false }, "red", (cwd) => {
    mkdirSync(join(cwd, "tests"));
    writeFileSync(join(cwd, "tests", "check.test.mjs"), RED);
  });
  assert.deepEqual(existing.log().map((r) => r.mode), ["code"]);
  assert.match(existing.text, /\n\n\[CARL\] Tests unchanged: tests\/check\.test\.mjs\.$/);
  const chain = await call({ chain: [{ agent: "scout", task: "Look." }, { agent: "coder", task: BRIEF() }] });
  assert.deepEqual(chain.log().map((r) => [r.agent, r.mode]), [["scout", "other"], ["coder", "test"], ["coder", "code"]]);
  assert.match(chain.text, /^\[CARL\] Chain:/);
});

test("Pi: parallel tasks refuse a brief that starts the chain, with one sentence; one task alone runs it", async () => {
  const two = await call({ tasks: [{ agent: "coder", task: BRIEF() }, { agent: "scout", task: "Look." }] });
  assert.equal(two.out.isError, true);
  assert.match(two.text, /^A coder brief with mode = "code" and tests = "new" runs as two sessions, one after the other/);
  assert.deepEqual(two.log(), []);
  const one = await call({ tasks: [{ agent: "coder", task: BRIEF() }], background: false });
  assert.deepEqual(one.log().map((r) => r.mode), ["test", "code"]);
  assert.match(one.text, /\[CARL\] Chain:/);
});
