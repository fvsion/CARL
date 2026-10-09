// The OpenCode server plugins: carl-background, carl-model-check (check.js) and carl-cache (its hooks and
// its fetch wrapper, staged with the shared files as the installer lays them out, against a fake server).
// Run: node --test tests/js (tests/scripts/test_js.py runs it with the other suites).
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import background from "../../client/opencode/plugins/carl-background/index.js";
import { parseModels, verdict } from "../../client/opencode/plugins/carl-model-check/check.js";

const REPO = fileURLToPath(new URL("../../", import.meta.url));
const HOME = mkdtempSync(join(tmpdir(), "carl-plugins-"));
process.env.HOME = HOME;                     // carl-panel.js and carl-cache.js read ~/.config/carl: an empty one

test("carl-background: CARL's coder goes to the background unless the call says", async () => {
  const hooks = await background.server();
  const call = async (args, tool = "task") => {
    const output = { args: { ...args } };
    await hooks["tool.execute.before"]({ tool }, output);
    return output.args.background;
  };
  assert.equal(await call({ subagent_type: "coder" }), true);
  assert.equal(await call({ subagent_type: "carl-coder" }), true);
  assert.equal(await call({ subagent_type: "coder", background: false }), false);
  assert.equal(await call({ subagent_type: "coder", task_id: "t1" }), undefined);   // continues an earlier task
  assert.equal(await call({ subagent_type: "explore" }), undefined);
  assert.equal(await call({ subagent_type: "coder" }, "bash"), undefined);
});

/** carl-delegation staged as the installer lays it out (its index.js next to the shared carl-delegation.js,
 * carl-brief.js and carl-chain.js). */
const delegationDir = mkdtempSync(join(tmpdir(), "carl-delegation-"));
copyFileSync(join(REPO, "client/opencode/plugins/carl-delegation/index.js"), join(delegationDir, "index.js"));
copyFileSync(join(REPO, "client/shared/carl-delegation.js"), join(delegationDir, "carl-delegation.js"));
copyFileSync(join(REPO, "client/shared/carl-brief.js"), join(delegationDir, "carl-brief.js"));
copyFileSync(join(REPO, "client/shared/carl-chain.js"), join(delegationDir, "carl-chain.js"));
const delegationMod = await import(pathToFileURL(join(delegationDir, "index.js")).href);
const { RULE_BEGIN, RULE_END, withoutRule } = delegationMod;
const delegation = delegationMod.default;
const chainMod = await import(pathToFileURL(join(delegationDir, "carl-chain.js")).href);
const CHAINS = join(HOME, ".config", "carl", "chains");                  // the held chains' state files

test("carl-delegation: subagents never get the delegation rule; the main agent keeps it", async () => {
  const rule = `${RULE_BEGIN}\n## Delegating to the coder subagent\nDelegate large work.\n${RULE_END}`;
  const sys = `You are opencode.\n\nInstructions from: /x/delegation.md\n${rule}\n\nInstructions from: AGENTS.md\nUse tabs.`;
  assert.equal(withoutRule(sys), "You are opencode.\n\nInstructions from: /x/delegation.md\n\nInstructions from: AGENTS.md\nUse tabs.");
  assert.equal(withoutRule(`a\n${rule}\nb\n${rule}`), "a\n\nb");
  assert.equal(withoutRule(`keep\n${RULE_BEGIN}\nhalf a rule`), "keep");          // never half of it
  assert.equal(withoutRule("no rule here"), "no rule here");
  const asked = [];
  const client = { session: { get: async ({ path }) => (asked.push(path.id), { data: { parentID: path.id === "c2" ? "m" : undefined } }) } };
  const hooks = await delegation.server({ client }, { provider: "llamacpp" });
  const run = async (sessionID) => {
    const system = [sys, "other"];
    const output = { system };
    await hooks["experimental.chat.system.transform"]({ sessionID }, output);
    assert.equal(output.system, system);                                   // the same array: OpenCode reads its own
    return system[0];
  };
  await hooks.event({ event: { type: "session.created", properties: { info: { id: "c1", parentID: "m" } } } });
  await hooks.event({ event: { type: "session.created", properties: { info: { id: "m" } } } });
  assert.ok(!(await run("c1")).includes("Delegating"));                    // a subagent seen in an event
  assert.ok((await run("m")).includes("Delegating"));                      // the main agent keeps it
  assert.ok(!(await run("c2")).includes("Delegating"));                    // a subagent asked from OpenCode
  assert.ok((await run("u")).includes("Delegating"));                      // unknown: kept (the main agent's case)
  assert.deepEqual(asked, ["c2", "u"]);
  await run("c2");
  assert.deepEqual(asked, ["c2", "u"]);                                     // asked once a session
  const none = await delegation.server({}, {});                            // no client: kept, no error
  const out = { system: [sys] };
  await none["experimental.chat.system.transform"]({ sessionID: "z" }, out);
  assert.equal(out.system[0], sys);
});

