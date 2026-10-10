// client/shared/carl-panel.js: the /carl panel's rows, read from a temporary HOME's config files, and its switches
// (a fake carl-sync.py in the client folder records what the panel runs and plays the setup); then both renderers
// (OpenCode's TUI plugin, Pi's extension) with fake APIs.
// Run: node --test tests/js (tests/scripts/test_js.py runs it with the other suites).
import assert from "node:assert/strict";
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const HOME = mkdtempSync(join(tmpdir(), "carl-panel-"));
process.env.HOME = HOME;                     // read when the module loads
delete process.env.PI_CODING_AGENT_DIR;
const { act, allRows, coderModel, costOf, duration, hostOf, openCodeModels, openCodeSearch, outcome, packageVersions,
        panel, piModels, piSearch, restartNotice, rowsAt, shownState, stateModels, switches, thinkingOverrides,
        thinkingValues, thinkingWord, viewText, when } = await import("../../client/shared/carl-panel.js");
const CARL = join(HOME, ".config", "carl");
const PI = join(HOME, ".pi", "agent");
mkdirSync(CARL, { recursive: true });
mkdirSync(PI, { recursive: true });

/** The env file as client/configure.py writes it (env_file_text). */
const envFile = (provider) => `# CARL: OpenCode's tool switches (WEB_SEARCH=exa|parallel|off,
export OPENCODE_ENABLE_${provider.toUpperCase()}=1                 # web search: the queries go to ${provider} (https://x)
export OPENCODE_WEBSEARCH_PROVIDER=${provider}
export OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=1   # the task tool can run a subagent in the background
`;
const get = (client, id) => allRows(panel(client).rows).find((r) => r.id === id);
const ids = (client) => panel(client).rows.map((r) => r.id);
const subIds = (client, path = ["subagent"]) => (rowsAt(panel(client).rows, path) ?? []).map((r) => r.id);

test("OpenCode web search: the provider that is on, off only when neither is set", () => {
  assert.equal(openCodeSearch(envFile("exa")), "exa");
  assert.equal(openCodeSearch(envFile("parallel")), "parallel");
  assert.equal(openCodeSearch("export OPENCODE_EXPERIMENTAL_LSP_TOOL=1\n"), "");
  assert.equal(openCodeSearch("# export OPENCODE_ENABLE_EXA=1 was here\n"), "");      // a comment is not a switch
  assert.equal(openCodeSearch(""), "");
  for (const p of ["exa", "parallel"]) {
    writeFileSync(join(CARL, "opencode.env"), envFile(p));
    assert.equal(get("opencode", "web").state, p);
  }
  writeFileSync(join(CARL, "opencode.env"), "export OPENCODE_EXPERIMENTAL_LSP_TOOL=1\n");
  assert.equal(get("opencode", "web").state, "off");
});

test("Pi web search: the provider from the MCP server's address", () => {
  assert.equal(piSearch({ url: "https://mcp.exa.ai/mcp" }), "exa");
  assert.equal(piSearch({ url: "https://search.parallel.ai/mcp" }), "parallel");
  assert.equal(piSearch({ url: "https://exa.ai.example.com/mcp" }), "on");
  assert.equal(piSearch(undefined), "");
  writeFileSync(join(PI, "mcp.json"), JSON.stringify({ mcpServers: { "carl-web-search": { url: "https://search.parallel.ai/mcp" } } }));
  assert.equal(get("pi", "web").state, "parallel");
  writeFileSync(join(PI, "mcp.json"), JSON.stringify({ mcpServers: {} }));
  assert.equal(get("pi", "web").state, "off");
});

test("the rows read broken or missing files as off; no sync rows when the server runs here", () => {
  writeFileSync(join(CARL, "client-sync.json"), "{broken");
  mkdirSync(join(HOME, ".config", "opencode"), { recursive: true });
  writeFileSync(join(HOME, ".config", "opencode", "opencode.json"), "[]");              // not an object
  assert.equal(get("opencode", "cache").state, "off");
  assert.ok(!ids("opencode").some((id) => ["sync", "auto", "once", "apply"].includes(id)));
});

// ------------------------------------------------------------------ the parts on and off

const OC = join(HOME, ".config", "opencode");
const BUNDLE = join(HOME, "client");
const HASH = "c084921eda7e";
const WAITING = "d1e2f3a4b5c6";
const OFF_KEYS = ["NO_CODER", "NO_BACKGROUND_SUBAGENTS", "NO_REMINDER", "NO_BROWSER", "NO_LSP", "NO_SIDEBAR",
                  "NO_SWITCHER", "NO_CACHE", "NO_MODEL_CHECK"];

/** The state files' "models" (client/configure.py models_state): one model (single-model mode) by default. */
const ONE = { "qwen3.6-35b-a3b-iq3": { thinking: "effort", main: "low", coder: "main" } };
const TWO = { ...ONE, "gemma-4-e4b": { thinking: "on-off", main: "on", coder: "off" } };

/** Every CARL part on (allOn) or off, and a sync state from another computer with a config that waits. */
function stage(allOn, models = ONE) {
  mkdirSync(OC, { recursive: true });
  mkdirSync(BUNDLE, { recursive: true });
  for (const d of ["extensions/carl-cache", "extensions/subagent", "agents"]) mkdirSync(join(PI, d), { recursive: true });
  writeFileSync(join(BUNDLE, "remote.json"), JSON.stringify({ host: "192.168.42.1", port: 8080, cache_api: "http://192.168.42.1:8081" }));
  writeFileSync(join(CARL, "client-sync.json"), JSON.stringify({
    bundle: BUNDLE, service: allOn, connected: allOn, alive: Date.now() / 1000 - 30, auto_apply: !allOn,
    applied: HASH, applied_at: "2026-10-04 10:53:33", pending: allOn ? WAITING : null,
    error: allOn ? "The dashboard does not answer: Connection refused. Make sure that the dashboard runs on the server." : null,
  }));
  writeFileSync(join(OC, "opencode.json"), JSON.stringify(allOn ? {
    plugin: ["file:/x/carl-cache", ["file:/x/carl-model-check", {}], ["file:/x/carl-delegation", { reminder: true, gate: 0 }]],
    agent: { coder: {} }, mcp: { "carl-browser": {} }, lsp: true,
  } : {}));
  writeFileSync(join(OC, "tui.json"), JSON.stringify(allOn ? { plugin: ["file:/x/session-switcher", "file:/x/subagents-sidebar"] } : {}));
  writeFileSync(join(CARL, "opencode.env"), allOn ? envFile("exa") + "export OPENCODE_EXPERIMENTAL_LSP_TOOL=1\n" : "");
  writeFileSync(join(CARL, "client-install.env"), allOn ? "CLIENTS=both\n"
    : ["CLIENTS=both", "WEB_SEARCH=off", ...OFF_KEYS.map((k) => `${k}=1`)].join("\n") + "\n");
  for (const f of ["extensions/carl-cache/index.ts", "extensions/subagent/index.ts", "agents/coder.md"]) {
    if (allOn) writeFileSync(join(PI, f), "x");
    else rmSync(join(PI, f), { force: true });
  }
  writeFileSync(join(PI, "mcp.json"), JSON.stringify(allOn ? { mcpServers: { "carl-browser": {}, "carl-web-search": { url: "https://mcp.exa.ai/mcp" } } } : {}));
  writeFileSync(join(PI, "carl.json"), JSON.stringify({ background_subagents: allOn, delegation: { reminder: allOn, gate: 0 }, models }));
  writeFileSync(join(OC, "carl.json"), JSON.stringify({ models }));
}

const OC_ROWS = ["subagent", "browser", "web", "lsp", "sidebar", "switcher", "cache", "check", "sync", "auto", "apply", "once"];
const PI_ROWS = ["subagent", "browser", "web", "cache", "sync", "auto", "apply", "once"];
const CODER_ROWS = ["coder", "background", "reminder", "tests", "request", "free-form", "loop", "thinking", "coder-model"];
const LABELS = { subagent: "Coder Subagent", coder: "Coder", background: "Background Coder", reminder: "Delegation Reminder",
                 tests: "Tests", request: "Request Check", "free-form": "Free-Form Brief", loop: "Coder Loop", "run-gate": "Run Gate",
                 "fix-rounds": "Fix Rounds",
                 thinking: "Coder Thinking", "coder-model": "Coder Model", browser: "Browser",
                 web: "Web Search", lsp: "LSP", sidebar: "Subagents Side Panel", switcher: "Session Switcher",
                 cache: "Disk Cache", check: "Model Check", auto: "Apply New Configs at Once", sync: "Sync Service",
                 apply: "Apply the Waiting Config", once: "Check for a New Config" };
const KEYS = { coder: "NO_CODER", background: "NO_BACKGROUND_SUBAGENTS", reminder: "NO_REMINDER", browser: "NO_BROWSER",
               lsp: "NO_LSP", sidebar: "NO_SIDEBAR", switcher: "NO_SWITCHER", cache: "NO_CACHE", check: "NO_MODEL_CHECK" };

test("one row for each part, in the order of the groups; OpenCode has LSP, the side panels and the model check", () => {
  stage(true);
  assert.deepEqual(ids("opencode"), OC_ROWS);
  assert.deepEqual(ids("pi"), PI_ROWS);
  for (const client of ["opencode", "pi"]) {
    for (const r of allRows(panel(client).rows)) assert.equal(r.label, LABELS[r.id]);
  }
  assert.ok(!allRows(panel("opencode").rows).some((r) => /gate/i.test(r.label) && r.id !== "run-gate"));   // the new-file gate: the dashboard's
});

test("Coder Subagent: a list of the coder's rows; its state is the coder's (Phase 23.4.4)", () => {
  stage(true);
  for (const client of ["opencode", "pi"]) {
    const s = get(client, "subagent");
    assert.deepEqual([s.kind, s.state, s.listTitle], ["list", "on", "Coder Subagent"]);
    assert.deepEqual(subIds(client), CODER_ROWS);
    assert.ok(!ids(client).includes("background") && !ids(client).includes("reminder"));   // not in the top list
    assert.deepEqual(get(client, "coder").actions.off.args, ["set", "NO_CODER=1"]);
  }
});

test("without the coder, the sub-list has only the coder's switch", () => {
  stage(false);
  for (const client of ["opencode", "pi"]) {
    assert.equal(get(client, "subagent").state, "off");
    assert.equal(get(client, "coder").state, "off");
    assert.deepEqual(subIds(client), ["coder"]);
  }
});

const Q = "qwen3.6-35b-a3b-iq3";
const FULL = "Use it only with a full spec (spec-kit or a similar tool).";
const getIn = (client, id, session) => allRows(panel(client, session).rows).find((r) => r.id === id);

