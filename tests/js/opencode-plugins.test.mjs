// The OpenCode server plugins: carl-background, carl-model-check (check.js) and carl-cache (its hooks and
// its fetch wrapper, staged with the shared files as the installer lays them out, against a fake server).
// Run: node --test tests/js (tests/scripts/test_js.py runs it with the other suites).
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import background from "../../client/opencode/plugins/carl-background/index.js";
import { parseModels, verdict } from "../../client/opencode/plugins/carl-model-check/check.js";
import * as B from "../../client/shared/carl-brief.js";

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
/** Wait for p with the event loop held open (the plugin's recovery timer is unref'd: alone, node would end). */
const alive = (p) => {
  const t = setInterval(() => {}, 1000);
  return p.finally(() => clearInterval(t));
};
const chainMod = await import(pathToFileURL(join(delegationDir, "carl-chain.js")).href);
const FREE_FORM = (await import(pathToFileURL(join(delegationDir, "carl-delegation.js")).href)).FREE_FORM_TEXT;
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

test("carl-delegation: chat.params sets the coder's thinking for the model it runs on; other agents and models stay", async () => {
  // Phase 23.4.4: the option coderThinking ({"provider/model": reasoningEffort}); OpenCode sends output.options as the
  // request's reasoning_effort (checked with OpenCode 1.18.34 against agent-bench's fake server)
  const hooks = await delegation.server({}, { coder: "carl-coder",
    coderThinking: { "llamacpp/qwen3.8-27b": "none", "llamacpp/gemma-4-e4b": "high" } });
  const params = async (agent, providerID, id) => {
    const output = { temperature: 0.6, topP: 1, topK: 0, maxOutputTokens: undefined,
                     options: { reasoningEffort: "low", parallel_tool_calls: true } };
    await hooks["chat.params"]({ sessionID: "s", agent, model: { providerID, id }, provider: {}, message: {} }, output);
    return output.options;
  };
  assert.deepEqual(await params("carl-coder", "llamacpp", "qwen3.8-27b"), { reasoningEffort: "none", parallel_tool_calls: true });
  assert.equal((await params("carl-coder", "llamacpp", "gemma-4-e4b")).reasoningEffort, "high");
  assert.equal((await params("coder", "llamacpp", "qwen3.8-27b")).reasoningEffort, "none");     // CARL's coder names
  assert.equal((await params("build", "llamacpp", "qwen3.8-27b")).reasoningEffort, "low");      // the main agent
  assert.equal((await params("explore", "llamacpp", "qwen3.8-27b")).reasoningEffort, "low");    // another subagent
  assert.equal((await params("carl-coder", "llamacpp", "other")).reasoningEffort, "low");       // "same as main"
  assert.equal((await params("carl-coder", "mine", "qwen3.8-27b")).reasoningEffort, "low");     // not our provider
  const none = await delegation.server({}, {});                                                    // no table: as is
  const output = { options: { reasoningEffort: "low" } };
  await none["chat.params"]({ agent: "coder", model: { providerID: "llamacpp", id: "qwen3.8-27b" } }, output);
  assert.equal(output.options.reasoningEffort, "low");
  assert.equal(delegationMod.coderEffort(null, "coder", "coder", { providerID: "a", id: "b" }), undefined);
  assert.equal(delegationMod.coderEffort({ "a/b": 3 }, "coder", "coder", { providerID: "a", id: "b" }), undefined);
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
run_command = "python -m pytest tests/test_csv_export.py -q"
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

test("carl-delegation: after a refused code brief, the same session's tests-only brief is refused until the user writes again", async () => {
  const client = { session: { get: async () => ({ data: {} }) } };
  const hooks = await delegation.server({ client, directory: "/p" }, { coder: "coder" });
  const broken = BRIEF.replace(/\[\[acceptance_check\]\][\s\S]*$/, "");
  const testsOnly = BRIEF.replace('work_mode = "code"', 'work_mode = "tests-only"').replace(/\[\[known_file\]\][\s\S]*?(?=\[\[task_requirement)/, "");
  assert.match(await before(hooks, "task", { subagent_type: "coder", prompt: broken }, "s1"), /Fix these points/);
  assert.match(await before(hooks, "task", { subagent_type: "coder", prompt: testsOnly }, "s1"), /changes work_mode to "tests-only"/);
  assert.equal(await before(hooks, "task", { subagent_type: "coder", prompt: testsOnly }, "s2"), "");   // another session
  await hooks["chat.message"]({ sessionID: "s1", agent: "build" }, { message: {}, parts: [{ type: "text", text: "only tests, please" }] });
  assert.equal(await before(hooks, "task", { subagent_type: "coder", prompt: testsOnly }, "s1"), "");
});

test("carl-delegation: the Request Check: the user's words of the main session; a reminder once (the state file's setting)", async () => {
  const client = { session: { get: async ({ path }) => ({ data: { id: path.id, parentID: path.id === "sub" ? "m" : undefined } }) } };
  const dir = mkdtempSync(join(tmpdir(), "carl-req-"));
  const stateFile = join(dir, "carl.json");
  const hooks = await delegation.server({ client, directory: "/p" }, { coder: "coder", stateFile });
  const say = (sessionID, text, synthetic = false) =>
    hooks["chat.message"]({ sessionID, agent: "build" }, { message: {}, parts: [{ type: "text", text, synthetic }] });
  await say("m", "Write the header `month,orders,total` and exit with code 4 when the file is open.");
  await say("m", "<task id=\"x\" state=\"completed\">`--never-the-users`</task>", true);   // not the user's words
  const refused = await before(hooks, "task", { subagent_type: "coder", prompt: BRIEF });
  assert.match(refused, /^\[CARL\] Brief refused: the request check: [^\n]*\n- exit code 4\nCopy each/);
  assert.doesNotMatch(refused, /never-the-users/);
  assert.equal(await before(hooks, "task", { subagent_type: "coder", prompt: BRIEF }), "");     // reminded once: taken
  writeFileSync(stateFile, JSON.stringify({ request_check: "off" }));
  await say("m", "Add `--sep`.");
  assert.equal(await before(hooks, "task", { subagent_type: "coder", prompt: BRIEF }), "");
  writeFileSync(stateFile, JSON.stringify({ request_check: "on" }));
  await say("m", "Add `--sep`.");
  assert.match(await before(hooks, "task", { subagent_type: "coder", prompt: BRIEF }), /- `--sep`/);
  assert.match(await before(hooks, "task", { subagent_type: "coder", prompt: BRIEF }), /- `--sep`/);
});

test("carl-delegation: the free-form brief: taught and taken in a main session whose model qualifies, never in another", async () => {
  const client = { session: { get: async ({ path }) => ({ data: { id: path.id, parentID: path.id === "sub" ? "m" : undefined } }) } };
  const dir = mkdtempSync(join(tmpdir(), "carl-free-"));
  const stateFile = join(dir, "carl.json");
  writeFileSync(stateFile, JSON.stringify({ providers: { llamacpp: "llamacpp" }, models: { "gemma-4-e4b": {} } }));
  const hooks = await delegation.server({ client, directory: "/p" }, { gate: false, coder: "coder", stateFile });
  const system = async (sessionID, providerID, id) => {
    const output = { system: [`x ${delegationMod.RULE_BEGIN} the rule ${delegationMod.RULE_END}`] };
    await hooks["experimental.chat.system.transform"]({ sessionID, model: { providerID, id } }, output);
    return output.system;
  };
  assert.equal((await system("big", "openrouter", "some-frontier")).at(-1), FREE_FORM);   // not CARL's: taught
  assert.equal((await system("small", "llamacpp", "gemma-4-e4b")).length, 1);             // CARL's E4B: never
  const free = BRIEF.replace("expected_outcome = ", 'detailed_brief = """\nUse csv.writer.\n"""\nexpected_outcome = ');
  assert.equal(await before(hooks, "task", { subagent_type: "coder", prompt: free }, "big"), "");
  assert.match(await before(hooks, "task", { subagent_type: "coder", prompt: free }, "small"), /"detailed_brief", which is not in the schema/);
});

test("carl-delegation: a coder task whose brief fails the check is refused before the coder starts", async () => {
  const client = { session: { get: async () => ({ data: {} }) } };
  const hooks = await delegation.server({ client, directory: "/p" }, { coder: "coder" });
  assert.equal(await before(hooks, "task", { subagent_type: "coder", prompt: "Here it is:\n```toml\n" + BRIEF + "```" }), "");
  assert.match(await before(hooks, "task", { subagent_type: "coder", prompt: "Mode: code\nGoal: add the header" }),
               /^\[CARL\] Brief refused: the coder takes its task only as a TOML brief, .*send it again\. The brief's form/);
  const gap = await before(hooks, "task", { subagent_type: "carl-coder", prompt: BRIEF.replace(/current_state = .*\n/, "") });
  assert.match(gap, /^\[CARL\] Brief refused: the coder did not start\. Fix these points .*:\n- current_state is empty: with work_type = "follow_up"/);
  const tests = BRIEF.replace('work_type = "follow_up"', 'work_type = "follow_up"\nexisting_tests = ["tests/test_csv_export.py"]');
  assert.match(await before(hooks, "task", { subagent_type: "coder", prompt: tests }),   // not in the project folder /p
               /- existing_tests has "tests\/test_csv_export\.py", which is not in the project/);
  assert.equal(await before(hooks, "task", { subagent_type: "coder", prompt: "Go on.", task_id: "t1" }), "");  // continues
  assert.equal(await before(hooks, "task", { subagent_type: "explore", prompt: "Find the CLI." }), "");
  const off = await delegation.server({ client, directory: "/p" }, { brief: false });
  assert.equal(await before(off, "task", { subagent_type: "coder", prompt: "Mode: code" }), "");      // the check off
});

test("carl-delegation: the coder's own session keeps to its work_mode and its read files; other sessions do not", async () => {
  const client = { session: { get: async ({ path }) => ({ data: { parentID: path.id === "child" ? "m" : undefined } }) } };
  const hooks = await delegation.server({ client, directory: "/p" }, { coder: "carl-coder" });
  const parts = [{ type: "text", text: BRIEF }];
  await hooks["chat.message"]({ sessionID: "child", agent: "carl-coder" }, { message: {}, parts });
  await hooks["chat.message"]({ sessionID: "other", agent: "explore" }, { message: {}, parts });
  assert.equal(await before(hooks, "edit", { filePath: "report/csv_export.py" }, "child"), "");
  assert.equal(await before(hooks, "edit", { filePath: "/p/report/csv_export.py" }, "child"), "");
  assert.equal(await before(hooks, "write", { filePath: "report/__init__.py" }, "child"), "");    // a new module file
  assert.match(await before(hooks, "edit", { filePath: "report/model.py" }, "child"),
               /^\[CARL\] Blocked: report\/model\.py is in your brief to read only/);
  assert.match(await before(hooks, "edit", { filePath: "tests/test_csv_export.py" }, "child"),
               /is a test file, and in work_mode code you do not change tests/);
  assert.equal(await before(hooks, "bash", { command: "python -m pytest -q" }, "child"), "");
  assert.equal(await before(hooks, "write", { filePath: "report/__init__.py" }, "other"), "");     // not a coder
  assert.equal(await before(hooks, "write", { filePath: "report/__init__.py" }, "m"), "");         // the main agent
  await hooks["chat.message"]({ sessionID: "child", agent: "carl-coder" }, { message: {}, parts: [{ type: "text", text: "Go on." }] });
  assert.match(await before(hooks, "write", { filePath: "tests/x.py" }, "child"), /is a test file/);  // kept
  const test = BRIEF.replace('work_mode = "code"', 'work_mode = "tests-only"').replace('file_path = "report/csv_export.py"', 'file_path = "tests/test_csv_export.py"');
  await hooks["chat.message"]({ sessionID: "t", agent: "" }, { message: { agent: "coder" }, parts: [{ type: "text", text: test }] });
  assert.equal(await before(hooks, "write", { filePath: "tests/test_csv_export.py" }, "t"), "");
  assert.match(await before(hooks, "edit", { filePath: "report/csv_export.py" }, "t"), /is not a test file, and in work_mode tests-only/);
  await hooks.event({ event: { type: "session.deleted", properties: { info: { id: "t" } } } });
  assert.equal(await before(hooks, "edit", { filePath: "report/csv_export.py" }, "t"), "");
});

// ------------------------------------------------------------------ the chain (Phase 23.4.3, item 3)

const CHAIN_BRIEF = (type = "new_feature") => `work_mode = "code"
work_type = "${type}"
${type === "new_feature" ? "" : 'existing_tests = ["tests/check.test.mjs"]\n'}task_summary = "Write the feature file."
expected_outcome = "src/feature.txt is there."
current_state = "src/ is empty."
design_notes = "One file, no code."

[[known_file]]
file_path = "src/feature.txt"
file_action = "create"

[[task_requirement]]
requirement_id = "R1"
requirement_text = "src/feature.txt exists"

[[acceptance_check]]
check_id = "C1"
covers_requirements = ["R1"]
run_command = "node --test tests/check.test.mjs"
expected_result = "exit 0"
`;
// the test file that the test session writes: red fails before the code (src/feature.txt), green passes
const RED_TEST = 'import { existsSync } from "node:fs";\nimport { test } from "node:test";\nimport assert from "node:assert";\ntest("R1", () => assert.ok(existsSync("src/feature.txt")));\n';
const GREEN_TEST = 'import { test } from "node:test";\ntest("R1", () => {});\n';

/**
 * A fake OpenCode for the chain: the session API that the plugin calls (get, create, promptAsync, messages). A
 * prompt to a new session is the code session: it writes src/feature.txt (and with tamper, the test too), then
 * OpenCode's session.idle event comes. A prompt to the main session ("m") is the one result.
 */
function chainOpenCode({ tamper = false, version = "1.18.35", fail = "", second = null } = {}) {
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
          if (second) {                                       // the second session is the test session (after code)
            answers.set(o.path.id, second(dir));
            await hooks.event({ event: { type: "session.idle", properties: { sessionID: o.path.id } } });
            return;
          }
          mkdirSync(join(dir, "src"), { recursive: true });
          writeFileSync(join(dir, "src", "feature.txt"), "ok\n");
          if (tamper) writeFileSync(join(dir, "tests", "check.test.mjs"), GREEN_TEST + "// made to pass\n");
          answers.set(o.path.id, 'task_status = "done"\noutcome_summary = "Wrote src/feature.txt."');
          await hooks.event({ event: { type: "session.idle", properties: { sessionID: o.path.id } } });
        }, 10);
        return { data: undefined };
      },
      // fail: the code session's provider failed (Phase 23.4.5): OpenCode's assistant message with its error
      messages: async ({ path }) => ({ data: [
        { info: { role: "user" }, parts: [{ type: "text", text: "the brief" }] },
        fail && path.id === "ses_code"
          ? { info: { role: "assistant", error: { name: "APIError", data: { message: fail } } }, parts: [] }
          : { info: { role: "assistant" }, parts: [{ type: "text", text: answers.get(path.id) ?? "" }] }] }),
    },
  };
  return { dir, calls, logs, client, deliveredP, setHooks: (h) => (hooks = h) };
}

