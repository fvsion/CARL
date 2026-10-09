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
const { act, duration, openCodeSearch, outcome, packageVersions, panel, piSearch, restartNotice, switches, viewText, when } =
  await import("../../client/shared/carl-panel.js");
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
const get = (client, id) => panel(client).rows.find((r) => r.id === id);
const ids = (client) => panel(client).rows.map((r) => r.id);

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

/** Every CARL part on (allOn) or off, and a sync state from another computer with a config that waits. */
function stage(allOn) {
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
  writeFileSync(join(PI, "carl.json"), JSON.stringify({ background_subagents: allOn, delegation: { reminder: allOn, gate: 0 } }));
}

const OC_ROWS = ["coder", "background", "reminder", "browser", "web", "lsp", "sidebar", "switcher", "cache", "check",
                 "sync", "auto", "apply", "once"];
const PI_ROWS = ["coder", "background", "reminder", "browser", "web", "cache", "sync", "auto", "apply", "once"];
const LABELS = { coder: "Coder subagent", background: "Background coder", reminder: "Delegation reminder", browser: "Browser",
                 web: "Web search", lsp: "LSP", sidebar: "Subagents side panel", switcher: "Session switcher",
                 cache: "Disk cache", check: "Model check", auto: "Apply new configs at once", sync: "Sync service",
                 apply: "Apply the waiting config", once: "Check for a new config" };
const KEYS = { coder: "NO_CODER", background: "NO_BACKGROUND_SUBAGENTS", reminder: "NO_REMINDER", browser: "NO_BROWSER",
               lsp: "NO_LSP", sidebar: "NO_SIDEBAR", switcher: "NO_SWITCHER", cache: "NO_CACHE", check: "NO_MODEL_CHECK" };

test("one row for each part, in the order of the groups; OpenCode has LSP, the side panels and the model check", () => {
  stage(true);
  assert.deepEqual(ids("opencode"), OC_ROWS);
  assert.deepEqual(ids("pi"), PI_ROWS);
  for (const client of ["opencode", "pi"]) {
    for (const r of panel(client).rows) assert.equal(r.label, LABELS[r.id]);
  }
  assert.ok(!panel("opencode").rows.some((r) => /gate/i.test(r.label)));     // the gate: a dashboard setting only
});

test("without the coder, the background coder and the delegation reminder disappear", () => {
  stage(false);
  for (const client of ["opencode", "pi"]) {
    assert.equal(get(client, "coder").state, "off");
    assert.ok(!ids(client).includes("background") && !ids(client).includes("reminder"), ids(client).join(" "));
  }
});

for (const allOn of [true, false]) {
  test(`the states and the switches ${allOn ? "with every part on" : "with every part off"}`, () => {
    stage(allOn);
    for (const client of ["opencode", "pi"]) {
      for (const r of panel(client).rows) {
        if (["sync", "apply", "once"].includes(r.id)) continue;
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
      for (const r of panel(client).rows) {
        assert.match(r.label, /^[A-Z]/, `${client} ${r.id}: a label starts with a capital`);
        assert.ok(r.label.length <= 30, `${client} ${r.id}: "${r.label}"`);
        assert.equal(r.state, r.state.toLowerCase(), `${client} ${r.id}: "${r.state}"`);
        assert.ok(r.state.length <= 14, `${client} ${r.id}: a state, not a sentence: "${r.state}"`);
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
  assert.equal(s.viewTitle, "Config sync");
  assert.equal(s.state, "error");
  const v = Object.fromEntries(s.view);
  assert.equal(v["Sync service"], "connected");
  assert.equal(v["Server address"], "192.168.42.1:8080");
  assert.equal(v["Dashboard API"], "192.168.42.1:8081");
  assert.equal(v["Waiting config"], WAITING);
  assert.match(v["Last check"], /^failed: The dashboard does not answer/);
  assert.match(viewText(s.view).split("\n")[0], /^Sync service {4}connected$/);
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
  assert.ok(!Object.hasOwn(Object.fromEntries(get("opencode", "sync").view), "Client package"));
  writeFileSync(join(BUNDLE, "VERSION"), "1.7.0\n");
  writeFileSync(join(BUNDLE, "remote.json"), JSON.stringify({ host: "192.168.42.1", port: 8080,
                                                            cache_api: "http://192.168.42.1:8081", version: "1.8.0" }));
  const v = Object.fromEntries(get("pi", "sync").view);
  assert.equal(v["Client package"], "1.7.0");
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
const calls = () => (existsSync(join(BUNDLE, "calls.txt")) ? readFileSync(join(BUNDLE, "calls.txt"), "utf8").trim().split("\n") : []);
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
  assert.deepEqual(main.options.map((o) => o.footer), panel("opencode").rows.map((r) => r.state));
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
  assert.equal(web.title, "Web search");
  assert.equal(web.current, "exa");
  assert.deepEqual(web.options.map((o) => [o.title, o.description]),
                   [["exa", "Queries go to exa.ai."], ["parallel", "Queries go to parallel.ai."], ["off", undefined]]);
  // the sync service: a view, nothing to select
  dialogs.at(-2).props.onSelect({ value: "sync" });
  const view = dialogs.at(-1);
  assert.equal(view.kind, "alert");
  assert.equal(view.props.title, "Config sync");
  assert.match(view.props.message, /^Sync service {4}connected$/m);
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
  const main = dialogs.at(-1).props;
  main.onSelect({ value: "coder" });
  for (let i = 0; i < 200 && !toasts.length; i++) await new Promise((r) => setTimeout(r, 25));
  assert.equal(toasts.length, 1);
  assert.equal(toasts[0].variant, "warning");
  assert.match(toasts[0].message, /^CARL turned the coder subagent on\. This server runs 1 slot: /);
  assert.equal(main.options.find((o) => o.value === "coder").footer, "on");
});

test("Pi: Pi's settings list; Space changes a row in place; a notice says what CARL did", async (t) => {
  stage(true);
  fake({ files: { [join(PI, "mcp.json")]: JSON.stringify({ mcpServers: {} }) }, out: { ok: true, restart: ["opencode", "pi"] } });
  let ext;
  try {
    ext = (await import(copyPanel("client/pi/extensions/carl-panel", "index.ts"))).default;
  } catch (e) {
    const why = String(e?.code ?? e);
    if (/UNKNOWN_FILE_EXTENSION|ERR_MODULE_NOT_FOUND|Cannot find package/.test(why)) return t.skip(`needs Pi's packages: ${why}`);
    throw e;
  }
  const commands = {};
  ext({ on: () => {}, registerCommand: (name, c) => { commands[name] = c; } });
  assert.ok(commands.carl);
});