test("Coder Thinking: one row for the coder's model; dashboard default first, then same as main (Phase 23.4.4)", () => {
  stage(true);
  for (const client of ["opencode", "pi"]) {
    const r = get(client, "thinking");
    assert.deepEqual([r.kind, r.state, r.value], ["choice", "dashboard default", "default"]);
    assert.deepEqual(r.values, ["default", "main", "off", "low", "medium", "xhigh"]);
    assert.deepEqual(r.titles, { main: "same as main", default: "dashboard default" });
    assert.equal(r.choiceTitle, `Coder Thinking with ${Q}`);
    // no session (the home screen): the config's default model, and the note says so
    assert.deepEqual(r.notes, { main: "the default model's thinking (low)", default: "same as main (low)" });
    assert.deepEqual(Object.keys(r.actions), ["main", "off", "low", "medium", "xhigh"]);
    const off = r.actions.off;
    assert.deepEqual(off.args, ["set", `CODER_THINKING=${Q}:off`]);
    assert.deepEqual([off.busy, off.row, off.want, off.live], ["turning off…", "thinking", "off", ["pi"]]);
    assert.equal(off.did, `CARL set the coder's thinking with ${Q} to off on this computer. ${FULL}`);
    assert.equal(r.actions.low.busy, "switching to low…");
    assert.equal(r.actions.low.did, `CARL set the coder's thinking with ${Q} to low on this computer.`);
    assert.equal(r.actions.main.did, `CARL set the coder's thinking with ${Q} to same as main on this computer.`);
    // in a session on that model: the main session's thinking
    assert.equal(getIn(client, "thinking", { model: `llamacpp/${Q}` }).notes.main, "the main session's thinking (low)");
  }
});

test("Coder Thinking: this computer's value; dashboard default removes it (what it gives in the note)", () => {
  stage(true, { [Q]: { thinking: "effort", main: "low", coder: "off", dashboard: "medium" } });
  writeFileSync(join(CARL, "client-install.env"), `CLIENTS=both\nCODER_THINKING=${Q}:off\n`);
  for (const client of ["opencode", "pi"]) {
    const r = get(client, "thinking");
    assert.deepEqual([r.state, r.value], ["off", "off"]);
    assert.equal(r.notes.default, "medium");
    assert.deepEqual(Object.keys(r.actions), ["default", "main", "low", "medium", "xhigh"]);
    const d = r.actions.default;
    assert.deepEqual(d.args, ["set", `CODER_THINKING=${Q}:default`]);
    assert.deepEqual([d.busy, d.want], ["switching to dashboard default…", "default"]);
    assert.equal(d.did, `CARL set the coder's thinking with ${Q} to dashboard default (medium) on this computer.`);
  }
  // an on / off model whose main session does not think: same as main and a default that gives it say "full spec"
  stage(true, { "gemma-4-e4b": { thinking: "on-off", main: "off", coder: "on", dashboard: "main" } });
  writeFileSync(join(CARL, "client-install.env"), "CODER_THINKING=gemma-4-e4b:on\n");
  const r = get("pi", "thinking");
  assert.deepEqual([r.state, r.values], ["on", ["default", "main", "off", "on"]]);
  assert.equal(r.notes.main, "the default model's thinking (off)");
  assert.equal(r.notes.default, "same as main (off)");
  assert.match(r.actions.main.did, /same as main on this computer\. Use it only with a full spec/);
  assert.match(r.actions.default.did, /dashboard default \(same as main\) on this computer\. Use it only with a full spec/);
  assert.equal(r.actions.main.busy, "switching to same as main…");
  // a state file from before "dashboard" was written: no note for the default while this computer has a value
  stage(true, { [Q]: { thinking: "effort", main: "low", coder: "xhigh" } });
  writeFileSync(join(CARL, "client-install.env"), `CODER_THINKING=${Q}:xhigh\n`);
  assert.equal(get("opencode", "thinking").state, "xhigh");
  assert.equal(get("opencode", "thinking").notes.default, undefined);
  assert.equal(get("opencode", "thinking").actions.default.did, `CARL set the coder's thinking with ${Q} to dashboard default on this computer.`);
});

test("router mode: the row is for the session's model, else the config's default model (no list of models)", () => {
  stage(true, TWO);
  const g = "gemma-4-e4b";
  // no session, no default model in the config: the first model
  let r = get("opencode", "thinking");
  assert.deepEqual([r.kind, r.choiceTitle, r.state], ["choice", `Coder Thinking with ${Q}`, "dashboard default"]);
  // the session's model
  r = getIn("opencode", "thinking", { model: `llamacpp/${g}` });
  assert.deepEqual([r.choiceTitle, r.notes.main, r.notes.default], [`Coder Thinking with ${g}`, "the main session's thinking (on)", "off"]);
  assert.deepEqual(r.values, ["default", "main", "off", "on"]);
  assert.deepEqual(r.actions.on.args, ["set", `CODER_THINKING=${g}:on`]);
  // the config's default model when the session has none (or one that is not CARL's)
  const oc = JSON.parse(readFileSync(join(OC, "opencode.json"), "utf8"));
  writeFileSync(join(OC, "opencode.json"), JSON.stringify({ ...oc, model: `llamacpp/${g}` }));
  for (const session of [{}, { model: "openrouter/some-free-model" }, { model: `other/${g}` }]) {
    r = getIn("opencode", "thinking", session);
    assert.deepEqual([r.choiceTitle, r.notes.main], [`Coder Thinking with ${g}`, "the default model's thinking (on)"]);
  }
  assert.equal(getIn("opencode", "thinking", { model: `llamacpp/${Q}` }).choiceTitle, `Coder Thinking with ${Q}`);
  // ours under the other provider id ("carl", next to a user's own "llamacpp": its models are not CARL's)
  writeFileSync(join(OC, "carl.json"), JSON.stringify({ providers: { llamacpp: "carl" }, models: TWO }));
  assert.equal(getIn("opencode", "thinking", { model: `carl/${g}` }).notes.main, "the main session's thinking (on)");
  assert.equal(getIn("opencode", "thinking", { model: `llamacpp/${g}` }).choiceTitle, `Coder Thinking with ${Q}`);
  // Pi: settings.json defaultProvider / defaultModel
  writeFileSync(join(PI, "settings.json"), JSON.stringify({ defaultProvider: "llamacpp", defaultModel: g }));
  assert.equal(get("pi", "thinking").choiceTitle, `Coder Thinking with ${g}`);
  assert.equal(getIn("pi", "thinking", { model: `llamacpp/${Q}` }).notes.main, "the main session's thinking (low)");
  rmSync(join(PI, "settings.json"));
  stage(true, {});                                                                      // an install before 23.4.4
  assert.deepEqual(subIds("pi"), ["coder", "background", "reminder", "tests", "request", "free-form", "loop", "coder-model"]);
});

test("coderModel: the session's CARL model, else the default, else the first", () => {
  const ms = stateModels(TWO);
  const pick = (session, fallback = "") => {
    const c = coderModel(ms, ["llamacpp"], session, fallback);
    return c && [c.m.id, c.from];
  };
  assert.deepEqual(pick({ model: "llamacpp/gemma-4-e4b" }), ["gemma-4-e4b", "session"]);
  assert.deepEqual(pick({ model: "llamacpp/unknown" }, "llamacpp/gemma-4-e4b"), ["gemma-4-e4b", "default"]);
  assert.deepEqual(pick({}, "carl/gemma-4-e4b"), [Q, "default"]);
  assert.deepEqual(pick({ model: "llamacpp" }), [Q, "default"]);
  assert.equal(coderModel([], ["llamacpp"], {}, ""), undefined);
});

test("Coder Model: same as main alone when the client has no other model (it opens nothing)", () => {
  stage(true);
  const r = get("opencode", "coder-model");
  assert.deepEqual([r.kind, r.state, r.value, r.values], ["choice", "same as main", "main", ["main"]]);
  assert.deepEqual(r.actions, {});
  assert.equal(shownState(r), "same as main");
});

test("Coder Subagent and Coder Loop: their state shows a › (they open a list); only those rows", () => {
  stage(true);
  for (const client of ["opencode", "pi"]) {
    const rows = allRows(panel(client).rows);
    assert.deepEqual(rows.filter((r) => r.arrow).map((r) => r.id), ["subagent", "loop"]);
    assert.equal(shownState(get(client, "loop")), "on ›");
    assert.equal(shownState(get(client, "subagent")), "on ›");
    assert.equal(shownState(get(client, "subagent"), "turning off…"), "turning off…");
    assert.equal(shownState(get(client, "browser")), "on");
  }
  stage(false);
  assert.equal(shownState(get("pi", "subagent")), "off ›");
});

test("the state files' models are read with care", () => {
  assert.deepEqual(stateModels({ "a b": {}, x: { thinking: "effort", main: "nope", coder: "xhigh", dashboard: "low" }, y: 3 }),
                   [{ id: "x", kind: "effort", main: "on", coder: "xhigh", dashboard: "low", override: false },
                    { id: "y", kind: "on-off", main: "on", coder: "main", dashboard: "main", override: false }]);
  assert.deepEqual(stateModels({ x: { coder: "off" } }, { x: "off" }),
                   [{ id: "x", kind: "on-off", main: "on", coder: "off", dashboard: "", override: true }]);
  assert.deepEqual(stateModels(undefined), []);
  assert.deepEqual(thinkingOverrides("a:off, b.c:xhigh,bad,c:nope,:on, d:main"), { a: "off", "b.c": "xhigh", d: "main" });
  assert.deepEqual(thinkingOverrides(undefined), {});
  assert.deepEqual(thinkingValues("effort"), ["default", "main", "off", "low", "medium", "xhigh"]);
  assert.deepEqual(thinkingValues("on-off"), ["default", "main", "off", "on"]);
  assert.equal(thinkingWord("main"), "same as main");
  assert.equal(thinkingWord("default"), "dashboard default");
});

for (const allOn of [true, false]) {
  test(`the states and the switches ${allOn ? "with every part on" : "with every part off"}`, () => {
    stage(allOn);
    for (const client of ["opencode", "pi"]) {
      for (const r of allRows(panel(client).rows)) {
        if (["sync", "apply", "once", "subagent", "tests", "request", "free-form", "loop", "run-gate", "fix-rounds", "thinking",
             "coder-model"].includes(r.id)) continue;
        const want = r.id === "web" ? (allOn ? "exa" : "off") : r.id === "auto" ? (allOn ? "off" : "on") : allOn ? "on" : "off";
        assert.equal(r.state, want, `${client} ${r.id}`);
        if (r.id === "auto") {
          assert.equal(r.kind, "switch");
          assert.deepEqual(Object.values(r.actions).map((a) => a.args), [["auto", allOn ? "on" : "off"]]);
        } else if (r.id === "web") {
          assert.equal(r.kind, "choice");
          assert.deepEqual(r.values, ["exa", "parallel", "off"]);
          assert.deepEqual(r.notes, { exa: "Queries go to exa.ai.", parallel: "Queries go to parallel.ai." });
          assert.deepEqual(Object.values(r.actions).map((a) => a.args[1]),
                           allOn ? ["WEB_SEARCH=parallel", "WEB_SEARCH=off"] : ["WEB_SEARCH=exa", "WEB_SEARCH=parallel"]);
        } else {
          assert.equal(r.kind, "switch");
          const [[want2, a]] = Object.entries(r.actions);
          assert.equal(want2, allOn ? "off" : "on");
          assert.equal(a.busy, allOn ? "turning off…" : "turning on…");
          assert.deepEqual(a.args, ["set", `${KEYS[r.id]}=${allOn ? "1" : "on"}`, ...(r.id === "coder" && !allOn ? ["CODER=1"] : [])],
                           `${client} ${r.id}`);    // on: also with 1 slot
        }
      }
    }
  });
}