test("carl-delegation: the reminder on main sessions only; off with reminder: false", async () => {
  const client = { session: { get: async ({ path }) => ({ data: { parentID: path.id === "sub" ? "m" : undefined } }) } };
  const msgs = (sid) => ({ messages: [
    { info: { role: "user", sessionID: sid }, parts: [{ type: "text", text: "Add a CLI." }, { type: "text", text: "x", synthetic: true }] },
    { info: { role: "assistant", sessionID: sid }, parts: [{ type: "text", text: "OK" }] }] });
  const on = await delegation.server({ client }, { coder: "carl-coder" });
  const main = msgs("main");
  await on["experimental.chat.messages.transform"]({}, main);
  assert.match(main.messages[0].parts[0].text, /^Add a CLI\.\n\n\[CARL reminder\] .*subagent_type "carl-coder"/);
  assert.equal(main.messages[0].parts[1].text, "x");                       // a synthetic part stays as it is
  assert.equal(main.messages[1].parts[0].text, "OK");
  await on["experimental.chat.messages.transform"]({}, main);               // the same line once (cache-safe)
  assert.equal(main.messages[0].parts[0].text.split("[CARL reminder]").length, 2);
  const sub = msgs("sub");
  await on["experimental.chat.messages.transform"]({}, sub);
  assert.equal(sub.messages[0].parts[0].text, "Add a CLI.");                // a subagent: no reminder
  const off = await delegation.server({ client }, { reminder: false });
  const quiet = msgs("main");
  await off["experimental.chat.messages.transform"]({}, quiet);
  assert.equal(quiet.messages[0].parts[0].text, "Add a CLI.");
});

test("carl-delegation: the gate is off unless the dashboard sets it; then it stops the main agent at the Nth new file", async () => {
  const client = { session: { get: async () => ({ data: {} }) } };
  const dir = mkdtempSync(join(tmpdir(), "gate-"));
  const call = async (hooks, tool, args, sessionID = "m") => {
    try {
      await hooks["tool.execute.before"]({ tool, sessionID }, { args });
      return "";
    } catch (e) {
      return String(e.message);
    }
  };
  const home = mkdtempSync(join(tmpdir(), "gate-home-"));                   // the dashboard's setting (Phase 23.4)
  const was = process.env.HOME;
  process.env.HOME = home;
  const none = await delegation.server({ client, directory: dir }, {});
  assert.equal(await call(none, "write", { filePath: "a.py" }), "");        // off by default
  mkdirSync(join(home, ".config", "carl"), { recursive: true });
  writeFileSync(join(home, ".config", "carl", "config.json"), JSON.stringify({ delegation: { gate: 2 } }));
  const two = await delegation.server({ client, directory: dir }, { gate: 9 });   // the old option: ignored
  process.env.HOME = was;
  assert.equal(await call(two, "write", { filePath: "a.py" }), "");         // the 1st new file passes
  assert.match(await call(two, "write", { filePath: "b.py" }), /^\[CARL\] Blocked: b\.py is a new file/);
  await two["chat.message"]({ sessionID: "m" });                             // a new turn counts again
  assert.equal(await call(two, "write", { filePath: "c.py" }), "");
  assert.equal(await call(two, "task", { subagent_type: "coder", task_id: "t1" }), "");    // (no brief check: continued)
  assert.equal(await call(two, "write", { filePath: "d.py" }), "");         // after the coder: no gate
});

const BRIEF = `mode = "code"
tests = "existing"
goal = "Write the header row of the CSV export."

[scope]
out = [{ text = "the text table", why = "unchanged" }]

[[file]]
path = "report/csv_export.py"
action = "change"

[[requirement]]
id = "R1"
text = "the first row is month,orders,total"

[[check]]
id = "A1"
covers = ["R1"]
run = "python -m pytest tests/test_csv_export.py -q"
`;

/** A tool call through the plugin's tool.execute.before: "" when it runs, else the error it throws. */
const before = async (hooks, tool, args, sessionID = "m") => {
  try {
    await hooks["tool.execute.before"]({ tool, sessionID, callID: "c" }, { args });
    return "";
  } catch (e) {
    return String(e.message);
  }
};

test("carl-delegation: a coder task whose brief fails the check is refused before the coder starts", async () => {
  const client = { session: { get: async () => ({ data: {} }) } };
  const hooks = await delegation.server({ client, directory: "/p" }, { coder: "coder" });
  assert.equal(await before(hooks, "task", { subagent_type: "coder", prompt: "Here it is:\n```toml\n" + BRIEF + "```" }), "");
  assert.match(await before(hooks, "task", { subagent_type: "coder", prompt: "Mode: code\nGoal: add the header" }),
               /^\[CARL\] Brief refused: the coder takes its task only as a TOML brief, .*send it again\.$/);
  const gap = await before(hooks, "task", { subagent_type: "carl-coder", prompt: BRIEF.replace(/\[scope\]\nout = .*\n/, "") });
  assert.match(gap, /^\[CARL\] Brief refused: the coder did not start\. Fix these points .*:\n- scope\.out is empty/);
  assert.equal(await before(hooks, "task", { subagent_type: "coder", prompt: "Go on.", task_id: "t1" }), "");  // continues
  assert.equal(await before(hooks, "task", { subagent_type: "explore", prompt: "Find the CLI." }), "");
  const off = await delegation.server({ client, directory: "/p" }, { brief: false });
  assert.equal(await before(off, "task", { subagent_type: "coder", prompt: "Mode: code" }), "");      // the check off
});