/** The test session, as the coder writes it: tests/check.test.mjs (red: it fails before the code). */
const testSession = (dir, red = true) => {
  mkdirSync(join(dir, "tests"), { recursive: true });
  writeFileSync(join(dir, "tests", "check.test.mjs"), red ? RED_TEST : GREEN_TEST);
  return 'outcome_summary = "One test for R1."\ntask_status = "done"';
};

test("carl-delegation, the chain in the foreground: the task becomes the test session; then the code session; one output", async () => {
  const oc = chainOpenCode();
  const hooks = await delegation.server({ client: oc.client, directory: oc.dir }, { gate: false, coder: "coder" });
  oc.setHooks(hooks);
  const args = { subagent_type: "coder", description: "Feature", prompt: "```toml\n" + CHAIN_BRIEF() + "```", background: false };
  await hooks["tool.execute.before"]({ tool: "task", sessionID: "m", callID: "c1" }, { args });
  assert.match(args.prompt, /^work_mode = "tests-only"\nwork_type = "new_feature"\n\ntask_summary = "Write the feature file\."/);   // the test session's brief
  assert.doesNotMatch(args.prompt, /design_notes|known_file/);
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
  assert.match(prompt[2].parts[0].text, /^work_mode = "code"\nwork_type = "new_feature"\n[\s\S]*\ndesign_notes = "One file, no code\."\n[\s\S]*\n\[test_session\]\ntest_files = \["tests\/check\.test\.mjs"\]\nsession_summary = "One test for R1\."\n/);
  const res = delegationMod.parseTaskXml(output.output);
  assert.equal(res.id, "ses_code");                                   // a task_id goes on with the code session
  assert.equal(res.state, "completed");
  assert.match(res.text, /^\[CARL\] Chain: the coder ran in two new sessions/);
  assert.match(res.text, /Red start: CARL ran `node --test tests\/check\.test\.mjs` before the code: it failed \(exit 1\)\./);
  assert.match(res.text, /Tests unchanged after the code session \(tests\/check\.test\.mjs\)\./);
  assert.match(res.text, /## The test session's report \(work_mode tests-only\)\n\noutcome_summary = "One test for R1\."/);
  assert.match(res.text, /## The code session's report \(work_mode code\)\n\ntask_status = "done"/);
  // the code session's own gates come from its brief: no test file in work_mode code
  await hooks["chat.message"]({ sessionID: "ses_code", agent: "coder" }, { message: {}, parts: prompt[2].parts });
  assert.match(await before(hooks, "write", { filePath: "tests/check.test.mjs" }, "ses_code"), /is a test file, and in work_mode code/);
  assert.equal(await before(hooks, "write", { filePath: "src/feature.txt" }, "ses_code"), "");
});

test("carl-delegation, Tests after code (the state file, read at each task): the task is the code session; the plugin starts the test session", async () => {
  const oc = chainOpenCode({ second: (dir) => testSession(dir) });
  const stateFile = join(oc.dir, "carl.json");
  writeFileSync(stateFile, JSON.stringify({ coder_tests: "after" }));
  const hooks = await delegation.server({ client: oc.client, directory: oc.dir }, { gate: false, coder: "coder", stateFile });
  oc.setHooks(hooks);
  const args = { subagent_type: "coder", description: "Feature", prompt: CHAIN_BRIEF(), background: false };
  await hooks["tool.execute.before"]({ tool: "task", sessionID: "m", callID: "a1" }, { args });
  assert.match(args.prompt, /^work_mode = "code"\nwork_type = "new_feature"\n[\s\S]*design_notes = "One file, no code\."/);   // the code session's brief
  assert.equal(args.description, "Feature: code");
  mkdirSync(join(oc.dir, "src"), { recursive: true });
  writeFileSync(join(oc.dir, "src", "feature.txt"), "ok\n");                // the code session's work
  const output = { metadata: { sessionId: "ses_first" }, output: delegationMod.taskXml({ id: "ses_first", state: "completed", text: 'task_status = "done"' }) };
  await hooks["tool.execute.after"]({ tool: "task", sessionID: "m", callID: "a1", args }, output);
  const [create, prompt] = oc.calls;
  assert.equal(create[1].title, "Feature: tests (@coder subagent)");
  assert.match(prompt[2].parts[0].text, /^work_mode = "tests-only"\n/);
  assert.doesNotMatch(prompt[2].parts[0].text, /design_notes|known_file/);
  const res = delegationMod.parseTaskXml(output.output);
  assert.match(res.text, /^\[CARL\] Chain: the coder ran in two new sessions: first the code \(work_mode code\), then the tests/);
  assert.match(res.text, /CARL ran `node --test tests\/check\.test\.mjs` after both sessions: it passed\./);
  assert.match(res.text, /The test session wrote: tests\/check\.test\.mjs\./);
  // off, changed while OpenCode runs (no restart): one session as written, the result says no test session ran
  writeFileSync(stateFile, JSON.stringify({ coder_tests: "off" }));
  const off = { subagent_type: "coder", description: "Feature", prompt: CHAIN_BRIEF(), background: false };
  await hooks["tool.execute.before"]({ tool: "task", sessionID: "m", callID: "a2" }, { args: off });
  assert.equal(off.prompt, CHAIN_BRIEF());
  const out2 = { metadata: { sessionId: "ses_one" }, output: delegationMod.taskXml({ id: "ses_one", state: "completed", text: "Done." }) };
  await hooks["tool.execute.after"]({ tool: "task", sessionID: "m", callID: "a2", args: off }, out2);
  assert.equal(delegationMod.parseTaskXml(out2.output).text, `Done.\n\n${chainMod.TESTS_OFF}`);
  assert.equal(oc.calls.length, 2);                                   // no session started for it
  // in the background: the completion message gets the line in place
  const bg = { subagent_type: "coder", description: "Feature", prompt: CHAIN_BRIEF(), background: true };
  await hooks["tool.execute.before"]({ tool: "task", sessionID: "m", callID: "a3" }, { args: bg });
  await hooks["tool.execute.after"]({ tool: "task", sessionID: "m", callID: "a3", args: bg }, { metadata: { sessionId: "ses_bg", background: true }, output: "" });
  const parts = [{ type: "text", synthetic: true, text: delegationMod.taskXml({ id: "ses_bg", state: "completed", summary: "s", text: "Done." }) }];
  await hooks["chat.message"]({ sessionID: "m", agent: "build" }, { message: {}, parts });
  assert.equal(delegationMod.parseTaskXml(parts[0].text).text, `Done.\n\n${chainMod.TESTS_OFF}`);
});

test("carl-delegation, the chain in the background: the test session's completion is held; one result comes later", async () => {
  const oc = chainOpenCode({ tamper: true });
  const hooks = await delegation.server({ client: oc.client, directory: oc.dir }, { gate: false, coder: "coder" });
  oc.setHooks(hooks);
  const args = { subagent_type: "coder", description: "Feature", prompt: CHAIN_BRIEF(), background: true };
  await hooks["tool.execute.before"]({ tool: "task", sessionID: "m", callID: "c2" }, { args });
  await hooks["tool.execute.after"]({ tool: "task", sessionID: "m", callID: "c2", args }, {
    metadata: { sessionId: "ses_test", background: true, jobId: "ses_test" },
    output: delegationMod.taskXml({ id: "ses_test", state: "running", summary: "Background task started", text: "working" }) });
  const rec = () => JSON.parse(readFileSync(join(CHAINS, "m.json"), "utf8")).chains;
  assert.deepEqual(rec().map((r) => [r.v, r.parent, r.first, r.stage, r.directory, r.pid]), [[2, "m", "ses_test", "first", oc.dir, process.pid]]);
  assert.equal(rec()[0].folder, delegationMod.folderId(oc.dir));      // the folder's identity (the 23.4.5 follow-up)
  assert.equal(statSync(join(CHAINS, "m.json")).mode & 0o777, 0o600);
  const done = delegationMod.taskXml({ id: "ses_test", state: "completed", summary: "Background task completed: Feature: tests",
                                      text: testSession(oc.dir, false) });
  const other = [{ type: "text", text: "a message of the user" }];
  await hooks["chat.message"]({ sessionID: "m", agent: "build" }, { message: {}, parts: other });   // passes
  await assert.rejects(hooks["chat.message"]({ sessionID: "m", agent: "build" }, { message: {}, parts: [{ type: "text", synthetic: true, text: done }] }),
                       /^Error: \[CARL\] Held: the test session of "Feature" ended; CARL runs its code session now/);
  assert.deepEqual(rec().map((r) => [r.stage, r.agent, r.chain.testOutput.slice(0, 15)]), [["held", "build", 'outcome_summary']]);
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

test("carl-delegation, the chain: a failed test session's message says so in place; follow_up with existing_tests and chain: false", async () => {
  const oc = chainOpenCode();
  const hooks = await delegation.server({ client: oc.client, directory: oc.dir }, { gate: false, coder: "coder" });
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
  const existing = await bg("e", CHAIN_BRIEF("follow_up"));
  assert.match(existing.prompt, /^work_mode = "code"\nwork_type = "follow_up"/);   // one session, the task as it was
  writeFileSync(join(oc.dir, "tests", "check.test.mjs"), RED_TEST);
  const parts = [{ type: "text", synthetic: true, text: delegationMod.taskXml({ id: "ses_e", state: "completed", summary: "s", text: "Done." }) }];
  await hooks["chat.message"]({ sessionID: "m", agent: "build" }, { message: {}, parts });
  assert.match(delegationMod.parseTaskXml(parts[0].text).text, /^Done\.\n\n\[CARL\] Warning: the coder changed these test files: tests\/check\.test\.mjs\./);
  const off = await delegation.server({ client: oc.client, directory: oc.dir }, { chain: false, gate: false });
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
    const hooks = await delegation.server({ client: oc.client, directory: oc.dir }, { gate: false, coder: "coder" });
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
    assert.match(oc.logs[0][1].message, /^CARL runs the coder's two-session chain \(tests and code\) in the foreground \(the background hold is off: OpenCode (1\.19\.0|0\.0\.0-test) is not a version/);
    const output = { metadata: { sessionId: "ses_test" }, output: delegationMod.taskXml({ id: "ses_test", state: "completed", text: testSession(oc.dir) }) };
    await hooks["tool.execute.after"]({ tool: "task", sessionID: "m", callID: "b1", args }, output);
    const res = delegationMod.parseTaskXml(output.output);
    assert.deepEqual([res.id, res.state], ["ses_code", "completed"]);
    assert.match(res.text, /^\[CARL\] Chain: the coder ran in two new sessions/);
    assert.equal(existsSync(join(CHAINS, "m.json")), false);          // no hold: no record
  }
});

const GATE_BRIEF = `work_mode = "code"
work_type = "follow_up"
task_summary = "Make add() take a third number."
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
/** A Python project in a folder: calc/core.py, and a test that passes. */
const gateProject = (dir) => {
  mkdirSync(join(dir, "calc"), { recursive: true });
  mkdirSync(join(dir, "tests"), { recursive: true });
  writeFileSync(join(dir, "calc", "__init__.py"), "");
  writeFileSync(join(dir, "calc", "core.py"), "def add(a, b):\n    return a + b\n");
  writeFileSync(join(dir, "tests", "test_core.py"), "from calc.core import add\n\ndef test_add():\n    assert add(1, 2) == 3\n");
};
const BROKEN = "def add(a, b, c=0):\n    return a - b + c\n";
const MENDED = "def add(a, b, c=0):\n    return a + b + c\n";

test("carl-delegation, the run gate: the baseline before the task; not done after it; a fix session mends it; one result (foreground)", async () => {
  const oc = chainOpenCode({ second: (dir) => (writeFileSync(join(dir, "calc", "core.py"), MENDED), 'task_status = "done"') });
  gateProject(oc.dir);
  const hooks = await delegation.server({ client: oc.client, directory: oc.dir }, { coder: "coder" });
  oc.setHooks(hooks);
  const args = { subagent_type: "coder", description: "Three", prompt: GATE_BRIEF, background: false };
  await hooks["tool.execute.before"]({ tool: "task", sessionID: "m", callID: "g1" }, { args });
  assert.equal(args.prompt, GATE_BRIEF);                                         // one session, as it is
  writeFileSync(join(oc.dir, "calc", "core.py"), BROKEN);                        // the coder's work breaks test_add
  const output = { metadata: { sessionId: "ses_first" }, output: delegationMod.taskXml({ id: "ses_first", state: "completed", text: 'task_status = "done"' }) };
  await hooks["tool.execute.after"]({ tool: "task", sessionID: "m", callID: "g1", args }, output);
  const [create, prompt] = oc.calls;
  assert.equal(create[1].title, "Three: fix 1 (@coder subagent)");
  const fix = B.parseBrief(prompt[2].parts[0].text).brief;
  assert.deepEqual([fix.workType, fix.failedAttempt.run.startsWith("python3 -m pytest")], ["bug_fix", true]);
  const res = delegationMod.parseTaskXml(output.output);
  assert.match(res.text, /^task_status = "done"\n\n## Fix round 1 \(work_mode code, bug_fix\)\n\ntask_status = "done"\n\n\[CARL\] Run gate: done after 1 fix round\./);
});

test("carl-delegation, the run gate in the background: held with the hold (one result later); checks only without it", async () => {
  const oc = chainOpenCode({ second: (dir) => (writeFileSync(join(dir, "calc", "core.py"), MENDED), "fixed") });
  gateProject(oc.dir);
  const hooks = await delegation.server({ client: oc.client, directory: oc.dir }, { coder: "coder" });
  oc.setHooks(hooks);
  const args = { subagent_type: "coder", description: "Three", prompt: GATE_BRIEF, background: true };
  await hooks["tool.execute.before"]({ tool: "task", sessionID: "m", callID: "g2" }, { args });
  await hooks["tool.execute.after"]({ tool: "task", sessionID: "m", callID: "g2", args }, {
    metadata: { sessionId: "ses_bg", background: true }, output: delegationMod.taskXml({ id: "ses_bg", state: "running", text: "working" }) });
  writeFileSync(join(oc.dir, "calc", "core.py"), BROKEN);
  const done = delegationMod.taskXml({ id: "ses_bg", state: "completed", summary: "Background task completed: Three", text: "my work" });
  await assert.rejects(hooks["chat.message"]({ sessionID: "m", agent: "build" }, { message: {}, parts: [{ type: "text", synthetic: true, text: done }] }),
                       /^Error: \[CARL\] Held: the coder session of "Three" ended; CARL runs its checks now/);
  const body = await oc.deliveredP;
  const res = delegationMod.parseTaskXml(body.parts[0].text);
  assert.match(res.text, /^my work\n\n## Fix round 1[\s\S]*\[CARL\] Run gate: done after 1 fix round\./);
  // without the hold (a version that CARL did not check): the checks in place, no fix round
  process.env.CARL_TEST_OPENCODE_VERSION = "0.0.0-test";
  const oc2 = chainOpenCode({ version: "0.0.0-test" });
  gateProject(oc2.dir);
  const hooks2 = await delegation.server({ client: oc2.client, directory: oc2.dir }, { coder: "coder" });
  oc2.setHooks(hooks2);
  const args2 = { subagent_type: "coder", description: "Three", prompt: GATE_BRIEF, background: true };
  await hooks2["tool.execute.before"]({ tool: "task", sessionID: "m", callID: "g3" }, { args: args2 });
  await hooks2["tool.execute.after"]({ tool: "task", sessionID: "m", callID: "g3", args: args2 }, { metadata: { sessionId: "ses_b3", background: true }, output: "" });
  writeFileSync(join(oc2.dir, "calc", "core.py"), BROKEN);
  const parts = [{ type: "text", synthetic: true, text: delegationMod.taskXml({ id: "ses_b3", state: "completed", summary: "s", text: "my work" }) }];
  await hooks2["chat.message"]({ sessionID: "m", agent: "build" }, { message: {}, parts });
  delete process.env.CARL_TEST_OPENCODE_VERSION;
  const t = delegationMod.parseTaskXml(parts[0].text).text;
  assert.match(t, /^my work\n\n\[CARL\] Run gate: NOT DONE\.\n- a test that passed before fails now: tests\/test_core\.py::test_add/);
  assert.match(t, /Give the coder this fix \(a bug_fix brief, ready to send\):\n```toml\nwork_mode = "code"\nwork_type = "bug_fix"/);
  assert.equal(oc2.calls.length, 0);                                             // no fix session
});

test("carl-delegation, the hold is safe for the run gate: a held gate that OpenCode left gets the coder's result at the next start", async () => {
  const dir = mkdtempSync(join(tmpdir(), "carl-oc-recover-gate-"));
  const dead = spawnSync(process.execPath, ["-e", ""]).pid;
  new delegationMod.ChainStore(CHAINS).write("p9", [{ v: 2, kind: "gate", parent: "p9", first: "ses_g", stage: "held", text: "my work",
                                                     description: "Three", agent: "build", directory: dir, pid: dead, at: 1 }]);
  const sent = [];
  let all;
  const allP = new Promise((r) => (all = r));
  const client = { session: { messages: async () => ({ data: [] }), promptAsync: async (o) => (sent.push(o), all(), { data: undefined }) } };
  await delegation.server({ client, directory: dir }, { recoverMs: 5 });
  await alive(allP);
  const t = delegationMod.parseTaskXml(sent[0].body.parts[0].text);
  assert.deepEqual([t.id, t.state], ["ses_g", "completed"]);
  assert.match(t.text, /^my work\n\n\[CARL\] Run gate: OpenCode stopped before the run gate ended\. Run the project's tests yourself/);
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(existsSync(join(CHAINS, "p9.json")), false);
});

test("carl-delegation, the hold is safe: a chain that OpenCode left undelivered is delivered at the next start", async () => {
  const dir = mkdtempSync(join(tmpdir(), "carl-oc-recover-"));
  const dead = spawnSync(process.execPath, ["-e", ""]).pid;          // an OpenCode process that is gone
  const plan = chainMod.coderPlan(CHAIN_BRIEF());
  const chain = new chainMod.Chain(plan.brief, dir);
  chain.testOutput = 'outcome_summary = "One test for R1."\ntask_status = "done"';
  chain.red = { red: true, why: "the test session's report has failing tests." };
  const snap = chain.snapshot();
  const rec = (test, stage, extra = {}) => ({ v: 1, parent: "p1", test, stage, description: `Task ${test}`, agent: "build",
                                              directory: dir, pid: dead, at: 1, chain: snap, ...extra });
  const done = (text) => [{ info: { role: "user" }, parts: [{ type: "text", text: "the brief" }] },
                          { info: { role: "assistant", time: { created: 1, completed: 2 }, finish: "stop" }, parts: [{ type: "text", text }] }];
  const open = [{ info: { role: "user" }, parts: [{ type: "text", text: "the brief" }] },
                { info: { role: "assistant", time: { created: 1 } }, parts: [{ type: "text", text: "half way" }] }];
  const msgs = { ses_c1: done('task_status = "done"\noutcome_summary = "Wrote it."'), ses_c2: open, ses_t3: open, ses_t4: done("tests") };
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
  await alive(allP);
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
  assert.match(by.ses_c1.text, /^\[CARL\] Chain: the coder ran in two new sessions[\s\S]*## The code session's report \(work_mode code\)\n\ntask_status = "done"/);
  assert.equal(by.ses_c1.summary, "Background task completed: Task ses_t1");
  assert.equal(by.ses_c2.state, "error");                             // not finished: what exists
  assert.match(by.ses_c2.text, /^\[CARL\] Chain: OpenCode stopped before the chain ended: the code session did not finish\./);
  assert.match(by.ses_c2.text, /## The test session's report \(work_mode tests-only\)\n\noutcome_summary = "One test[\s\S]*unfinished\)\n\nhalf way/);
  assert.match(by.ses_t3.text, /the test session did not finish, and the code session did not run\.[\s\S]*\n\nhalf way$/);
  assert.match(by.ses_t4.text, /: the code session did not run\.[\s\S]*report \(work_mode tests-only\)\n\ntests$/);
  assert.match(by.ses_t5.text, /: the code session did not run\./);
  assert.deepEqual(store.read("p1").map((r) => r.test), ["ses_t6", "ses_t7"]);   // the delivered ones are gone
  store.write("p1", []);
  assert.equal(existsSync(join(CHAINS, "p1.json")), false);
});

test("carl-delegation, the hold is safe: a record of another folder at the same path, another project or a session that is gone is dropped, not delivered", async () => {
  const dir = mkdtempSync(join(tmpdir(), "carl-oc-recover3-"));
  const dead = spawnSync(process.execPath, ["-e", ""]).pid;
  const chain = new chainMod.Chain(chainMod.coderPlan(CHAIN_BRIEF()).brief, dir);
  const here = delegationMod.folderId(dir);
  const rec = (first, parent, extra = {}) => ({ v: 2, parent, first, stage: "held", description: `Task ${first}`, agent: "build",
                                                directory: dir, folder: here, project: "prj_now", pid: dead, at: 1,
                                                chain: chain.snapshot(), ...extra });
  const store = new delegationMod.ChainStore(CHAINS);
  for (const [first, parent, extra] of [["ses_ok", "p3", {}], ["ses_folder", "p4", { folder: "1:2" }],
                                        ["ses_project", "p5", { project: "prj_old" }], ["ses_gone", "p6", {}],
                                        ["ses_moved", "p7", {}], ["ses_old", "p8", { folder: undefined, project: undefined }]]) {
    store.write(parent, [rec(first, parent, extra)]);
  }
  const sessions = { p3: { id: "p3", directory: dir }, p4: { id: "p4", directory: dir }, p5: { id: "p5", directory: dir },
                     p7: { id: "p7", directory: "/elsewhere" }, p8: { id: "p8", directory: dir } };
  const sent = [];
  const client = { session: {
    get: async ({ path }) => (sessions[path.id] ? { data: sessions[path.id] } : { error: { name: "NotFoundError" } }),
    messages: async () => ({ data: [] }),
    promptAsync: async (o) => (sent.push(o.path.id), { data: undefined }),
  } };
  await delegation.server({ client, directory: dir, project: { id: "prj_now" } }, { recoverMs: 5 });
  await new Promise((r) => setTimeout(r, 120));
  assert.deepEqual(sent.sort(), ["p3", "p8"]);                         // this folder, this project, its session there
  for (const p of ["p3", "p4", "p5", "p6", "p7", "p8"]) assert.equal(existsSync(join(CHAINS, `${p}.json`)), false, p);
  assert.equal(delegationMod.folderId(join(dir, "nope")), "");
});

test("carl-delegation, the hold is safe with Tests after code: a record of v 2 (first, second) is delivered with the sessions' roles", async () => {
  const dir = mkdtempSync(join(tmpdir(), "carl-oc-recover2-"));
  const dead = spawnSync(process.execPath, ["-e", ""]).pid;
  const plan = chainMod.coderPlan(CHAIN_BRIEF(), false, "after");
  const chain = new chainMod.Chain(plan.brief, dir, { order: plan.order });
  chain.firstOutcome({ ok: true, output: 'task_status = "done"\noutcome_summary = "Wrote it."' });
  const rec = (first, stage, extra = {}) => ({ v: 2, parent: "p2", first, stage, description: `Task ${first}`, agent: "build",
                                               directory: dir, pid: dead, at: 1, chain: chain.snapshot(), ...extra });
  const open = [{ info: { role: "user" }, parts: [{ type: "text", text: "the brief" }] },
                { info: { role: "assistant", time: { created: 1 } }, parts: [{ type: "text", text: "two tests so far" }] }];
  new delegationMod.ChainStore(CHAINS).write("p2", [rec("ses_f1", "second", { second: "ses_s1" }), rec("ses_f2", "held")]);
  const sent = [];
  let all;
  const allP = new Promise((r) => (all = r));
  const client = { session: {
    messages: async ({ path }) => ({ data: path.id === "ses_s1" ? open : [] }),
    promptAsync: async (o) => (sent.push(o), sent.length === 2 && all(), { data: undefined }),
  } };
  await delegation.server({ client, directory: dir }, { recoverMs: 5 });
  await alive(allP);
  const by = Object.fromEntries(sent.map((o) => {
    const t = delegationMod.parseTaskXml(o.body.parts[0].text);
    return [t.id, t];
  }));
  assert.match(by.ses_s1.text, /the test session did not finish\.[\s\S]*## The code session's report \(work_mode code\)\n\ntask_status = "done"[\s\S]*## The test session's last answer \(work_mode tests-only, unfinished\)\n\ntwo tests so far/);
  assert.match(by.ses_f2.text, /^\[CARL\] Chain: OpenCode stopped before the chain ended: the test session did not run\./);
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(existsSync(join(CHAINS, "p2.json")), false);          // delivered: the records are gone
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

// ------------------------------------------------------------------ Phase 23.4.5: the coder on another endpoint

const OR_MODEL = { providerID: "openrouter", id: "example-coder-32b",
                   variants: { low: { reasoningEffort: "low" }, high: { reasoningEffort: "high", reasoning: { effort: "high" } } } };

test("carl-delegation, an external coder model: chat.params sends only the variant /carl chose; no CARL thinking or sampling", async () => {
  const opts = { coder: "coder", coderThinking: { "llamacpp/qwen3.8-27b": "none" }, coderModel: "openrouter/example-coder-32b" };
  const params = async (hooks, agent, model) => {
    // OpenCode's output for a coder agent with no temperature (configure.py leaves it out for an external model)
    const output = { temperature: undefined, topP: undefined, topK: undefined, options: { usage: { include: true } } };
    await hooks["chat.params"]({ sessionID: "s", agent, model, provider: {}, message: {} }, output);
    return output;
  };
  // model default (no coderVariant): the request stays as it is
  let hooks = await delegation.server({}, opts);
  assert.deepEqual(await params(hooks, "coder", OR_MODEL), { temperature: undefined, topP: undefined, topK: undefined,
                                                             options: { usage: { include: true } } });
  // a variant: that variant of the model, merged into the options (nothing else changes)
  hooks = await delegation.server({}, { ...opts, coderVariant: "high" });
  const out = await params(hooks, "coder", OR_MODEL);
  assert.deepEqual(out.options, { usage: { include: true }, reasoningEffort: "high", reasoning: { effort: "high" } });
  assert.equal(out.temperature, undefined);
  OR_MODEL.variants.high.reasoning.effort = "high";                                 // the model's own entry: not changed
  const again = await params(hooks, "coder", OR_MODEL);
  again.options.reasoning.effort = "x";
  assert.equal(OR_MODEL.variants.high.reasoning.effort, "high");
  // not the coder, another model, a variant the model does not have: as it is
  assert.deepEqual((await params(hooks, "build", OR_MODEL)).options, { usage: { include: true } });
  assert.deepEqual((await params(hooks, "coder", { ...OR_MODEL, id: "other" })).options, { usage: { include: true } });
  hooks = await delegation.server({}, { ...opts, coderVariant: "max" });
  assert.deepEqual((await params(hooks, "coder", OR_MODEL)).options, { usage: { include: true } });
  // CARL's table still applies to CARL's models (same as main again in the same OpenCode)
  assert.equal((await params(hooks, "coder", { providerID: "llamacpp", id: "qwen3.8-27b" })).options.reasoningEffort, "none");
  assert.equal(delegationMod.coderVariant("a/b", "high", "coder", "coder", { providerID: "a", id: "b", variants: { high: 1 } }), undefined);
});

test("carl-model-check: a request to the coder's external model is not checked (no request to CARL's server, no warning)", async () => {
  const { default: check } = await import("../../client/opencode/plugins/carl-model-check/index.js");
  const said = [];
  const asked = [];
  const keep = globalThis.fetch;
  globalThis.fetch = async (u) => (asked.push(String(u)), new Response(JSON.stringify({ data: [{ id: "other-model" }] })));
  try {
    const hooks = await check.server({ client: { app: { log: async (o) => said.push(o) }, tui: { showToast: async (o) => said.push(o) } } },
                                     { provider: "llamacpp" });
    await hooks["chat.params"]({ agent: "coder", model: { providerID: "openrouter", id: "example-coder-32b" },
                                 provider: { options: { baseURL: "https://openrouter.ai/api/v1", apiKey: "sk-or-secret" } } }, {});
    assert.deepEqual([asked, said], [[], []]);
    // CARL's own provider is still checked (the same plugin, a CARL model the server does not run)
    await hooks["chat.params"]({ agent: "build", model: { providerID: "llamacpp", id: "m" },
                                 provider: { options: { baseURL: "http://10.0.0.1:8080/v1" } } }, {});
    assert.deepEqual(asked, ["http://10.0.0.1:8080/v1/models"]);
    assert.ok(said.length > 0);
  } finally {
    globalThis.fetch = keep;
  }
});

test("carl-cache: a request to another provider (the coder's external model) is not marked, saved, restored or claimed", async () => {
  const before = existsSync(join(HOME, ".config", "carl")) ? readdirSync(join(HOME, ".config", "carl")).sort() : [];
  srv.seen.length = 0;
  assert.equal(await mark("ses_coder", "openrouter", "coder"), undefined);            // no mark
  const body = JSON.stringify({ model: "example-coder-32b", stream: true, temperature: undefined,
                                messages: [{ role: "system", content: "You are coder." }, { role: "user", content: "the brief" }] });
  const res = await fetch("https://openrouter.ai/api/v1/chat/completions",
                          { method: "POST", headers: { Authorization: "Bearer sk-or-secret" }, body });
  await res.text();                                                                    // the reply ended the turn
  await hooks.event({ event: { type: "session.idle", properties: { sessionID: "ses_coder" } } });
  // one request, to the provider, as OpenCode sent it: no slot, no CARL call before or after it
  assert.deepEqual(srv.seen.map((x) => [x.path, x.body, x.headers.get("authorization"), x.headers.get("x-carl-cache")]),
                   [["/api/v1/chat/completions", body, "Bearer sk-or-secret", null]]);
  const after = existsSync(join(HOME, ".config", "carl")) ? readdirSync(join(HOME, ".config", "carl")).sort() : [];
  assert.deepEqual(after, before);                                                    // no claim, turn or record file
});

test("carl-delegation, the chain with an external coder model: both sessions on the coder agent's model", async () => {
  // OpenCode's task tool starts the test session on the coder agent's model (agent.model, read in OpenCode 1.18.35)
  // and says so in its metadata; the plugin starts the code session with that model and the coder agent
  const oc = chainOpenCode();
  const hooks = await delegation.server({ client: oc.client, directory: oc.dir }, { gate: false, coder: "coder", coderModel: "openrouter/example-coder-32b" });
  oc.setHooks(hooks);
  const args = { subagent_type: "coder", description: "Feature", prompt: CHAIN_BRIEF(), background: false };
  await hooks["tool.execute.before"]({ tool: "task", sessionID: "m", callID: "e1" }, { args });
  assert.match(args.prompt, /^work_mode = "tests-only"/);                             // the brief and the check as before
  const output = { metadata: { sessionId: "ses_test", model: { providerID: "openrouter", modelID: "example-coder-32b" } },
                   output: delegationMod.taskXml({ id: "ses_test", state: "completed", text: testSession(oc.dir) }) };
  await hooks["tool.execute.after"]({ tool: "task", sessionID: "m", callID: "e1", args }, output);
  const prompt = oc.calls.find((c) => c[0] === "promptAsync");
  assert.deepEqual([prompt[1], prompt[2].agent, prompt[2].model], ["ses_code", "coder", { providerID: "openrouter", modelID: "example-coder-32b" }]);
  const res = delegationMod.parseTaskXml(output.output);
  assert.equal(res.state, "completed");
  assert.match(res.text, /Red start: CARL ran `node --test tests\/check\.test\.mjs` before the code: it failed \(exit 1\)\./);
  // without the model in the metadata (an older OpenCode): no model in the prompt, so OpenCode takes the agent's own
  const oc2 = chainOpenCode();
  const hooks2 = await delegation.server({ client: oc2.client, directory: oc2.dir }, { gate: false, coder: "coder" });
  oc2.setHooks(hooks2);
  const args2 = { subagent_type: "coder", description: "Feature", prompt: CHAIN_BRIEF(), background: false };
  await hooks2["tool.execute.before"]({ tool: "task", sessionID: "m", callID: "e2" }, { args: args2 });
  await hooks2["tool.execute.after"]({ tool: "task", sessionID: "m", callID: "e2", args: args2 }, {
    metadata: { sessionId: "ses_test" }, output: delegationMod.taskXml({ id: "ses_test", state: "completed", text: testSession(oc2.dir) }) });
  const p2 = oc2.calls.find((c) => c[0] === "promptAsync");
  assert.deepEqual([p2[2].agent, "model" in p2[2]], ["coder", false]);
});

test("carl-delegation, a provider failure: the coder's task fails with the provider's error; no other session, no other model", async () => {
  const why = "401 Unauthorized: the key has expired (openrouter.ai)";
  // the code session's provider fails
  const oc = chainOpenCode({ fail: why });
  const hooks = await delegation.server({ client: oc.client, directory: oc.dir }, { gate: false, coder: "coder", coderModel: "openrouter/example-coder-32b" });
  oc.setHooks(hooks);
  const args = { subagent_type: "coder", description: "Feature", prompt: CHAIN_BRIEF(), background: false };
  await hooks["tool.execute.before"]({ tool: "task", sessionID: "m", callID: "f1" }, { args });
  const output = { metadata: { sessionId: "ses_test", model: { providerID: "openrouter", modelID: "example-coder-32b" } },
                   output: delegationMod.taskXml({ id: "ses_test", state: "completed", text: testSession(oc.dir) }) };
  await hooks["tool.execute.after"]({ tool: "task", sessionID: "m", callID: "f1", args }, output);
  const res = delegationMod.parseTaskXml(output.output);
  assert.equal(res.state, "error");
  assert.ok(res.text.includes(why), res.text);
  assert.deepEqual(oc.calls.map((c) => c[0]), ["create", "promptAsync"]);             // one code session, one prompt
  assert.deepEqual(oc.calls[1][2].model, { providerID: "openrouter", modelID: "example-coder-32b" });
  // the test session's provider fails (OpenCode's task tool gives the error): the chain stops with it
  const oc2 = chainOpenCode();
  const hooks2 = await delegation.server({ client: oc2.client, directory: oc2.dir }, { gate: false, coder: "coder" });
  oc2.setHooks(hooks2);
  const args2 = { subagent_type: "coder", description: "Feature", prompt: CHAIN_BRIEF(), background: false };
  await hooks2["tool.execute.before"]({ tool: "task", sessionID: "m", callID: "f2" }, { args: args2 });
  const out2 = { metadata: { sessionId: "ses_test" }, output: delegationMod.taskXml({ id: "ses_test", state: "error", text: why }) };
  await hooks2["tool.execute.after"]({ tool: "task", sessionID: "m", callID: "f2", args: args2 }, out2);
  const res2 = delegationMod.parseTaskXml(out2.output);
  assert.equal(res2.state, "error");
  assert.ok(res2.text.includes(why), res2.text);
  assert.deepEqual(oc2.calls, []);                                                    // no code session
});