test("the reminder and the background: what is installed", () => {
  stage(true);
  const oc = JSON.parse(readFileSync(join(OC, "opencode.json"), "utf8"));
  oc.plugin[2][1].reminder = false;
  writeFileSync(join(OC, "opencode.json"), JSON.stringify(oc));
  writeFileSync(join(PI, "carl.json"), JSON.stringify({ background_subagents: false, delegation: { reminder: false } }));
  assert.equal(get("opencode", "reminder").state, "off");
  assert.equal(get("pi", "reminder").state, "off");
  assert.equal(get("pi", "background").state, "off");
  assert.equal(get("opencode", "background").state, "on");
  writeFileSync(join(CARL, "client-install.env"), "NO_REMINDER=1\n");
  assert.deepEqual(switches(), { NO_REMINDER: "1" });
});

// ------------------------------------------------------------------ the words: a control panel, the 23.2 rules

for (const allOn of [true, false]) {
  test(`the panel ${allOn ? "with every part on" : "with every part off"}: labels, short lower-case states, no prose`, () => {
    stage(allOn);
    for (const client of ["opencode", "pi"]) {
      for (const r of allRows(panel(client).rows)) {
        assert.match(r.label, /^[A-Z]/, `${client} ${r.id}: a label starts with a capital`);
        assert.ok(r.label.length <= 30, `${client} ${r.id}: "${r.label}"`);
        assert.equal(r.state, r.state.toLowerCase(), `${client} ${r.id}: "${r.state}"`);
        assert.ok(r.state.length <= 17, `${client} ${r.id}: a state, not a sentence: "${r.state}"`);
        assert.ok(!/ · |\b[A-Z_]{3,}=/.test(r.label + r.state), `${client} ${r.id}`);
        for (const a of [...Object.values(r.actions ?? {}), ...(r.action ? [r.action] : [])]) {
          assert.match(a.busy, /^[a-z]+ing\b.*…$/, `${client} ${r.id}: "${a.busy}"`);
        }
      }
    }
  });
}

test("the sync rows: the service, apply at once, a waiting config, the check; the view's label rows", () => {
  stage(true);
  const s = get("opencode", "sync");
  assert.equal(s.kind, "view");
  assert.equal(s.viewTitle, "Config Sync");
  assert.equal(s.state, "error");
  const v = Object.fromEntries(s.view);
  assert.equal(v["Sync Service"], "connected");
  assert.equal(v["Server Address"], "192.168.42.1:8080");
  assert.equal(v["Dashboard API"], "192.168.42.1:8081");
  assert.equal(v["Waiting Config"], WAITING);
  assert.match(v["Last Check"], /^failed: The dashboard does not answer/);
  assert.match(viewText(s.view).split("\n")[0], /^Sync Service {4}connected$/);
  assert.deepEqual(get("opencode", "apply").action.args, ["apply"]);
  assert.deepEqual(get("opencode", "once").action.args, ["once"]);
  stage(false);
  assert.equal(get("pi", "sync").state, "not installed");
  assert.ok(!ids("pi").includes("apply"));
  assert.equal(panel("pi").title, "Restart Pi to use 1 change.");   // the config applied since this module loaded
});

test("the sync view: the client package's version and the server's", () => {
  stage(true);
  rmSync(join(BUNDLE, "VERSION"), { force: true });
  assert.ok(!Object.hasOwn(Object.fromEntries(get("opencode", "sync").view), "Client Package"));
  writeFileSync(join(BUNDLE, "VERSION"), "1.7.0\n");
  writeFileSync(join(BUNDLE, "remote.json"), JSON.stringify({ host: "192.168.42.1", port: 8080,
                                                            cache_api: "http://192.168.42.1:8081", version: "1.8.0" }));
  const v = Object.fromEntries(get("pi", "sync").view);
  assert.equal(v["Client Package"], "1.7.0");
  assert.equal(v.Server, "CARL 1.8.0");
  assert.deepEqual(packageVersions(BUNDLE, { version: "1.8.0\n<script>" }), { client: "1.7.0", server: "" });
  assert.deepEqual(packageVersions("", {}), { client: "", server: "" });
  assert.deepEqual(packageVersions(BUNDLE, { version: "1.7.0" }, { server_version: "1.8.0" }), { client: "1.7.0", server: "1.8.0" });
  rmSync(join(BUNDLE, "VERSION"), { force: true });
});

test("outcome: what a toast says after a sync action", () => {
  stage(false);
  writeFileSync(join(CARL, "client-sync.json"), JSON.stringify({ bundle: BUNDLE, applied: HASH }));
  const check = { id: "check", args: ["once"], busy: "checking…" };
  assert.deepEqual(outcome(check, 0, "opencode"), { ok: true, message: "CARL updated the model list. Restart OpenCode to use it." });
  assert.equal(outcome({ id: "auto-off", args: ["auto", "off"], busy: "turning off…" }, 0, "pi").message, "New configs from the dashboard now wait for you.");
  assert.equal(outcome(check, 1, "pi").ok, false);
  assert.match(outcome(check, -1, "pi").message, /Run the setup again/);
  writeFileSync(join(CARL, "client-sync.json"), JSON.stringify({ bundle: BUNDLE, error: "x" }));
  assert.equal(outcome(check, 0, "pi").ok, false);
  assert.equal(restartNotice("pi"), "CARL updated the model list. Restart Pi to use it.");
  assert.equal(restartNotice("opencode", "CARL turned web search off.", ["opencode", "pi"], true),
               "CARL turned web search off. Restart OpenCode and Pi to use it. Start OpenCode from a new terminal.");
});

// ------------------------------------------------------------------ the switches: carl-sync.py set (a fake)

/** A fake carl-sync.py: it records its arguments (calls.txt), writes the files of plan.json (the setup's work), prints
 * plan.json "out" and exits with "code". */
const FAKE = `import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "calls.txt"), "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\\n")
try:
    plan = json.load(open(os.path.join(here, "plan.json")))
except OSError:
    plan = {}
for path, body in plan.get("files", {}).items():
    with open(path, "w") as f:
        f.write(body)
print(json.dumps(plan.get("out", {})))
sys.exit(plan.get("code", 0))
`;
// the lines written so far (a file just made can still be empty: no call yet)
const calls = () => (existsSync(join(BUNDLE, "calls.txt")) ? readFileSync(join(BUNDLE, "calls.txt"), "utf8").trim().split("\n").filter(Boolean) : []);
function fake(plan) {
  writeFileSync(join(BUNDLE, "carl-sync.py"), FAKE);
  writeFileSync(join(BUNDLE, "plan.json"), JSON.stringify(plan));
  rmSync(join(BUNDLE, "calls.txt"), { force: true });
}

test("a switch runs carl-sync.py set, reads the row again and says what must restart", async () => {
  stage(true);
  fake({ files: { [join(OC, "tui.json")]: JSON.stringify({ plugin: ["file:/x/session-switcher"] }) },
         out: { ok: true, changed: { NO_SIDEBAR: { from: "on", to: "1" } }, restart: ["opencode"], new_terminal: false } });
  const said = await act(get("opencode", "sidebar").actions.off, "opencode");
  assert.deepEqual(calls(), ["set NO_SIDEBAR=1"]);
  assert.deepEqual(said, { ok: true, message: "CARL turned the subagents side panel off. Restart OpenCode to use it." });
  assert.equal(get("opencode", "sidebar").state, "off");
  assert.equal(panel("opencode").title, "Restart OpenCode to use 2 changes.");      // and the config applied since start
  // back as it was: one change less
  fake({ files: { [join(OC, "tui.json")]: JSON.stringify({ plugin: ["file:/x/session-switcher", "file:/x/subagents-sidebar"] }) },
         out: { ok: true, restart: ["opencode"] } });
  assert.equal((await act(get("opencode", "sidebar").actions.on, "opencode")).ok, true);
  assert.equal(panel("opencode").title, "Restart OpenCode to use 1 change.");
});

test("web search: both clients restart, and OpenCode from a new terminal", async () => {
  stage(true);
  fake({ files: { [join(CARL, "opencode.env")]: envFile("parallel") }, out: { ok: true, restart: ["opencode", "pi"], new_terminal: true } });
  const said = await act(get("opencode", "web").actions.parallel, "opencode");
  assert.deepEqual(calls(), ["set WEB_SEARCH=parallel"]);
  assert.equal(said.message, "CARL set web search to parallel. Restart OpenCode and Pi to use it. Start OpenCode from a new terminal.");
});