test("carl-delegation: the coder's own session keeps to its brief's files and mode; other sessions do not", async () => {
  const client = { session: { get: async ({ path }) => ({ data: { parentID: path.id === "child" ? "m" : undefined } }) } };
  const hooks = await delegation.server({ client, directory: "/p" }, { coder: "carl-coder" });
  const parts = [{ type: "text", text: BRIEF }];
  await hooks["chat.message"]({ sessionID: "child", agent: "carl-coder" }, { message: {}, parts });
  await hooks["chat.message"]({ sessionID: "other", agent: "explore" }, { message: {}, parts });
  assert.equal(await before(hooks, "edit", { filePath: "report/csv_export.py" }, "child"), "");
  assert.equal(await before(hooks, "edit", { filePath: "/p/report/csv_export.py" }, "child"), "");
  assert.match(await before(hooks, "write", { filePath: "report/__init__.py" }, "child"),
               /^\[CARL\] Blocked: report\/__init__\.py is not in your brief's files to create or change/);
  assert.match(await before(hooks, "edit", { filePath: "tests/test_csv_export.py" }, "child"),
               /is a test file, and in mode code you do not change tests/);
  assert.equal(await before(hooks, "bash", { command: "python -m pytest -q" }, "child"), "");
  assert.equal(await before(hooks, "write", { filePath: "report/__init__.py" }, "other"), "");     // not a coder
  assert.equal(await before(hooks, "write", { filePath: "report/__init__.py" }, "m"), "");         // the main agent
  await hooks["chat.message"]({ sessionID: "child", agent: "carl-coder" }, { message: {}, parts: [{ type: "text", text: "Go on." }] });
  assert.match(await before(hooks, "write", { filePath: "x.py" }, "child"), /not in your brief's files/);  // kept
  const test = BRIEF.replace('mode = "code"', 'mode = "test"').replace("report/csv_export.py", "tests/test_csv_export.py");
  await hooks["chat.message"]({ sessionID: "t", agent: "" }, { message: { agent: "coder" }, parts: [{ type: "text", text: test }] });
  assert.equal(await before(hooks, "write", { filePath: "tests/test_csv_export.py" }, "t"), "");
  assert.match(await before(hooks, "edit", { filePath: "report/csv_export.py" }, "t"), /is not a test file, and in mode test/);
  await hooks.event({ event: { type: "session.deleted", properties: { info: { id: "t" } } } });
  assert.equal(await before(hooks, "edit", { filePath: "report/csv_export.py" }, "t"), "");
});

// ------------------------------------------------------------------ the chain (Phase 23.4.3, item 3)

const CHAIN_BRIEF = (tests = "new") => `mode = "code"
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
// the test file that the test session writes: red fails before the code (src/feature.txt), green passes
const RED_TEST = 'import { existsSync } from "node:fs";\nimport { test } from "node:test";\nimport assert from "node:assert";\ntest("R1", () => assert.ok(existsSync("src/feature.txt")));\n';
const GREEN_TEST = 'import { test } from "node:test";\ntest("R1", () => {});\n';

/**
 * A fake OpenCode for the chain: the session API that the plugin calls (get, create, promptAsync, messages). A
 * prompt to a new session is the code session: it writes src/feature.txt (and with tamper, the test too), then
 * OpenCode's session.idle event comes. A prompt to the main session ("m") is the one result.
 */
function chainOpenCode({ tamper = false, version = "1.18.35" } = {}) {
  const dir = mkdtempSync(join(tmpdir(), "carl-oc-chain-"));
  const calls = [];
  const logs = [];
  let hooks;
  let delivered;
  const deliveredP = new Promise((r) => (delivered = r));
  const answers = new Map();
  const client = {
    global: { health: async () => ({ data: { healthy: true, version } }) },
    app: { log: async (o) => (logs.push(["log", o.body]), {}) },
    tui: { showToast: async (o) => (logs.push(["toast", o.body]), {}) },
    session: {
      get: async ({ path }) => ({ data: { id: path.id, parentID: path.id === "m" ? undefined : "m",
                                          permission: [{ permission: "bash", pattern: "rm *", action: "deny" }] } }),
      create: async (o) => (calls.push(["create", o.body]), { data: { id: "ses_code" } }),
      promptAsync: async (o) => {
        calls.push(["promptAsync", o.path.id, o.body]);
        if (o.path.id === "m") return delivered(o.body), { data: undefined };
        setTimeout(async () => {
          mkdirSync(join(dir, "src"), { recursive: true });
          writeFileSync(join(dir, "src", "feature.txt"), "ok\n");
          if (tamper) writeFileSync(join(dir, "tests", "check.test.mjs"), GREEN_TEST + "// made to pass\n");
          answers.set(o.path.id, 'status = "done"\nmode = "code"\nsummary = "Wrote src/feature.txt."');
          await hooks.event({ event: { type: "session.idle", properties: { sessionID: o.path.id } } });
        }, 10);
        return { data: undefined };
      },
      messages: async ({ path }) => ({ data: [
        { info: { role: "user" }, parts: [{ type: "text", text: "the brief" }] },
        { info: { role: "assistant" }, parts: [{ type: "text", text: answers.get(path.id) ?? "" }] }] }),
    },
  };
  return { dir, calls, logs, client, deliveredP, setHooks: (h) => (hooks = h) };
}

/** The test session, as the coder writes it: tests/check.test.mjs (red: it fails before the code). */
const testSession = (dir, red = true) => {
  mkdirSync(join(dir, "tests"), { recursive: true });
  writeFileSync(join(dir, "tests", "check.test.mjs"), red ? RED_TEST : GREEN_TEST);
  return 'status = "done"\nmode = "test"\nsummary = "One test for R1."';
};

test("carl-delegation, the chain in the foreground: the task becomes the test session; then the code session; one output", async () => {
  const oc = chainOpenCode();
  const hooks = await delegation.server({ client: oc.client, directory: oc.dir }, { coder: "coder" });
  oc.setHooks(hooks);
  const args = { subagent_type: "coder", description: "Feature", prompt: "```toml\n" + CHAIN_BRIEF() + "```", background: false };
  await hooks["tool.execute.before"]({ tool: "task", sessionID: "m", callID: "c1" }, { args });
  assert.match(args.prompt, /^mode = "test"\ngoal = "Write the feature file\."/);         // the test session's brief
  assert.equal(args.description, "Feature: tests");
  const report = testSession(oc.dir);
  const output = { title: "Feature: tests", metadata: { sessionId: "ses_test", model: { providerID: "llamacpp", modelID: "m1" } },
                   output: delegationMod.taskXml({ id: "ses_test", state: "completed", text: report }) };
  await hooks["tool.execute.after"]({ tool: "task", sessionID: "m", callID: "c1", args }, output);
  const [create, prompt] = oc.calls;
  assert.deepEqual(create, ["create", { parentID: "m", title: "Feature: code (@coder subagent)", agent: "coder", permission: [
    { permission: "bash", pattern: "rm *", action: "deny" }, { permission: "todowrite", pattern: "*", action: "deny" },
    { permission: "task", pattern: "*", action: "deny" }] }]);
  assert.equal(prompt[1], "ses_code");
  assert.equal(prompt[2].agent, "coder");
  assert.deepEqual(prompt[2].model, { providerID: "llamacpp", modelID: "m1" });         // the test session's model
  assert.match(prompt[2].parts[0].text, /^mode = "code"\ntests = "new"\n[\s\S]*\n\[test_session\]\nfiles = \["tests\/check\.test\.mjs"\]\nsummary = "One test for R1\."\n/);
  const res = delegationMod.parseTaskXml(output.output);
  assert.equal(res.id, "ses_code");                                   // a task_id goes on with the code session
  assert.equal(res.state, "completed");
  assert.match(res.text, /^\[CARL\] Chain: the coder ran in two new sessions/);
  assert.match(res.text, /Red start: CARL ran `node --test tests\/check\.test\.mjs` before the code: it failed \(exit 1\)\./);
  assert.match(res.text, /Tests unchanged after the code session \(tests\/check\.test\.mjs\)\./);
  assert.match(res.text, /## The test session's report \(mode test\)\n\nstatus = "done"\nmode = "test"/);
  assert.match(res.text, /## The code session's report \(mode code\)\n\nstatus = "done"\nmode = "code"/);
  // the code session's own gates come from its brief: no test file in mode code
  await hooks["chat.message"]({ sessionID: "ses_code", agent: "coder" }, { message: {}, parts: prompt[2].parts });
  assert.match(await before(hooks, "write", { filePath: "tests/check.test.mjs" }, "ses_code"), /is a test file, and in mode code/);
  assert.equal(await before(hooks, "write", { filePath: "src/feature.txt" }, "ses_code"), "");
});

test("carl-delegation, the chain in the background: the test session's completion is held; one result comes later", async () => {
  const oc = chainOpenCode({ tamper: true });
  const hooks = await delegation.server({ client: oc.client, directory: oc.dir }, { coder: "coder" });
  oc.setHooks(hooks);
  const args = { subagent_type: "coder", description: "Feature", prompt: CHAIN_BRIEF(), background: true };
  await hooks["tool.execute.before"]({ tool: "task", sessionID: "m", callID: "c2" }, { args });
  await hooks["tool.execute.after"]({ tool: "task", sessionID: "m", callID: "c2", args }, {
    metadata: { sessionId: "ses_test", background: true, jobId: "ses_test" },
    output: delegationMod.taskXml({ id: "ses_test", state: "running", summary: "Background task started", text: "working" }) });
  const rec = () => JSON.parse(readFileSync(join(CHAINS, "m.json"), "utf8")).chains;
  assert.deepEqual(rec().map((r) => [r.parent, r.test, r.stage, r.directory, r.pid]), [["m", "ses_test", "test", oc.dir, process.pid]]);
  assert.equal(statSync(join(CHAINS, "m.json")).mode & 0o777, 0o600);
  const done = delegationMod.taskXml({ id: "ses_test", state: "completed", summary: "Background task completed: Feature: tests",
                                      text: testSession(oc.dir, false) });
  const other = [{ type: "text", text: "a message of the user" }];
  await hooks["chat.message"]({ sessionID: "m", agent: "build" }, { message: {}, parts: other });   // passes
  await assert.rejects(hooks["chat.message"]({ sessionID: "m", agent: "build" }, { message: {}, parts: [{ type: "text", synthetic: true, text: done }] }),
                       /^Error: \[CARL\] Held: the test session of "Feature" ended; CARL runs its code session now/);
  assert.deepEqual(rec().map((r) => [r.stage, r.agent, r.chain.testOutput.slice(0, 15)]), [["held", "build", 'status = "done"']]);
  const body = await oc.deliveredP;
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(existsSync(join(CHAINS, "m.json")), false);            // delivered: the record is gone
  assert.equal(body.agent, "build");                                  // the main session's agent, as OpenCode's own
  assert.equal(body.parts[0].synthetic, true);
  const res = delegationMod.parseTaskXml(body.parts[0].text);
  assert.deepEqual([res.id, res.state, res.summary], ["ses_code", "completed", "Background task completed: Feature"]);
  assert.match(res.text, /\[CARL\] Warning: no new test failed before the code \(no red start\)/);
  assert.match(res.text, /\[CARL\] Warning: the code session changed these test files after the test session: tests\/check\.test\.mjs\./);
  // the result passes chat.message (it is not a test session's)
  await hooks["chat.message"]({ sessionID: "m", agent: "build" }, { message: {}, parts: body.parts });
});

test("carl-delegation, the chain: a failed test session's message says so in place; tests = existing and chain: false", async () => {
  const oc = chainOpenCode();
  const hooks = await delegation.server({ client: oc.client, directory: oc.dir }, { coder: "coder" });
  oc.setHooks(hooks);
  const bg = async (callID, prompt) => {
    const args = { subagent_type: "coder", description: "Feature", prompt, background: true };
    await hooks["tool.execute.before"]({ tool: "task", sessionID: "m", callID }, { args });
    await hooks["tool.execute.after"]({ tool: "task", sessionID: "m", callID, args },
                                      { metadata: { sessionId: `ses_${callID}`, background: true }, output: "" });
    return args;
  };
  await bg("f", CHAIN_BRIEF());
  const failed = [{ type: "text", synthetic: true, text: delegationMod.taskXml({ id: "ses_f", state: "error", summary: "Background task failed: Feature: tests", text: "the server stopped" }) }];
  await hooks["chat.message"]({ sessionID: "m", agent: "build" }, { message: {}, parts: failed });
  const f = delegationMod.parseTaskXml(failed[0].text);
  assert.equal(f.state, "error");
  assert.match(f.text, /^\[CARL\] Chain: the test session failed, so the code session did not run\.[\s\S]*the server stopped/);
  assert.equal(oc.calls.length, 0);                                    // no code session
  assert.equal(existsSync(join(CHAINS, "m.json")), false);            // delivered in place: no record
  mkdirSync(join(oc.dir, "tests"), { recursive: true });
  writeFileSync(join(oc.dir, "tests", "check.test.mjs"), GREEN_TEST);
  const existing = await bg("e", CHAIN_BRIEF("existing"));
  assert.match(existing.prompt, /^mode = "code"\ntests = "existing"/);   // one session, the task as it was
  writeFileSync(join(oc.dir, "tests", "check.test.mjs"), RED_TEST);
  const parts = [{ type: "text", synthetic: true, text: delegationMod.taskXml({ id: "ses_e", state: "completed", summary: "s", text: "Done." }) }];
  await hooks["chat.message"]({ sessionID: "m", agent: "build" }, { message: {}, parts });
  assert.match(delegationMod.parseTaskXml(parts[0].text).text, /^Done\.\n\n\[CARL\] Warning: the coder changed these test files: tests\/check\.test\.mjs\./);
  const off = await delegation.server({ client: oc.client, directory: oc.dir }, { chain: false });
  const args = { subagent_type: "coder", prompt: CHAIN_BRIEF() };
  await off["tool.execute.before"]({ tool: "task", sessionID: "m", callID: "x" }, { args });
  assert.equal(args.prompt, CHAIN_BRIEF());
  const continued = { subagent_type: "coder", prompt: CHAIN_BRIEF(), task_id: "ses_code" };
  await hooks["tool.execute.before"]({ tool: "task", sessionID: "m", callID: "y" }, { args: continued });
  assert.equal(continued.prompt, CHAIN_BRIEF());                       // a continued task: no chain
});

test("carl-delegation, the hold's gate: the version (SDK health, the test switch), the self-check", async () => {
  const health = (version) => ({ global: { health: async () => ({ data: { healthy: true, version } }) } });
  assert.equal(await delegationMod.openCodeVersion(health("1.18.35")), "1.18.35");
  assert.equal(await delegationMod.openCodeVersion({ global: { health: () => new Promise(() => {}) } }, 50), "");   // no answer
  assert.equal(await delegationMod.openCodeVersion({}), "");          // node runs the tests: not OpenCode's program
  process.env.CARL_TEST_OPENCODE_VERSION = "0.0.0-test";
  assert.equal(await delegationMod.openCodeVersion(health("1.18.35")), "0.0.0-test");
  delete process.env.CARL_TEST_OPENCODE_VERSION;
  assert.deepEqual(delegationMod.selfCheck({}), ["session.create", "session.promptAsync", "session.messages", "session.get"]);
  assert.deepEqual(delegationMod.selfCheck(chainOpenCode().client), []);
  assert.deepEqual(delegationMod.holdGate("1.18.35", []), { ok: true, why: "OpenCode 1.18.35" });
  assert.match(delegationMod.holdGate("1.19.0", []).why, /^OpenCode 1\.19\.0 is not a version that CARL checked the background hold on \(1\.18\.34, 1\.18\.35\)$/);
  assert.equal(delegationMod.holdGate("", []).ok, false);
  assert.match(delegationMod.holdGate("1.18.35", ["session.promptAsync"]).why, /lacks session\.promptAsync/);
  // a session's end from its messages (the code session's wait besides session.idle; the restart's check)
  const user = { info: { role: "user" }, parts: [{ type: "text", text: "brief" }] };
  const asst = (info, text = "the report") => ({ info: { role: "assistant", ...info }, parts: [{ type: "text", text }] });
  const end = (list) => { const e = delegationMod.sessionEnd(list); return [e.finished, e.ok, e.output]; };
  assert.deepEqual(end([user]), [false, false, ""]);
  assert.deepEqual(end([user, asst({ time: { created: 1 } }, "half")]), [false, true, "half"]);
  assert.deepEqual(end([user, asst({ time: { created: 1, completed: 2 }, finish: "tool-calls" })]), [false, true, "the report"]);
  assert.deepEqual(end([user, asst({ time: { created: 1, completed: 2 }, finish: "stop" })]), [true, true, "the report"]);
  assert.deepEqual(end([user, asst({ time: { created: 1, completed: 2 }, error: { name: "APIError", data: { message: "context size" } } }, "")]),
                   [true, false, "context size"]);
  // one OpenCode delivers a record: the claim
  const store = new delegationMod.ChainStore(mkdtempSync(join(tmpdir(), "carl-claim-")));
  const r = { parent: "p", test: "t" };
  assert.equal(store.claim(r), true);
  assert.equal(store.claim(r), false);                                // held by a process that runs (this one)
  store.release(r);
  assert.equal(store.claim(r), true);
  assert.equal(store.file("../x"), "");                               // only a session id names a file
});

test("carl-delegation, the chain's base: without the hold, the task runs in the foreground (any version); one notice", async () => {
  for (const setup of [() => chainOpenCode({ version: "1.19.0" }),
                       () => (process.env.CARL_TEST_OPENCODE_VERSION = "0.0.0-test", chainOpenCode())]) {
    const oc = setup();
    const hooks = await delegation.server({ client: oc.client, directory: oc.dir }, { coder: "coder" });
    delete process.env.CARL_TEST_OPENCODE_VERSION;
    oc.setHooks(hooks);
    const args = { subagent_type: "coder", description: "Feature", prompt: CHAIN_BRIEF(), background: true };
    await hooks["tool.execute.before"]({ tool: "task", sessionID: "m", callID: "b1" }, { args });
    assert.equal(args.background, false);                             // the test session in the foreground
    const again = { subagent_type: "coder", description: "Feature", prompt: CHAIN_BRIEF() };
    await hooks["tool.execute.before"]({ tool: "task", sessionID: "m", callID: "b2" }, { args: again });
    assert.equal(again.background, false);                            // carl-background, later in the list, keeps it
    await new Promise((r) => setTimeout(r, 10));
    assert.deepEqual(oc.logs.map((l) => l[0]), ["log", "toast"]);     // once
    assert.equal(oc.logs[0][1].level, "warn");
    assert.match(oc.logs[0][1].message, /^CARL runs the coder's tests-then-code chain in the foreground \(the background hold is off: OpenCode (1\.19\.0|0\.0\.0-test) is not a version/);
    const output = { metadata: { sessionId: "ses_test" }, output: delegationMod.taskXml({ id: "ses_test", state: "completed", text: testSession(oc.dir) }) };
    await hooks["tool.execute.after"]({ tool: "task", sessionID: "m", callID: "b1", args }, output);
    const res = delegationMod.parseTaskXml(output.output);
    assert.deepEqual([res.id, res.state], ["ses_code", "completed"]);
    assert.match(res.text, /^\[CARL\] Chain: the coder ran in two new sessions/);
    assert.equal(existsSync(join(CHAINS, "m.json")), false);          // no hold: no record
  }
});

test("carl-delegation, the hold is safe: a chain that OpenCode left undelivered is delivered at the next start", async () => {
  const dir = mkdtempSync(join(tmpdir(), "carl-oc-recover-"));
  const dead = spawnSync(process.execPath, ["-e", ""]).pid;          // an OpenCode process that is gone
  const plan = chainMod.coderPlan(CHAIN_BRIEF());
  const chain = new chainMod.Chain(plan.brief, dir);
  chain.testOutput = 'status = "done"\nmode = "test"\nsummary = "One test for R1."';
  chain.red = { red: true, why: "the test session's report has failing tests." };
  const snap = chain.snapshot();
  const rec = (test, stage, extra = {}) => ({ v: 1, parent: "p1", test, stage, description: `Task ${test}`, agent: "build",
                                              directory: dir, pid: dead, at: 1, chain: snap, ...extra });
  const done = (text) => [{ info: { role: "user" }, parts: [{ type: "text", text: "the brief" }] },
                          { info: { role: "assistant", time: { created: 1, completed: 2 }, finish: "stop" }, parts: [{ type: "text", text }] }];
  const open = [{ info: { role: "user" }, parts: [{ type: "text", text: "the brief" }] },
                { info: { role: "assistant", time: { created: 1 } }, parts: [{ type: "text", text: "half way" }] }];
  const msgs = { ses_c1: done('status = "done"\nmode = "code"\nsummary = "Wrote it."'), ses_c2: open, ses_t3: open, ses_t4: done("tests") };
  const store = new delegationMod.ChainStore(CHAINS);
  store.write("p1", [rec("ses_t1", "code", { code: "ses_c1" }), rec("ses_t2", "code", { code: "ses_c2" }), rec("ses_t3", "test"),
                     rec("ses_t4", "test"), rec("ses_t5", "held"),
                     rec("ses_t6", "code", { code: "ses_c1", pid: process.pid }),            // this OpenCode's own: running
                     rec("ses_t7", "code", { code: "ses_c1", directory: "/elsewhere" })]);   // another project's
  const sent = [];
  let all;
  const allP = new Promise((r) => (all = r));
  const client = { session: {
    messages: async ({ path }) => ({ data: msgs[path.id] ?? [] }),
    promptAsync: async (o) => (sent.push(o), sent.length === 5 && all(), { data: undefined }),
  } };
  await delegation.server({ client, directory: dir }, { recoverMs: 5 });
  await allP;
  await new Promise((r) => setTimeout(r, 20));
  const by = Object.fromEntries(sent.map((o) => {
    const t = delegationMod.parseTaskXml(o.body.parts[0].text);
    assert.equal(o.path.id, "p1");
    assert.equal(o.body.agent, "build");
    assert.equal(o.body.parts[0].synthetic, true);
    return [t.id, t];
  }));
  assert.deepEqual(Object.keys(by).sort(), ["ses_c1", "ses_c2", "ses_t3", "ses_t4", "ses_t5"]);
  assert.equal(by.ses_c1.state, "completed");                         // the code session finished: the one result
  assert.match(by.ses_c1.text, /^\[CARL\] Chain: the coder ran in two new sessions[\s\S]*## The code session's report \(mode code\)\n\nstatus = "done"/);
  assert.equal(by.ses_c1.summary, "Background task completed: Task ses_t1");
  assert.equal(by.ses_c2.state, "error");                             // not finished: what exists
  assert.match(by.ses_c2.text, /^\[CARL\] Chain: OpenCode stopped before the chain ended: the code session did not finish\./);
  assert.match(by.ses_c2.text, /## The test session's report \(mode test\)\n\nstatus = "done"[\s\S]*unfinished\)\n\nhalf way/);
  assert.match(by.ses_t3.text, /the test session did not finish, and the code session did not run\.[\s\S]*\n\nhalf way$/);
  assert.match(by.ses_t4.text, /: the code session did not run\.[\s\S]*report \(mode test\)\n\ntests$/);
  assert.match(by.ses_t5.text, /: the code session did not run\./);
  assert.deepEqual(store.read("p1").map((r) => r.test), ["ses_t6", "ses_t7"]);   // the delivered ones are gone
  store.write("p1", []);
  assert.equal(existsSync(join(CHAINS, "p1.json")), false);
});

test("carl-model-check: the warning for each situation", () => {
  const single = parseModels({ data: [{ id: "a" }] });
  assert.deepEqual(single, { router: false, models: [{ id: "a", status: undefined }] });
  assert.equal(verdict("a", single), undefined);
  assert.equal(verdict("b", single).variant, "warning");
  const router = parseModels({ data: [{ id: "a", status: { value: "loaded" } }, { id: "b", status: { value: "unloaded" } }, 7, { id: 3 }] });
  assert.equal(router.router, true);
  assert.equal(router.models.length, 2);                                   // the entries that are not models are left out
  assert.equal(verdict("a", router), undefined);
  assert.equal(verdict("b", router).variant, "info");
  assert.equal(verdict("c", router).variant, "error");
  assert.equal(parseModels({ nope: 1 }), undefined);
});

test("carl-model-check: short sentences, one path notation (Settings > Router), no arrow", () => {
  const single = parseModels({ data: [{ id: "a" }] });
  const router = parseModels({ data: [{ id: "a", status: { value: "loaded" } }, { id: "b", status: { value: "unloaded" } }] });
  const said = [verdict("b", single).message, verdict("b", router).message, verdict("c", router).message];
  assert.match(said[0], /^The server runs a, not b\. .*Settings > Router/);
  assert.match(said[2], /^The server does not have c\. It has a, b\./);
  for (const m of said) {
    assert.ok(!m.includes("→"), m);
    for (const sentence of m.split(/(?<=\.) /)) assert.ok(sentence.split(" ").length <= 20, sentence);
  }
  assert.match(verdict("c", { router: true, models: [] }).message, /It has no models\./);
});

// ------------------------------------------------------------------ carl-cache (OpenCode)

/** A fake llama-server: one idle slot; a chat reply that ends the turn. */
function fakeServer() {
  const seen = [];
  const json = (x) => new Response(JSON.stringify(x), { headers: { "Content-Type": "application/json" } });
  const fetch = async (input, init = {}) => {
    const u = new URL(String(input));
    seen.push({ path: u.pathname, headers: new Headers(init.headers), body: init.body });
    if (u.pathname === "/v1/models") return json({ data: [{ id: "m" }] });
    if (u.pathname === "/slots") return json([{ id: 0, is_processing: false, n_prompt_tokens: 0, id_task: 1 }]);
    if (u.pathname === "/props") return json({ model_path: "/m.gguf", build_info: "b1" });
    if (u.pathname === "/apply-template") return json({ prompt: "short" });
    if (u.pathname === "/tokenize") return json({ tokens: [1, 2, 3] });
    if (u.pathname.endsWith("/chat/completions")) {
      return new Response('data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',
                          { headers: { "Content-Type": "text/event-stream" } });
    }
    return json({ ok: true });
  };
  return { seen, fetch };
}

const srv = fakeServer();
globalThis.fetch = srv.fetch;                // the plugin wraps the fetch it finds
const dir = mkdtempSync(join(tmpdir(), "carl-cache-plugin-"));
copyFileSync(join(REPO, "client/opencode/plugins/carl-cache/index.js"), join(dir, "index.js"));
for (const f of ["carl-cache.js", "carl-panel.js"]) copyFileSync(join(REPO, "client/shared", f), join(dir, f));
const plugin = (await import(pathToFileURL(join(dir, "index.js")).href)).default;
const client = {
  app: { log: async () => ({}) },
  tui: { showToast: async () => ({}) },
  session: { get: async ({ path }) => ({ data: { id: path.id, parentID: path.id === "child" ? "main" : undefined } }) },
};
const hooks = await plugin.server({ client }, { provider: "llamacpp" });

const mark = async (sessionID, providerID = "llamacpp", agent = "build") => {
  const output = { headers: {} };
  await hooks["chat.headers"]({ sessionID, agent, model: { providerID } }, output);
  return output.headers["x-carl-cache"];
};

test("carl-cache: chat.headers marks only CARL's provider, with the session, the agent and a subagent's flag", async () => {
  assert.equal(await mark("s1", "openai"), undefined);
  assert.deepEqual(JSON.parse(await mark("s1")), { session: "s1", agent: "build", sub: false });
  assert.deepEqual(JSON.parse(await mark("child", "llamacpp", "coder")), { session: "child", agent: "coder", sub: true });
});

test("carl-cache: a marked chat request is pinned to a slot, and the mark does not reach the server", async () => {
  srv.seen.length = 0;
  const body = JSON.stringify({ model: "m", stream: true, messages: [{ role: "system", content: "sys" }, { role: "user", content: "hi" }] });
  const res = await fetch("http://10.0.0.1:8080/v1/chat/completions",
                          { method: "POST", headers: { "x-carl-cache": await mark("s1"), Authorization: "Bearer k" }, body });
  await res.text();                                                        // the stream ends: the turn's end runs
  const chat = srv.seen.find((x) => x.path.endsWith("/chat/completions"));
  assert.equal(chat.headers.get("x-carl-cache"), null);
  assert.equal(JSON.parse(chat.body).id_slot, 0);
  assert.equal(chat.headers.get("authorization"), "Bearer k");
});

test("carl-cache: other requests go as they are (a marked one that is not a chat request loses only the mark)", async () => {
  srv.seen.length = 0;
  await fetch("http://10.0.0.1:8080/v1/embeddings", { method: "POST", headers: { "x-carl-cache": await mark("s1") }, body: "{\"x\":1}" });
  await fetch("http://10.0.0.1:8080/v1/chat/completions", { method: "POST", body: "{\"y\":2}" });
  assert.deepEqual(srv.seen.map((x) => [x.path, x.body, x.headers.get("x-carl-cache")]),
                   [["/v1/embeddings", "{\"x\":1}", null], ["/v1/chat/completions", "{\"y\":2}", null]]);
});

test("carl-cache: a broken mark leaves the request as it was", async () => {
  srv.seen.length = 0;
  await fetch("http://10.0.0.1:8080/v1/chat/completions", { method: "POST", headers: { "x-carl-cache": "{not json" }, body: "{\"z\":3}" });
  assert.deepEqual(srv.seen.map((x) => [x.path, x.body]), [["/v1/chat/completions", "{\"z\":3}"]]);
});

test("carl-cache: an idle session's turn ends without an error", async () => {
  await hooks.event({ event: { type: "session.idle", properties: { sessionID: "s1" } } });
  await hooks.event({ event: { type: "session.status", properties: { sessionID: "s1", status: { type: "idle" } } } });
  await hooks.event({ event: { type: "message.updated", properties: {} } });
});