test("a switch that did not change the part, and a setup that failed, say so", async () => {
  stage(false);
  fake({ out: { ok: true, restart: ["pi"] } });                           // the setup did not turn it on (a fake)
  let said = await act(get("pi", "coder").actions.on, "pi");
  assert.deepEqual(calls(), ["set NO_CODER=on CODER=1"]);
  assert.equal(said.ok, false);
  assert.match(said.message, /still off\. The setup's output is in ~\/\.config\/carl\/client-sync\.log\./);
  fake({ code: 1, out: { ok: false, error: "The installer stopped with an error (exit code 3)." } });
  said = await act(get("pi", "browser").actions.on, "pi");
  assert.deepEqual(said, { ok: false, message: "CARL could not change the setting. The installer stopped with an error (exit code 3)." });
  rmSync(join(BUNDLE, "carl-sync.py"));
  said = await act(get("pi", "browser").actions.on, "pi");
  assert.match(said.message, /cannot find carl-sync\.py/);
});

test("the coder turned on with 1 slot: it is on, and the message is a warning (user, 2026-10-09)", async () => {
  stage(false);
  const coderOn = { [join(OC, "opencode.json")]: JSON.stringify({ agent: { coder: {} } }) };
  fake({ files: coderOn, out: { ok: true, restart: ["opencode"], slots: 1 } });
  let said = await act(get("opencode", "coder").actions.on, "opencode");
  assert.deepEqual(calls(), ["set NO_CODER=on CODER=1"]);
  assert.deepEqual(said, { ok: true, warn: true, message: "CARL turned the coder subagent on. This server runs 1 slot: "
    + "the coder takes the main session's slot while it works. Restart OpenCode to use it." });
  assert.equal(get("opencode", "coder").state, "on");
  // Pi: the same warning
  fake({ files: { [join(PI, "agents", "coder.md")]: "x" }, out: { ok: true, restart: ["pi"], slots: 1 } });
  said = await act(get("pi", "coder").actions.on, "pi");
  assert.equal(said.warn, true);
  assert.match(said.message, /^CARL turned the coder subagent on\. This server runs 1 slot: .* Restart Pi to use it\.$/);
  // 2 slots, or a slot count that is not known (no server answer): a plain success
  for (const slots of [2, null, undefined]) {
    stage(false);
    fake({ files: coderOn, out: { ok: true, restart: ["opencode"], slots } });
    said = await act(get("opencode", "coder").actions.on, "opencode");
    assert.deepEqual(said, { ok: true, message: "CARL turned the coder subagent on. Restart OpenCode to use it." });
  }
  // the coder turned off with 1 slot, or another part: no warning
  stage(true);
  fake({ files: { [join(OC, "opencode.json")]: JSON.stringify({}) }, out: { ok: true, restart: ["opencode"], slots: 1 } });
  said = await act(get("opencode", "coder").actions.off, "opencode");
  assert.deepEqual(said, { ok: true, message: "CARL turned the coder subagent off. Restart OpenCode to use it." });
  // the panel itself: no explanation in the list
  assert.ok(panel("opencode").rows.every((r) => !/slot/.test(`${r.label} ${r.state}`)));
});

test("Coder Thinking: set CODER_THINKING=MODEL:VALUE; the toast has the full-spec sentence; Pi needs no restart", async () => {
  // Pi reads carl.json each time it starts the coder: no restart, and its title does not count it
  stage(true);
  const piBefore = panel("pi").title;
  const piLow = JSON.stringify({ models: { [Q]: { thinking: "effort", main: "low", coder: "low", dashboard: "main" } } });
  const env = (v) => ({ [join(CARL, "client-install.env")]: `CLIENTS=both\n${v ? `CODER_THINKING=${v}\n` : ""}` });
  fake({ files: { [join(PI, "carl.json")]: piLow, ...env(`${Q}:low`) }, out: { ok: true, restart: [] } });
  let said = await act(get("pi", "thinking").actions.low, "pi");
  assert.deepEqual(said, { ok: true, message: `CARL set the coder's thinking with ${Q} to low on this computer.` });
  assert.equal(panel("pi").title, piBefore);
  stage(true);
  const offState = JSON.stringify({ models: { [Q]: { thinking: "effort", main: "low", coder: "off", dashboard: "main" } } });
  fake({ files: { [join(OC, "carl.json")]: offState, ...env(`${Q}:off`) }, out: { ok: true, restart: ["opencode"] } });
  const before = panel("opencode").title;
  said = await act(get("opencode", "thinking").actions.off, "opencode");
  assert.deepEqual(calls(), [`set CODER_THINKING=${Q}:off`]);
  assert.deepEqual(said, { ok: true, message: `CARL set the coder's thinking with ${Q} to off on this computer. ${FULL} `
    + "Restart OpenCode to use it." });
  assert.equal(get("opencode", "thinking").state, "off");
  assert.notEqual(panel("opencode").title, before);                     // OpenCode reads it when it starts
  // dashboard default: the entry goes
  const back = JSON.stringify({ models: { [Q]: { thinking: "effort", main: "low", coder: "main", dashboard: "main" } } });
  fake({ files: { [join(OC, "carl.json")]: back, ...env("") }, out: { ok: true, restart: ["opencode"] } });
  said = await act(get("opencode", "thinking").actions.default, "opencode");
  assert.deepEqual(calls(), [`set CODER_THINKING=${Q}:default`]);
  assert.equal(said.ok, true, said.message);
  assert.equal(get("opencode", "thinking").state, "dashboard default");
  // the setup did not write it: the row says its value in words
  stage(true);
  fake({ out: { ok: true, restart: ["opencode"] } });
  said = await act(get("opencode", "thinking").actions.xhigh, "opencode");
  assert.equal(said.ok, false);
  assert.match(said.message, /the coder's thinking with qwen3\.6-35b-a3b-iq3 is still dashboard default\./);
});

test("router mode: the action reads the row again for the same session's model", async () => {
  stage(true, TWO);
  const g = "gemma-4-e4b";
  const session = { model: `llamacpp/${g}` };
  const after = JSON.stringify({ models: { ...TWO, [g]: { thinking: "on-off", main: "on", coder: "on", dashboard: "off" } } });
  fake({ files: { [join(OC, "carl.json")]: after, [join(CARL, "client-install.env")]: `CODER_THINKING=${g}:on\n` },
         out: { ok: true, restart: ["opencode"] } });
  const said = await act(getIn("opencode", "thinking", session).actions.on, "opencode", session);
  assert.deepEqual(calls(), [`set CODER_THINKING=${g}:on`]);
  assert.equal(said.ok, true, said.message);
  assert.equal(getIn("opencode", "thinking", session).state, "on");
  assert.equal(get("opencode", "thinking").state, "dashboard default");      // the first model has no value of its own
});

// ------------------------------------------------------------------ the front ends

/** A copy of a client's panel folder with carl-panel.js next to it (as the setup installs it), and a small solid-js
 * (OpenCode gives the real one to its plugins). */
function copyPanel(src, file) {
  const dir = mkdtempSync(join(tmpdir(), "carl-panel-ui-"));
  copyFileSync(join(REPO, src, file), join(dir, file));
  copyFileSync(join(REPO, "client/shared/carl-panel.js"), join(dir, "carl-panel.js"));
  mkdirSync(join(dir, "node_modules", "solid-js"), { recursive: true });
  writeFileSync(join(dir, "node_modules", "solid-js", "package.json"), JSON.stringify({ name: "solid-js", type: "module", main: "index.js" }));
  writeFileSync(join(dir, "node_modules", "solid-js", "index.js"),
                "export function createSignal(v) { let x = v; return [() => x, (n) => { x = n; }]; }\n");
  return pathToFileURL(join(dir, file)).href;
}

test("OpenCode: one list of labels and states; Enter changes a row in place; a toast says what CARL did", async () => {
  stage(true);
  fake({ files: { [join(OC, "tui.json")]: JSON.stringify({ plugin: ["file:/x/subagents-sidebar"] }) }, out: { ok: true, restart: ["opencode"] } });
  const plugin = (await import(copyPanel("client/opencode/plugins/carl-panel", "tui.js"))).default;
  const dialogs = [];
  const toasts = [];
  const commands = [];
  const api = {
    ui: { DialogSelect: (props) => ({ kind: "select", props }), DialogAlert: (props) => ({ kind: "alert", props }),
          dialog: { replace: (f) => { dialogs.push(f()); } }, toast: (t) => toasts.push(t) },
    command: { register: (f) => { commands.push(...f()); return () => {}; } },
    lifecycle: { onDispose: () => {} },
  };
  await plugin.tui(api);
  assert.equal(commands[0].slash.name, "carl");
  commands[0].onSelect();
  const main = dialogs.at(-1).props;
  assert.equal(main.title, "CARL");             // this copy started with the config that is applied now
  assert.deepEqual(main.options.map((o) => o.title), panel("opencode").rows.map((r) => r.label));
  assert.deepEqual(main.options.map((o) => o.footer), panel("opencode").rows.map((r) => shownState(r)));
  assert.equal(main.options[0].footer, "on ›");
  assert.ok(main.options.every((o) => !o.description && !o.category));     // no explanations, no headings
  // a switch: no second dialog; the state reads "turning off…" while it runs, then the new state
  main.onSelect({ value: "switcher" });
  assert.equal(dialogs.length, 1);
  const state = () => main.options.find((o) => o.value === "switcher").footer;
  assert.equal(state(), "turning off…");
  for (let i = 0; i < 200 && state() === "turning off…"; i++) await new Promise((r) => setTimeout(r, 25));
  assert.deepEqual(calls(), ["set NO_SWITCHER=1"]);
  assert.deepEqual(toasts, [{ message: "CARL turned the session switcher off. Restart OpenCode to use it.", variant: "success" }]);
  assert.equal(main.options.find((o) => o.value === "switcher").footer, "off");
  assert.equal(main.title, "CARL   Restart OpenCode to use 1 change.");
  // web search: its values, the current one marked, with where the queries go
  main.onSelect({ value: "web" });
  const web = dialogs.at(-1).props;
  assert.equal(web.title, "Web Search");
  assert.equal(web.current, "exa");
  assert.deepEqual(web.options.map((o) => [o.title, o.description]),
                   [["exa", "Queries go to exa.ai."], ["parallel", "Queries go to parallel.ai."], ["off", undefined]]);
  // the sync service: a view, nothing to select
  dialogs.at(-2).props.onSelect({ value: "sync" });
  const view = dialogs.at(-1);
  assert.equal(view.kind, "alert");
  assert.equal(view.props.title, "Config Sync");
  assert.match(view.props.message, /^Sync Service {4}connected$/m);
});

test("OpenCode: the coder turned on with 1 slot gives a warning toast", async () => {
  stage(false);
  fake({ files: { [join(OC, "opencode.json")]: JSON.stringify({ agent: { coder: {} } }) }, out: { ok: true, restart: ["opencode"], slots: 1 } });
  const plugin = (await import(copyPanel("client/opencode/plugins/carl-panel", "tui.js"))).default;
  const dialogs = [];
  const toasts = [];
  const commands = [];
  await plugin.tui({
    ui: { DialogSelect: (props) => ({ kind: "select", props }), DialogAlert: (props) => ({ kind: "alert", props }),
          dialog: { replace: (f) => { dialogs.push(f()); } }, toast: (t) => toasts.push(t) },
    command: { register: (f) => { commands.push(...f()); return () => {}; } },
    lifecycle: { onDispose: () => {} },
  });
  commands[0].onSelect();
  dialogs.at(-1).props.onSelect({ value: "subagent" });          // Coder Subagent: its own list
  const sub = dialogs.at(-1).props;
  assert.equal(sub.title, "Coder Subagent");
  assert.deepEqual(sub.options.map((o) => [o.title, o.footer]), [["Coder", "off"]]);
  sub.onSelect({ value: "coder" });
  for (let i = 0; i < 200 && !toasts.length; i++) await new Promise((r) => setTimeout(r, 25));
  assert.equal(toasts.length, 1);
  assert.equal(toasts[0].variant, "warning");
  assert.match(toasts[0].message, /^CARL turned the coder subagent on\. This server runs 1 slot: /);
  assert.equal(sub.options.find((o) => o.value === "coder").footer, "on");
  assert.deepEqual(sub.options.map((o) => o.value), CODER_ROWS);  // on: its rows appear in the same list
  assert.match(sub.title, /^Coder Subagent {3}Restart OpenCode to use \d changes?\.$/);
});

/** The OpenCode TUI API with fakes: the dialogs shown (each with its onClose), the toasts; the dialog stack as
 * OpenCode 1.18's (one dialog; replace() runs the shown one's onClose; Esc runs it, then empties the stack); a renderer
 * key input; a route and the session state (a session whose newest message has a model). */
function fakeOpenCode({ route = { name: "home" }, sessions = {} } = {}) {
  const dialogs = [];
  const toasts = [];
  const abouts = [];                                                       // a Coder setting's explanation (info)
  const commands = [];
  const listeners = [];
  const stack = [];
  const api = {
    ui: { DialogSelect: (props) => ({ kind: "select", props }), DialogAlert: (props) => ({ kind: "alert", props }),
          dialog: {
            replace: (f, onClose) => {
              for (const d of stack.splice(0)) d.onClose?.();
              const d = f();
              dialogs.push(d);
              stack.push({ d, onClose });
            },
            get depth() { return stack.length; },
          },
          toast: (t) => (t.variant === "info" ? abouts : toasts).push(t) },
    command: { register: (f) => { commands.push(...f()); return () => {}; } },
    lifecycle: { onDispose: () => {} },
    renderer: { keyInput: { prependListener: (_e, f) => listeners.unshift(f), off: () => {} } },
    route: { current: route },
    state: { session: { get: (id) => sessions[id]?.info, messages: (id) => sessions[id]?.messages ?? [] } },
  };
  /** A key as OpenCode handles it: the key input's listeners, then the dialog stack's binding (escape, ctrl+c). */
  const press = (name) => {
    for (const f of listeners) f({ name });
    if (name === "escape" || name === "ctrl+c") {
      const top = stack.at(-1);
      top?.onClose?.();
      stack.splice(stack.length - 1, 1);
    }
  };
  /** A click outside the dialog: OpenCode's dialog.clear(). */
  const clickOutside = () => {
    for (const d of stack.splice(0)) d.onClose?.();
  };
  return { api, dialogs, toasts, abouts, commands, press, clickOutside, stack };
}

const until = async (ok) => {
  for (let i = 0; i < 200 && !ok(); i++) await new Promise((r) => setTimeout(r, 25));
};
const tick = () => new Promise((r) => setTimeout(r, 10));

test("OpenCode: Coder Subagent opens its list; Coder Thinking its values (● the current one, the notes in grey)", async () => {
  stage(true);
  const offState = JSON.stringify({ models: { [Q]: { thinking: "effort", main: "low", coder: "off", dashboard: "main" } } });
  fake({ files: { [join(OC, "carl.json")]: offState, [join(CARL, "client-install.env")]: `CODER_THINKING=${Q}:off\n` },
         out: { ok: true, restart: ["opencode"] } });
  const plugin = (await import(copyPanel("client/opencode/plugins/carl-panel", "tui.js"))).default;
  const { api, dialogs, toasts, commands } = fakeOpenCode();
  await plugin.tui(api);
  commands[0].onSelect();
  const main = dialogs.at(-1).props;
  assert.equal(main.options[0].title, "Coder Subagent");
  assert.equal(main.options[0].footer, "on ›");
  assert.ok(!main.options.some((o) => ["Background Coder", "Delegation Reminder"].includes(o.title)));
  main.onSelect({ value: "subagent" });
  const sub = dialogs.at(-1).props;
  assert.equal(sub.title, "Coder Subagent");
  assert.equal(sub.placeholder, undefined);                               // no search line in a sub-list
  assert.deepEqual(sub.options.map((o) => [o.title, o.footer]), [["Coder", "on"], ["Background Coder", "on"],
    ["Delegation Reminder", "on"], ["Tests", "before code ›"], ["Request Check", "single reminder ›"], ["Free-Form Brief", "per model ›"], ["Coder Loop", "on ›"], ["Coder Thinking", "dashboard default ›"],
    ["Coder Model", "same as main"]]);
  const n = dialogs.length;
  sub.onSelect({ value: "coder-model" });                                 // one value: nothing opens
  assert.equal(dialogs.length, n);
  sub.onSelect({ value: "thinking" });
  const values = dialogs.at(-1).props;
  assert.equal(values.title, `Coder Thinking with ${Q}`);
  assert.equal(values.current, "default");                                // OpenCode draws the ● there
  assert.deepEqual(values.options.map((o) => [o.title, o.value, o.description]), [
    ["dashboard default", "default", "same as main (low)"], ["same as main", "main", "the default model's thinking (low)"],
    ["off", "off", undefined], ["low", "low", undefined], ["medium", "medium", undefined], ["xhigh", "xhigh", undefined]]);
  values.onSelect({ value: "off" });
  const back = dialogs.at(-1).props;                                      // back in the Coder Subagent list
  const state = () => back.options.find((o) => o.value === "thinking").footer;
  assert.equal(state(), "turning off…");
  await until(() => toasts.length);
  assert.deepEqual(calls(), [`set CODER_THINKING=${Q}:off`]);
  assert.deepEqual(toasts, [{ message: `CARL set the coder's thinking with ${Q} to off on this computer. ${FULL} `
    + "Restart OpenCode to use it.", variant: "success" }]);
  assert.equal(state(), "off ›");
  assert.match(back.title, /^Coder Subagent {3}Restart OpenCode to use \d changes?\.$/);
});

test("OpenCode: Coder Thinking is for the model of the session /carl is opened in (its main session)", async () => {
  stage(true, TWO);
  fake({ out: { ok: true, restart: ["opencode"] } });
  const plugin = (await import(copyPanel("client/opencode/plugins/carl-panel", "tui.js"))).default;
  const g = "gemma-4-e4b";
  const sessions = {
    child: { info: { id: "child", parentID: "root" }, messages: [{ role: "user", model: { providerID: "llamacpp", modelID: Q } }] },
    root: { info: { id: "root" }, messages: [
      { role: "user", model: { providerID: "llamacpp", modelID: Q } },
      { role: "assistant", providerID: "llamacpp", modelID: Q },
      { role: "user", model: { providerID: "llamacpp", modelID: g } }] },   // the user picked another model since
  };
  const { api, dialogs, commands } = fakeOpenCode({ route: { name: "session", params: { sessionID: "child" } }, sessions });
  await plugin.tui(api);
  commands[0].onSelect();
  dialogs.at(-1).props.onSelect({ value: "subagent" });
  const sub = dialogs.at(-1).props;
  assert.equal(sub.options.find((o) => o.value === "thinking").footer, "dashboard default ›");
  sub.onSelect({ value: "thinking" });
  const values = dialogs.at(-1).props;
  assert.equal(values.title, `Coder Thinking with ${g}`);
  assert.deepEqual(values.options.map((o) => [o.title, o.description]), [["dashboard default", "off"],
    ["same as main", "the main session's thinking (on)"], ["off", undefined], ["on", undefined]]);
  values.onSelect({ value: "on" });
  await until(() => calls().length);
  assert.deepEqual(calls(), [`set CODER_THINKING=${g}:on`]);
  // the home screen: the config's default model, and the note says so
  const home = fakeOpenCode();
  await plugin.tui(home.api);
  home.commands[0].onSelect();
  home.dialogs.at(-1).props.onSelect({ value: "subagent" });
  home.dialogs.at(-1).props.onSelect({ value: "thinking" });
  assert.equal(home.dialogs.at(-1).props.title, `Coder Thinking with ${Q}`);
  assert.equal(home.dialogs.at(-1).props.options[1].description, "the default model's thinking (low)");
});

test("OpenCode: Esc goes back one list; Esc on the top list, ctrl+c and a click outside close /carl", async () => {
  stage(true);
  fake({ out: { ok: true, restart: ["opencode"] } });
  const plugin = (await import(copyPanel("client/opencode/plugins/carl-panel", "tui.js"))).default;
  const { api, dialogs, commands, press, clickOutside, stack } = fakeOpenCode();
  await plugin.tui(api);
  const title = () => dialogs.at(-1).props.title;
  commands[0].onSelect();
  dialogs.at(-1).props.onSelect({ value: "subagent" });
  dialogs.at(-1).props.onSelect({ value: "thinking" });
  assert.equal(title(), `Coder Thinking with ${Q}`);
  press("escape");                                                        // the values: back to Coder Subagent
  assert.equal(stack.length, 0);
  await tick();
  assert.deepEqual([title(), stack.length], ["Coder Subagent", 1]);
  dialogs.at(-1).props.onSelect({ value: "thinking" });                   // and again: the parent's parent next
  press("escape");
  await tick();
  press("escape");
  await tick();
  assert.deepEqual([title(), stack.length], ["CARL", 1]);
  const shown = dialogs.length;
  press("escape");                                                        // the top list: /carl closes
  await tick();
  assert.deepEqual([dialogs.length, stack.length], [shown, 0]);
  // the sync view goes back to its list too
  commands[0].onSelect();
  dialogs.at(-1).props.onSelect({ value: "sync" });
  assert.equal(dialogs.at(-1).kind, "alert");
  press("escape");
  await tick();
  assert.deepEqual([title(), stack.length], ["CARL", 1]);
  // ctrl+c and a click outside close it from any list, as OpenCode's own dialogs
  dialogs.at(-1).props.onSelect({ value: "subagent" });
  press("ctrl+c");
  await tick();
  assert.equal(stack.length, 0);
  commands[0].onSelect();
  dialogs.at(-1).props.onSelect({ value: "subagent" });
  clickOutside();
  await tick();
  assert.equal(stack.length, 0);
  // another dialog opened in between (OpenCode's own): /carl does not come back over it
  commands[0].onSelect();
  dialogs.at(-1).props.onSelect({ value: "subagent" });
  press("escape");
  api.ui.dialog.replace(() => ({ kind: "other" }));
  await tick();
  assert.deepEqual([dialogs.at(-1).kind, stack.length], ["other", 1]);
});

/** Pi's packages as small fakes with the parts /carl uses (as the real SettingsList: Enter or Space activates a row,
 * a submenu opens or the value goes to the next one; Esc cancels; ↓ moves), in a copied panel folder. */
function fakePi(dir) {
  const pkg = (name, code) => {
    const d = join(dir, "node_modules", "@earendil-works", name);
    mkdirSync(d, { recursive: true });
    writeFileSync(join(d, "package.json"), JSON.stringify({ name: `@earendil-works/${name}`, type: "module", main: "index.js" }));
    writeFileSync(join(d, "index.js"), code);
  };
  pkg("pi-coding-agent", "export const getSelectListTheme = () => ({});\nexport const getSettingsListTheme = () => ({});\n");
  // pi-ai's getSupportedThinkingLevels: off only for a model that does not reason, else the levels it maps
  pkg("pi-ai", "export const getSupportedThinkingLevels = (m) => (m.reasoning ? [\"off\", \"minimal\", \"low\", \"medium\", \"high\"] : [\"off\"]);\n");
  pkg("pi-tui", String.raw`export const Key = { escape: "\x1b" };
export const matchesKey = (data, key) => data === key;
export class Text { constructor(t) { this.text = t; } setText(t) { this.text = t; } render() { return [this.text]; } }
export class Spacer { render() { return [""]; } }
export class Container {
  constructor() { this.children = []; }
  clear() { this.children = []; }
  addChild(c) { this.children.push(c); }
  render(w) { return this.children.flatMap((c) => c.render(w)); }
  invalidate() {}
}
export class SelectList {
  constructor(items) { this.items = items; this.sel = 0; }
  setSelectedIndex(i) { this.sel = i; }
  render() { return this.items.map((x, i) => (i === this.sel ? "→ " : "  ") + x.label + (x.description ? "  " + x.description : "")); }
  handleInput(d) {
    if (d === "\x1b[B") this.sel = Math.min(this.sel + 1, this.items.length - 1);
    else if (d === "\r") this.onSelect?.(this.items[this.sel]);
    else if (d === "\x1b") this.onCancel?.();
  }
}
export class SettingsList {
  constructor(items, max, theme, onChange, onCancel) { Object.assign(this, { items, onChange, onCancel, sel: 0, sub: null }); }
  updateValue(id, v) { const it = this.items.find((x) => x.id === id); if (it) it.currentValue = v; }
  selectItem(id) { const i = this.items.findIndex((x) => x.id === id); if (i >= 0) this.sel = i; }
  render(w) {
    return this.sub ? this.sub.render(w) : this.items.map((x, i) => (i === this.sel ? "→ " : "  ") + x.label + "  " + x.currentValue);
  }
  handleInput(d) {
    if (this.sub) return this.sub.handleInput(d);
    if (d === "\x1b[B") this.sel = (this.sel + 1) % this.items.length;
    else if (d === "\x1b") this.onCancel();
    else if (d === "\r" || d === " ") {
      const it = this.items[this.sel];
      if (it.submenu) {
        this.sub = it.submenu(it.currentValue, (v) => {
          this.sub = null;
          if (v !== undefined) { it.currentValue = v; this.onChange(it.id, v); }
        });
      } else if (it.values?.length) {
        const v = it.values[(it.values.indexOf(it.currentValue) + 1) % it.values.length];
        it.currentValue = v;
        this.onChange(it.id, v);
      }
    }
  }
}
`);
}

test("Pi: Pi's settings list; Coder Subagent opens its list in place (Esc back); Coder Thinking its values", async () => {
  stage(true, TWO);
  const g = "gemma-4-e4b";
  const offState = JSON.stringify({ models: { ...TWO, [g]: { thinking: "on-off", main: "on", coder: "off", dashboard: "off" } } });
  fake({ files: { [join(PI, "carl.json")]: offState, [join(CARL, "client-install.env")]: `CODER_THINKING=${g}:off\n` },
         out: { ok: true, restart: ["opencode"] } });
  const url = copyPanel("client/pi/extensions/carl-panel", "index.ts");
  fakePi(dirname(fileURLToPath(url)));
  const ext = (await import(url)).default;
  const commands = {};
  ext({ on: () => {}, registerCommand: (name, c) => { commands[name] = c; } });
  const notes = [];
  let comp;
  let closed = false;
  const ctx = { mode: "tui", model: { provider: "llamacpp", id: g }, ui: { notify: (m, k) => notes.push([m, k]),
    custom: (factory) => new Promise((done) => {
      comp = factory({ requestRender: () => {} }, { fg: (_c, t) => t, bold: (t) => t }, {}, () => { closed = true; done(); });
    }) } };
  const ended = commands.carl.handler("", ctx);
  const screen = () => comp.render(80);
  const key = (...ks) => ks.forEach((k) => comp.handleInput(k));
  const DOWN = "\x1b[B";
  assert.equal(screen()[0], "CARL");
  assert.equal(screen()[2], "→ Coder Subagent  on ›");
  key("\r");                                                               // opens the list in the same place
  assert.deepEqual(screen(), ["Coder Subagent", "", "→ Coder  on", "  Background Coder  on", "  Delegation Reminder  on",
                              "  Tests  before code ›", "  Request Check  single reminder ›", "  Free-Form Brief  per model ›", "  Coder Loop  on ›", "  Coder Thinking  dashboard default ›", "  Coder Model  same as main"]);
  key(DOWN, DOWN, DOWN, DOWN, DOWN, DOWN, DOWN, DOWN, "\r");                           // Coder Model: one value, nothing happens
  assert.equal(screen()[0], "Coder Subagent");
  assert.ok(screen().includes("→ Coder Model  same as main"), screen().join("\n"));
  assert.deepEqual(calls(), []);
  key(DOWN, DOWN, DOWN, DOWN, DOWN, DOWN, DOWN, DOWN, "\r");             // around to Coder Thinking: its values
  assert.equal(screen()[0], `Coder Thinking with ${g}\nHow much the coder thinks with this model. Off suits a full spec only.`);   // the session's model; the explanation under the title
  assert.deepEqual(screen().slice(2), ["→ dashboard default  off", "  same as main  the main session's thinking (on)",
                                       "  off", "  on"]);
  key(DOWN, DOWN, "\r");                                                   // off
  await until(() => notes.length);
  assert.deepEqual(calls(), [`set CODER_THINKING=${g}:off`]);
  assert.deepEqual(notes, [[`CARL set the coder's thinking with ${g} to off on this computer. ${FULL} `
    + "Restart OpenCode to use it.", "info"]]);
  await until(() => screen().includes("→ Coder Thinking  off"));
  assert.equal(screen()[0], "Coder Subagent");                            // back in its list, with the new value
  assert.ok(screen().includes("→ Coder Thinking  off ›"), screen().join("\n"));
  key("\x1b");                                                             // Esc: back to the top list
  assert.equal(screen()[0], "CARL");
  assert.equal(screen()[2], "→ Coder Subagent  on ›");
  key("\x1b");
  await ended;
  assert.ok(closed);
});

// ------------------------------------------------------------------ Phase 23.4.5: the coder on another endpoint

/** OpenCode's state.provider as the TUI has it (made-up providers; each with a key that CARL must never read). */
const OC_PROVIDERS = [
  { id: "llamacpp", name: "CARL", key: "carl-key", options: { baseURL: "http://192.168.42.1:8080/v1", apiKey: "carl-key" },
    models: { [Q]: { id: Q, api: { url: "" }, cost: { input: 0, output: 0, cache: { read: 0, write: 0 } }, variants: {} } } },
  { id: "zen", name: "OpenCode Zen", key: "sk-zen-secret", options: { apiKey: "sk-zen-secret" },
    models: { "free-coder-1": { id: "free-coder-1", api: { url: "https://opencode.ai/zen/v1" },
                                cost: { input: 0, output: 0, cache: { read: 0, write: 0 } } } } },
  { id: "openrouter", name: "OpenRouter", key: "sk-or-secret", options: { apiKey: "sk-or-secret" },
    models: { "example-coder-32b": { id: "example-coder-32b", api: { url: "https://openrouter.ai/api/v1" },
                                     cost: { input: 0.2, output: 0.6, cache: { read: 0, write: 0 } },
                                     variants: { low: { reasoningEffort: "low" }, high: { reasoningEffort: "high" } } } } },
  { id: "myprovider", name: "My provider", options: { baseURL: "https://api.example.com/v1", apiKey: "sk-mine" },
    models: { "big-model": { id: "big-model", api: { url: "" }, cost: { input: 1, output: 2 } } } },
];
/** Pi's modelRegistry.getAvailable() (made-up models; CARL's provider among them). */
const PI_MODELS = [
  { provider: "llamacpp", id: Q, baseUrl: "http://192.168.42.1:8080/v1", reasoning: true,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } },
  { provider: "opencode", id: "free-coder-1", baseUrl: "https://opencode.ai/zen/v1", reasoning: false,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } },
  { provider: "openrouter", id: "example-coder-32b", baseUrl: "https://openrouter.ai/api/v1", reasoning: true,
    cost: { input: 0.2, output: 0.6, cacheRead: 0, cacheWrite: 0 } },
  { provider: "custom", id: "no-price", baseUrl: "http://10.0.0.5:9000/v1", reasoning: false },
];
const OR = "openrouter/example-coder-32b";
const piLevels = (m) => (m.reasoning ? ["off", "minimal", "low", "medium", "high"] : ["off"]);
const OC_SESSION = { external: openCodeModels(OC_PROVIDERS) };
const PI_SESSION = { external: piModels(PI_MODELS, piLevels) };
/** OpenCode's configs after the setup wrote an external coder model (the agent's model, carl-delegation's options). */
const ocWith = (model = "", variant = "") => JSON.stringify({
  plugin: ["file:/x/carl-cache", ["file:/x/carl-model-check", {}],
           ["file:/x/carl-delegation", { reminder: true, ...(model ? { coderModel: model } : {}), ...(variant ? { coderVariant: variant } : {}) }]],
  agent: { coder: model ? { model } : {} }, mcp: { "carl-browser": {} }, lsp: true });
/** Pi's carl.json after the setup wrote one (coder_model; its level in "thinking"). */
const piWith = (model = "main", level = "") => JSON.stringify({
  background_subagents: true, delegation: { reminder: true }, models: ONE, coder_model: model,
  ...(level ? { thinking: { coder: { [model]: level } } } : {}) });

test("the external models: OpenCode's providers and Pi's registry, each with its host and its cost; never a key", () => {
  assert.deepEqual(openCodeModels(OC_PROVIDERS), [
    { ref: `llamacpp/${Q}`, host: "192.168.42.1", cost: "free", levels: [] },
    { ref: "zen/free-coder-1", host: "opencode.ai", cost: "free", levels: [] },
    { ref: OR, host: "openrouter.ai", cost: "paid", levels: ["low", "high"] },
    { ref: "myprovider/big-model", host: "api.example.com", cost: "paid", levels: [] }]);
  assert.deepEqual(piModels(PI_MODELS, piLevels).slice(1), [
    { ref: "opencode/free-coder-1", host: "opencode.ai", cost: "free", levels: ["off"] },
    { ref: OR, host: "openrouter.ai", cost: "paid", levels: ["off", "minimal", "low", "medium", "high"] },
    { ref: "custom/no-price", host: "10.0.0.5", cost: "", levels: ["off"] }]);              // no cost info: the host only
  // a provider with no URL (the SDK's own): its name; ids with / and : (openrouter's) are kept whole
  assert.deepEqual(openCodeModels([{ id: "anthropic", name: "Anthropic", models: { "m-1": { id: "m-1", api: {}, cost: { input: 3, output: 15 } } } },
                                   { id: "openrouter", models: { "qwen/qwen3-coder:free": { id: "qwen/qwen3-coder:free", api: { url: "https://openrouter.ai/api/v1" }, cost: { input: 0, output: 0 } } } }]),
                   [{ ref: "anthropic/m-1", host: "Anthropic", cost: "paid", levels: [] },
                    { ref: "openrouter/qwen/qwen3-coder:free", host: "openrouter.ai", cost: "free", levels: [] }]);
  assert.deepEqual([costOf(undefined), costOf({ input: 0 }), costOf({ input: 0, output: 0, cache: { read: 0.1 } })], ["", "", "paid"]);
  assert.deepEqual([hostOf("not a url"), hostOf(3), hostOf("https://x.example:8443/v1")], ["", "", "x.example"]);
  assert.deepEqual(openCodeModels(undefined), []);
  assert.deepEqual(piModels([{ provider: "a b", id: "x" }, null], piLevels), []);              // not PROVIDER/MODEL
  // the keys: never read (a provider whose key fields throw when read), never in the result
  const watched = OC_PROVIDERS.map((p) => new Proxy({ ...p, options: new Proxy(p.options, { get(t, k) {
    if (k === "apiKey") throw new Error("CARL read options.apiKey");
    return t[k];
  } }) }, { get(t, k) {
    if (k === "key") throw new Error("CARL read the provider's key");
    return t[k];
  } }));
  const got = openCodeModels(watched);
  assert.equal(got.length, 4);
  assert.doesNotMatch(JSON.stringify(got), /secret|sk-mine|carl-key/);
  const piWatched = PI_MODELS.map((m) => new Proxy(m, { get(t, k) {
    if (k === "apiKey" || k === "headers") throw new Error(`CARL read the model's ${String(k)}`);
    return t[k];
  } }));
  assert.equal(piModels(piWatched, piLevels).length, 4);
});

test("Coder Model: same as main, then this client's models with where they go; CARL's own are left out (Phase 23.4.5)", () => {
  stage(true);
  for (const [client, session, first] of [["opencode", OC_SESSION, "zen/free-coder-1"], ["pi", PI_SESSION, "opencode/free-coder-1"]]) {
    const r = getIn(client, "coder-model", session);
    assert.deepEqual([r.kind, r.state, r.value], ["choice", "same as main", "main"]);
    assert.equal(shownState(r), "same as main ›");                                       // it opens its values now
    assert.equal(r.values[0], "main");
    assert.equal(r.values[1], first);
    assert.ok(!r.values.some((v) => v.startsWith("llamacpp/")), r.values.join());           // CARL's models: Phase 29
    assert.equal(r.notes.main, `CARL: ${Q}`);
    assert.equal(r.notes[OR], "openrouter.ai, paid");
    assert.deepEqual(r.titles, { main: "same as main" });
    const a = r.actions[OR];
    assert.deepEqual(a.args, ["set", `CODER_MODEL=${OR}`]);
    assert.deepEqual([a.busy, a.row, a.want, a.live], [`switching to ${OR}…`, "coder-model", OR, ["pi"]]);
    assert.equal(a.did, `CARL set the coder's model to ${OR} on this computer. The coder's work goes to openrouter.ai, `
      + "and the model costs money.");
    assert.equal(r.actions[first].did, `CARL set the coder's model to ${first} on this computer. The coder's work goes `
      + "to opencode.ai.");                                                                 // free: no cost sentence
  }
  assert.deepEqual(getIn("opencode", "coder-model", OC_SESSION).notes, { main: `CARL: ${Q}`, "zen/free-coder-1": "opencode.ai, free",
    [OR]: "openrouter.ai, paid", "myprovider/big-model": "api.example.com, paid" });
  assert.equal(getIn("pi", "coder-model", PI_SESSION).notes["custom/no-price"], "10.0.0.5");
  // CARL's provider under its other id ("carl", next to a user's own "llamacpp"): left out; the user's "llamacpp" stays
  writeFileSync(join(OC, "carl.json"), JSON.stringify({ providers: { llamacpp: "carl" }, models: ONE }));
  const mine = { external: openCodeModels([{ ...OC_PROVIDERS[0], id: "carl" }, OC_PROVIDERS[0]]) };
  assert.deepEqual(getIn("opencode", "coder-model", mine).values, ["main", `llamacpp/${Q}`]);
});

test("Coder Model: a choice runs carl-sync.py set CODER_MODEL; the toast says where the work goes, as the mock-up", async () => {
  // Pi reads carl.json each time it starts the coder: no restart, and its title does not count it
  stage(true);
  const before = panel("pi", PI_SESSION).title;
  fake({ files: { [join(PI, "carl.json")]: piWith("opencode/free-coder-1") }, out: { ok: true, restart: [] } });
  const pi = await act(getIn("pi", "coder-model", PI_SESSION).actions["opencode/free-coder-1"], "pi", PI_SESSION);
  assert.deepEqual(pi, { ok: true, message: "CARL set the coder's model to opencode/free-coder-1 on this computer. "
    + "The coder's work goes to opencode.ai." });
  assert.equal(panel("pi", PI_SESSION).title, before);
  assert.equal(getIn("pi", "coder-model", PI_SESSION).state, "opencode/free-coder-1");
  stage(true);
  fake({ files: { [join(OC, "opencode.json")]: ocWith(OR), [join(CARL, "client-install.env")]: `CLIENTS=both\nCODER_MODEL=${OR}\n` },
         out: { ok: true, restart: ["opencode"], slots: 1 } });
  const said = await act(getIn("opencode", "coder-model", OC_SESSION).actions[OR], "opencode", OC_SESSION);
  assert.deepEqual(calls(), [`set CODER_MODEL=${OR}`]);
  assert.deepEqual(said, { ok: true, message: `CARL set the coder's model to ${OR} on this computer. The coder's work goes `
    + "to openrouter.ai, and the model costs money. Restart OpenCode to use it." });     // 1 slot: no warning (no slot used)
  const sub = rowsAt(panel("opencode", OC_SESSION).rows, ["subagent"]);
  assert.deepEqual(sub.map((r) => [r.label, shownState(r)]), [["Coder", "on"], ["Background Coder", "on"],
    ["Delegation Reminder", "on"], ["Tests", "before code ›"], ["Request Check", "single reminder ›"], ["Free-Form Brief", "per model ›"], ["Coder Loop", "on ›"], ["Coder Thinking", "model default ›"], ["Coder Model", `${OR} ›`]]);
  assert.match(panel("opencode", OC_SESSION).title, /^Restart OpenCode to use \d+ changes?\.$/);   // (1 in the TUI test)
  // back to same as main: the coder stays on (CODER=1); with 1 slot, the warning of the coder on the server
  fake({ files: { [join(OC, "opencode.json")]: ocWith() }, out: { ok: true, restart: ["opencode"], slots: 1 } });
  const back = await act(getIn("opencode", "coder-model", OC_SESSION).actions.main, "opencode", OC_SESSION);
  assert.deepEqual(calls(), ["set CODER_MODEL=main CODER=1"]);
  assert.deepEqual(back, { ok: true, warn: true, message: "CARL set the coder's model to same as main on this computer. "
    + "This server runs 1 slot: the coder takes the main session's slot while it works. Restart OpenCode to use it." });
  // the setup did not write it: the row says so
  stage(true);
  fake({ out: { ok: true, restart: ["opencode"] } });
  const not = await act(getIn("opencode", "coder-model", OC_SESSION).actions[OR], "opencode", OC_SESSION);
  assert.equal(not.ok, false);
  assert.match(not.message, /the coder's model is still same as main\./);
});

test("an external coder uses no slot: turning the coder on gives no 1-slot warning (Phase 23.4.5)", async () => {
  stage(false);
  writeFileSync(join(PI, "carl.json"), piWith(OR));                 // the coder's model is external already
  fake({ files: { [join(PI, "agents", "coder.md")]: "x" }, out: { ok: true, restart: ["pi"], slots: 1 } });
  const said = await act(get("pi", "coder").actions.on, "pi", PI_SESSION);
  assert.deepEqual(said, { ok: true, message: "CARL turned the coder subagent on. Restart Pi to use it." });
});

test("Coder Thinking with an external model: model default, then the levels the client knows for it", async () => {
  stage(true);
  writeFileSync(join(OC, "opencode.json"), ocWith(OR));
  writeFileSync(join(CARL, "client-install.env"), `CLIENTS=both\nCODER_MODEL=${OR}\nCODER_THINKING=${Q}:off\n`);
  let r = getIn("opencode", "thinking", OC_SESSION);
  assert.deepEqual([r.state, r.value, r.values, r.choiceTitle], ["model default", "default", ["default", "low", "high"],
                                                                 `Coder Thinking with ${OR}`]);   // CARL's table: not used
  assert.deepEqual([r.titles, r.notes], [{ default: "model default" }, {}]);
  const high = r.actions.high;
  assert.deepEqual(high.args, ["set", "CODER_MODEL_THINKING=high"]);
  assert.deepEqual([high.busy, high.row, high.want, high.live], ["switching to high…", "thinking", "high", ["pi"]]);
  assert.equal(high.did, `CARL set the coder's thinking with ${OR} to high on this computer.`);
  // the variant the setup wrote (carl-delegation's coderVariant)
  writeFileSync(join(OC, "opencode.json"), ocWith(OR, "high"));
  r = getIn("opencode", "thinking", OC_SESSION);
  assert.deepEqual([r.state, Object.keys(r.actions)], ["high", ["default", "low"]]);
  assert.deepEqual(r.actions.default.args, ["set", "CODER_MODEL_THINKING=default"]);
  assert.equal(r.actions.default.did, `CARL set the coder's thinking with ${OR} to model default on this computer.`);
  // Pi: its thinking levels; off says to use a full spec
  writeFileSync(join(PI, "carl.json"), piWith(OR, "low"));
  r = getIn("pi", "thinking", PI_SESSION);
  assert.deepEqual([r.state, r.values], ["low", ["default", "off", "minimal", "low", "medium", "high"]]);
  assert.equal(r.actions.off.did, `CARL set the coder's thinking with ${OR} to off on this computer. ${FULL}`);
  // a model the client does not know (now): model default only, and nothing opens
  r = getIn("pi", "thinking", {});
  assert.deepEqual([r.values, shownState(r)], [["default", "low"], "low ›"]);
  writeFileSync(join(PI, "carl.json"), piWith(OR));
  assert.equal(shownState(getIn("pi", "thinking", {})), "model default");
});

test("Coder Model: the current model stays in the list when this client does not list it, with a note", () => {
  stage(true);
  writeFileSync(join(PI, "carl.json"), piWith("zen/free-coder-1"));      // chosen in OpenCode: Pi calls it opencode/...
  const r = getIn("pi", "coder-model", PI_SESSION);
  assert.deepEqual(r.values.slice(0, 2), ["main", "zen/free-coder-1"]);
  assert.equal(r.notes["zen/free-coder-1"], "Pi does not list this model.");
  assert.equal(r.state, "zen/free-coder-1");
  assert.deepEqual(Object.keys(r.actions).includes("zen/free-coder-1"), false);
});

test("OpenCode: Coder Model's values come from OpenCode's providers (● same as main, the notes in grey)", async () => {
  stage(true);
  fake({ files: { [join(OC, "opencode.json")]: ocWith(OR) }, out: { ok: true, restart: ["opencode"] } });
  const plugin = (await import(copyPanel("client/opencode/plugins/carl-panel", "tui.js"))).default;
  const { api, dialogs, toasts, commands } = fakeOpenCode();
  api.state.provider = OC_PROVIDERS;
  await plugin.tui(api);
  commands[0].onSelect();
  dialogs.at(-1).props.onSelect({ value: "subagent" });
  const sub = dialogs.at(-1).props;
  assert.deepEqual(sub.options.at(-1), { title: "Coder Model", value: "coder-model", footer: "same as main ›" });
  sub.onSelect({ value: "coder-model" });
  const values = dialogs.at(-1).props;
  assert.equal(values.title, "Coder Model");
  assert.equal(values.current, "main");
  assert.deepEqual(values.options.map((o) => [o.title, o.description]), [["same as main", `CARL: ${Q}`],
    ["zen/free-coder-1", "opencode.ai, free"], [OR, "openrouter.ai, paid"], ["myprovider/big-model", "api.example.com, paid"]]);
  values.onSelect({ value: OR });
  await until(() => toasts.length);
  assert.deepEqual(calls(), [`set CODER_MODEL=${OR}`]);
  assert.deepEqual(toasts, [{ message: `CARL set the coder's model to ${OR} on this computer. The coder's work goes to `
    + "openrouter.ai, and the model costs money. Restart OpenCode to use it.", variant: "success" }]);
  const back = dialogs.at(-1).props;
  assert.equal(back.title, "Coder Subagent   Restart OpenCode to use 1 change.");
  assert.deepEqual(back.options.slice(-2).map((o) => o.footer), ["model default ›", `${OR} ›`]);
});

test("Pi: Coder Model's values come from Pi's model registry; a choice says where the work goes", async () => {
  stage(true);
  fake({ files: { [join(PI, "carl.json")]: piWith(OR) }, out: { ok: true, restart: [] } });
  const url = copyPanel("client/pi/extensions/carl-panel", "index.ts");
  fakePi(dirname(fileURLToPath(url)));
  const ext = (await import(url)).default;
  const commands = {};
  ext({ on: () => {}, registerCommand: (name, c) => { commands[name] = c; } });
  const notes = [];
  let comp;
  const ctx = { mode: "tui", model: { provider: "llamacpp", id: Q }, modelRegistry: { getAvailable: () => PI_MODELS },
    ui: { notify: (m, k) => notes.push([m, k]), custom: (factory) => new Promise((done) => {
      comp = factory({ requestRender: () => {} }, { fg: (_c, t) => t, bold: (t) => t }, {}, () => done());
    }) } };
  const ended = commands.carl.handler("", ctx);
  const screen = () => comp.render(80);
  const key = (...ks) => ks.forEach((k) => comp.handleInput(k));
  const DOWN = "\x1b[B";
  key("\r");
  assert.equal(screen().at(-1), "  Coder Model  same as main ›");
  key(DOWN, DOWN, DOWN, DOWN, DOWN, DOWN, DOWN, DOWN, "\r");
  assert.equal(screen()[0], "Coder Model\nThe model the coder runs on. Another provider's model gets the coder's work.");
  assert.deepEqual(screen().slice(2), [`→ same as main  CARL: ${Q}`, "  opencode/free-coder-1  opencode.ai, free",
                                       `  ${OR}  openrouter.ai, paid`, "  custom/no-price  10.0.0.5"]);
  key(DOWN, DOWN, "\r");
  await until(() => notes.length);
  assert.deepEqual(calls(), [`set CODER_MODEL=${OR}`]);
  assert.deepEqual(notes, [[`CARL set the coder's model to ${OR} on this computer. The coder's work goes to openrouter.ai, `
    + "and the model costs money.", "info"]]);
  await until(() => screen().some((l) => l.includes(`Coder Model  ${OR}`)));
  assert.ok(screen().includes("  Coder Thinking  model default ›"), screen().join("\n"));
  key("\x1b", "\x1b");
  await ended;
});

// ------------------------------------------------------------------ Tests (Phase 23.4.6)

test("Tests: before code (the default), after code, off; set CODER_TESTS; no restart in either client", async () => {
  stage(true);
  for (const client of ["opencode", "pi"]) {
    const r = get(client, "tests");
    assert.deepEqual([r.kind, r.state, r.value, r.label], ["choice", "before code", "before", "Tests"]);
    assert.equal(shownState(r), "before code ›");
    assert.deepEqual(r.values, ["before", "after", "off"]);
    assert.deepEqual(r.titles, { before: "before code", after: "after code", off: "off" });
    assert.deepEqual(r.notes, {});                                                       // no explanations
    assert.deepEqual(Object.keys(r.actions), ["after", "off"]);
    const a = r.actions.after;
    assert.deepEqual([a.args, a.busy, a.row, a.want, a.live], [["set", "CODER_TESTS=after"], "switching to after code…", "tests",
                                                                "after", ["opencode", "pi"]]);
    assert.equal(a.did, "CARL set the coder's tests to after code on this computer.");
  }
  // the state file's value; a value that is not one: before
  writeFileSync(join(OC, "carl.json"), JSON.stringify({ models: ONE, coder_tests: "off" }));
  assert.deepEqual(Object.keys(get("opencode", "tests").actions), ["before", "after"]);
  assert.equal(get("opencode", "tests").state, "off");
  writeFileSync(join(OC, "carl.json"), JSON.stringify({ models: ONE, coder_tests: "never" }));
  assert.equal(get("opencode", "tests").state, "before code");
  // the toast as the mock-up; the title has no restart note (the chain reads it at each task)
  for (const [client, file] of [["opencode", join(OC, "carl.json")], ["pi", join(PI, "carl.json")]]) {
    stage(true);
    const st = JSON.parse(readFileSync(file, "utf8"));
    const title = panel(client).title;                                                 // earlier tests' changes
    fake({ files: { [file]: JSON.stringify({ ...st, coder_tests: "after" }) }, out: { ok: true, restart: [] } });
    const said = await act(get(client, "tests").actions.after, client);
    assert.deepEqual(calls(), ["set CODER_TESTS=after"]);
    assert.deepEqual(said, { ok: true, message: "CARL set the coder's tests to after code on this computer." });
    assert.equal(get(client, "tests").state, "after code");
    assert.equal(panel(client).title, title);                                          // not counted
  }
  stage(false);
  assert.ok(!subIds("opencode").includes("tests"));                                    // the coder off: the row goes
});

test("Request Check (the 23.4.3 addendum): single reminder (the default), on, off; set CODER_REQUEST_CHECK; no restart", async () => {
  stage(true);
  for (const client of ["opencode", "pi"]) {
    const r = get(client, "request");
    assert.deepEqual([r.kind, r.label, r.state, r.value], ["choice", "Request Check", "single reminder", "reminder"]);
    assert.equal(shownState(r), "single reminder ›");
    assert.deepEqual(r.values, ["on", "reminder", "off"]);
    assert.deepEqual(r.titles, { on: "on", reminder: "single reminder", off: "off" });
    assert.deepEqual(r.notes, {});
    const a = r.actions.on;
    assert.deepEqual([a.args, a.row, a.want, a.live], [["set", "CODER_REQUEST_CHECK=on"], "request", "on", ["opencode", "pi"]]);
    assert.equal(a.did, "CARL set the request check to on on this computer.");
    assert.equal(r.actions.off.busy, "turning off…");
  }
  for (const [client, file] of [["opencode", join(OC, "carl.json")], ["pi", join(PI, "carl.json")]]) {
    stage(true);
    const st = JSON.parse(readFileSync(file, "utf8"));
    const title = panel(client).title;
    fake({ files: { [file]: JSON.stringify({ ...st, request_check: "off" }) }, out: { ok: true, restart: [] } });
    const said = await act(get(client, "request").actions.off, client);
    assert.deepEqual(calls(), ["set CODER_REQUEST_CHECK=off"]);
    assert.deepEqual(said, { ok: true, message: "CARL set the request check to off on this computer." });
    assert.equal(get(client, "request").state, "off");
    assert.equal(panel(client).title, title);
  }
  stage(false);
  assert.ok(!subIds("opencode").includes("request"));
});

test("the 23.4.3 addendum: Free-Form Brief (per model, on, off), Coder Loop (Run Gate, Fix Rounds 0-3); every Coder choice explains itself in at most two sentences", async () => {
  stage(true);
  for (const client of ["opencode", "pi"]) {
    const f = get(client, "free-form");
    assert.deepEqual([f.kind, f.state, f.value, f.values], ["choice", "per model", "model", ["model", "on", "off"]]);
    assert.deepEqual(f.actions.off.args, ["set", "CODER_FREE_FORM=off"]);
    const loop = get(client, "loop");
    assert.deepEqual([loop.kind, loop.label, loop.state, loop.rows.map((r) => r.id)], ["list", "Coder Loop", "on", ["run-gate", "fix-rounds"]]);
    assert.deepEqual(get(client, "run-gate").actions.off.args, ["set", "CODER_RUN_GATE=off"]);
    const r = get(client, "fix-rounds");
    assert.deepEqual([r.state, r.values, r.actions["3"].args], ["1", ["0", "1", "2", "3"], ["set", "CODER_FIX_ROUNDS=3"]]);
    for (const row of allRows(rowsAt(panel(client).rows, ["subagent"]))) {
      if (row.kind !== "choice") continue;
      assert.ok(row.about, row.id);
      assert.ok((row.about.match(/[.!?](\s|$)/g) ?? []).length <= 2, row.about);       // at most two sentences
    }
  }
  writeFileSync(join(PI, "carl.json"), JSON.stringify({ ...JSON.parse(readFileSync(join(PI, "carl.json"), "utf8")), run_gate: false, fix_rounds: 0, free_form: "on" }));
  assert.deepEqual([get("pi", "loop").state, get("pi", "fix-rounds").state, get("pi", "free-form").state], ["off", "0", "on"]);
  // OpenCode: the explanation as a toast when the values open
  stage(true);
  const plugin = (await import(copyPanel("client/opencode/plugins/carl-panel", "tui.js"))).default;
  const { api, dialogs, abouts, commands } = fakeOpenCode();
  await plugin.tui(api);
  commands[0].onSelect();
  dialogs.at(-1).props.onSelect({ value: "subagent" });
  dialogs.at(-1).props.onSelect({ value: "free-form" });
  assert.equal(dialogs.at(-1).props.title, "Free-Form Brief");
  assert.deepEqual(abouts, [{ variant: "info", message: "Lets a strong main model add its own words to the coder's brief. Per model allows it for cloud models and CARL's larger models.", duration: 8000 }]);
});
