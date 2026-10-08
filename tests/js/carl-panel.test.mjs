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
const { act, duration, openCodeSearch, outcome, packageVersions, piSearch, restartNotice, row, sections, switches, when, wrap } =
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
const get = (client, id) => sections(client).find((s) => s.id === id);

test("OpenCode web search: the provider that is on, off only when neither is set", () => {
  assert.equal(openCodeSearch(envFile("exa")), "exa");
  assert.equal(openCodeSearch(envFile("parallel")), "parallel");
  assert.equal(openCodeSearch("export OPENCODE_EXPERIMENTAL_LSP_TOOL=1\n"), "");
  assert.equal(openCodeSearch("# export OPENCODE_ENABLE_EXA=1 was here\n"), "");      // a comment is not a switch
  assert.equal(openCodeSearch(""), "");
  for (const p of ["exa", "parallel"]) {
    writeFileSync(join(CARL, "opencode.env"), envFile(p));
    const s = get("opencode", "web");
    assert.equal(s.summary, p);
    assert.equal(s.row, row("Web search", p));
    assert.ok(s.lines.includes(`The search queries go to ${p}, outside this computer.`), s.lines.join(" | "));
  }
  writeFileSync(join(CARL, "opencode.env"), "export OPENCODE_EXPERIMENTAL_LSP_TOOL=1\n");
  assert.equal(get("opencode", "web").summary, "off");
  assert.ok(get("opencode", "web").lines.some((l) => l.includes("outside this computer")));    // also when it is off
});

test("Pi web search: the provider from the MCP server's address", () => {
  assert.equal(piSearch({ url: "https://mcp.exa.ai/mcp" }), "exa");
  assert.equal(piSearch({ url: "https://search.parallel.ai/mcp" }), "parallel");
  assert.equal(piSearch({ url: "https://exa.ai.example.com/mcp" }), "on");
  assert.equal(piSearch(undefined), "");
  writeFileSync(join(PI, "mcp.json"), JSON.stringify({ mcpServers: { "carl-web-search": { url: "https://search.parallel.ai/mcp" } } }));
  assert.equal(get("pi", "web").summary, "parallel");
  writeFileSync(join(PI, "mcp.json"), JSON.stringify({ mcpServers: {} }));
  assert.equal(get("pi", "web").summary, "off");
});

test("the rows read broken or missing files as off", () => {
  writeFileSync(join(CARL, "client-sync.json"), "{broken");
  mkdirSync(join(HOME, ".config", "opencode"), { recursive: true });
  writeFileSync(join(HOME, ".config", "opencode", "opencode.json"), "[]");              // not an object
  const all = sections("opencode");
  assert.equal(all[0].id, "sync");
  assert.equal(all[0].summary, "not needed");
  assert.equal(all.find((s) => s.id === "cache").summary, "off");
  assert.ok(!all.some((s) => s.id === "auto"));                 // no sync: nothing to apply at once
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

const OC_ROWS = ["sync", "auto", "coder", "background", "reminder", "browser", "web", "lsp", "sidebar", "switcher", "cache", "check"];
const PI_ROWS = ["sync", "auto", "coder", "background", "reminder", "browser", "web", "cache"];
const LABELS = { coder: "Coder subagent", background: "Background coder", reminder: "Delegation reminder", browser: "Browser",
                 web: "Web search", lsp: "LSP", sidebar: "Subagents side panel", switcher: "Session switcher",
                 cache: "Disk cache", check: "Model check", auto: "Apply new configs at once", sync: "Config sync" };
const KEYS = { coder: "NO_CODER", background: "NO_BACKGROUND_SUBAGENTS", reminder: "NO_REMINDER", browser: "NO_BROWSER",
               lsp: "NO_LSP", sidebar: "NO_SIDEBAR", switcher: "NO_SWITCHER", cache: "NO_CACHE", check: "NO_MODEL_CHECK" };

test("one row for each part: OpenCode has the side panel, the switcher, LSP and the model check; Pi does not", () => {
  stage(true);
  assert.deepEqual(sections("opencode").map((s) => s.id), OC_ROWS);
  assert.deepEqual(sections("pi").map((s) => s.id), PI_ROWS);
  for (const client of ["opencode", "pi"]) {
    for (const s of sections(client)) assert.equal(s.title, LABELS[s.id]);
  }
  assert.ok(!sections("opencode").some((s) => /gate/i.test([s.title, ...s.lines, ...s.details].join(" "))));  // dashboard only
});

for (const allOn of [true, false]) {
  test(`the states and the switches ${allOn ? "with every part on" : "with every part off"}`, () => {
    stage(allOn);
    for (const client of ["opencode", "pi"]) {
      for (const s of sections(client)) {
        if (s.id === "sync") continue;
        const want = s.id === "web" ? (allOn ? "exa" : "off") : s.id === "auto" ? (allOn ? "off" : "on") : allOn ? "on" : "off";
        assert.equal(s.summary, want, `${client} ${s.id}`);
        assert.equal(s.row, row(s.title, want));
        if (s.id === "auto") {
          assert.deepEqual(s.actions.map((a) => a.args), [["auto", allOn ? "on" : "off"]]);
        } else if (s.id === "web") {
          assert.deepEqual(s.actions.map((a) => a.args[1]),
                           allOn ? ["WEB_SEARCH=parallel", "WEB_SEARCH=off"] : ["WEB_SEARCH=exa", "WEB_SEARCH=parallel"]);
          assert.deepEqual(s.actions.map((a) => a.label), allOn ? ["Use parallel", "Turn it off"] : ["Use exa", "Use parallel"]);
        } else {
          assert.deepEqual(s.actions.map((a) => [a.label, ...a.args]),
                           [[allOn ? "Turn it off" : "Turn it on", "set", `${KEYS[s.id]}=${allOn ? "1" : "on"}`,
                             ...(s.id === "coder" && !allOn ? ["CODER=1"] : [])]], `${client} ${s.id}`);   // on: also with 1 slot
          assert.ok(s.details.includes(row("Setup switch", KEYS[s.id])), s.details.join(" | "));
          assert.ok(s.details.includes(row("In client-install.env", allOn ? "not set" : `${KEYS[s.id]}=1`)), s.details.join(" | "));
        }
      }
    }
  });
}

test("the reminder and the background: what is installed; without the coder, the switch in client-install.env", () => {
  stage(true);
  const oc = JSON.parse(readFileSync(join(OC, "opencode.json"), "utf8"));
  oc.plugin[2][1].reminder = false;
  writeFileSync(join(OC, "opencode.json"), JSON.stringify(oc));
  writeFileSync(join(PI, "carl.json"), JSON.stringify({ background_subagents: false, delegation: { reminder: false } }));
  assert.equal(get("opencode", "reminder").summary, "off");
  assert.equal(get("pi", "reminder").summary, "off");
  assert.equal(get("pi", "background").summary, "off");
  assert.equal(get("opencode", "background").summary, "on");
  // no coder: the installed files say nothing, so the row shows the switch, and says that it has no effect now
  delete oc.agent;
  writeFileSync(join(OC, "opencode.json"), JSON.stringify(oc));
  writeFileSync(join(CARL, "client-install.env"), "NO_REMINDER=1\n");
  assert.equal(get("opencode", "coder").summary, "off");
  assert.equal(get("opencode", "reminder").summary, "off");
  assert.equal(get("opencode", "background").summary, "on");
  assert.ok(get("opencode", "background").lines.some((l) => l.includes("no effect now")));
  assert.deepEqual(switches(), { NO_REMINDER: "1" });
});

// ------------------------------------------------------------------ the words (the 23.2 rules) and the widths

/** Every text a row shows, with where it is. */
function texts(s) {
  return [["row", s.row], ["title", s.title], ...s.actions.map((a) => ["action", a.label]),
          ...s.actions.filter((a) => a.busy).map((a) => ["busy", a.busy]),
          ...s.lines.map((l) => ["line", l]), ...s.details.map((l) => ["detail", l])];
}

/** Wrapped lines joined again (the next part of a line starts with 2 spaces). */
function paragraphs(list) {
  const out = [];
  for (const l of list) {
    if (l.startsWith("  ") && out.length) out[out.length - 1] += ` ${l.trim()}`;
    else out.push(l);
  }
  return out;
}
const isRow = (t) => /^\S.*?\S {2,}\S/.test(t);

// reference/glossary.md: the names that go away (and G16: one path notation)
const FORBIDDEN = [/prompt cache/i, /auto-apply/i, /\bpush/i, /\bpiece/i, /\bwindow/i, /\bmonitor\b/i, /cache API/,
                   /dashboard's API/, /→/, /\bctx\b/, /conversation/i, /\bdone\b/, / · /, /\bsidebar\b/i];

for (const allOn of [true, false]) {
  test(`the panel ${allOn ? "with every part on" : "with every part off"}: the 23.2 rules, glossary names, short lines`, () => {
    stage(allOn);
    for (const client of ["opencode", "pi"]) {
      for (const s of sections(client)) {
        assert.match(s.title, /^[A-Z]/, `${client} ${s.id}: a label starts with a capital`);
        assert.equal(s.summary, s.summary.toLowerCase(), `${client} ${s.id}: a state value stays lower case`);
        for (const [where, t] of texts(s)) {
          for (const bad of FORBIDDEN) assert.ok(!bad.test(t), `${client} ${s.id} ${where}: ${bad} in "${t}"`);
          assert.ok(t.length <= 90, `${client} ${s.id} ${where}: ${t.length} characters: "${t}"`);
          if (where === "action") assert.match(t, /^[A-Z]/, `${client} ${s.id}: "${t}"`);
          if (where === "busy") assert.match(t, /^CARL is \S+ing .*…$/, `${client} ${s.id}: "${t}"`);
          if (where !== "detail") {
            assert.ok(!t.includes(HASH) && !t.includes(WAITING), `${client} ${s.id} ${where}: a hash in "${t}"`);
            assert.ok(!/\b[A-Z_]+=/.test(t), `${client} ${s.id} ${where}: an installer variable in "${t}"`);
            assert.ok(!t.includes("192.168.42.1"), `${client} ${s.id} ${where}: an address in "${t}"`);
          }
        }
        for (const p of paragraphs([...s.lines, ...s.details])) {
          assert.match(p, /^[^a-z]/, `${client} ${s.id}: "${p}" starts with a capital`);
          assert.ok(isRow(p) || /[.…]$/.test(p), `${client} ${s.id}: "${p}" is not a whole sentence or a label row`);
        }
      }
    }
  });
}

test("the sync rows: label rows and sentences, the version and the addresses in the details", () => {
  stage(true);
  const s = sections("opencode")[0];
  assert.ok(s.lines.includes(row("Sync service", "connected")), s.lines.join(" | "));
  assert.ok(s.lines.includes(row("Last config", "2026-10-04 10:53")) || s.lines.some((l) => l.startsWith("Last config")), s.lines.join(" | "));
  assert.ok(s.details.includes(row("Dashboard API", "http://192.168.42.1:8081")), s.details.join(" | "));
  assert.ok(s.details.some((l) => l.includes(HASH)), s.details.join(" | "));
  assert.deepEqual(s.actions.map((a) => a.id), ["apply", "check"]);
  assert.equal(s.summary, "restart needed");
  assert.deepEqual(get("opencode", "auto").actions.map((a) => a.id), ["auto-on"]);
  stage(false);
  assert.equal(sections("pi")[0].summary, "restart needed");
  writeFileSync(join(CARL, "client-sync.json"), JSON.stringify({ bundle: BUNDLE, service: false }));
  assert.equal(sections("pi")[0].summary, "checks at start");
  assert.equal(get("pi", "auto").summary, "on");
});

test("the sync row: the version of the client package, and a note when the server runs another", () => {
  stage(true);
  rmSync(join(BUNDLE, "VERSION"), { force: true });
  let s = sections("opencode")[0];
  assert.ok(!s.details.some((l) => l.startsWith("Client package")), s.details.join(" | "));   // no package file
  writeFileSync(join(BUNDLE, "VERSION"), "1.7.0\n");
  writeFileSync(join(BUNDLE, "remote.json"), JSON.stringify({ host: "192.168.42.1", port: 8080,
                                                            cache_api: "http://192.168.42.1:8081", version: "1.7.0" }));
  s = sections("opencode")[0];
  assert.ok(s.details.includes(row("Client package", "version 1.7.0")), s.details.join(" | "));
  assert.ok(s.details.includes(row("Server version", "CARL 1.7.0")), s.details.join(" | "));
  assert.ok(s.details.includes(row("Server version from", "the client package")), s.details.join(" | "));
  assert.ok(!s.lines.some((l) => l.includes("version")), s.lines.join(" | "));                // the same: no note
  writeFileSync(join(BUNDLE, "remote.json"), JSON.stringify({ host: "192.168.42.1", port: 8080,
                                                            cache_api: "http://192.168.42.1:8081", version: "1.8.0" }));
  s = sections("pi")[0];
  assert.ok(s.lines.includes("The client package is version 1.7.0, but the server runs CARL 1.8.0."), s.lines.join(" | "));
  assert.ok(s.lines.some((l) => l.includes("./setup")), s.lines.join(" | "));
  for (const t of [...s.lines, ...s.details]) assert.ok(t.length <= 90, t);
  assert.deepEqual(packageVersions(BUNDLE, { version: "1.8.0\n<script>" }), { client: "1.7.0", server: "" });
  assert.deepEqual(packageVersions("", {}), { client: "", server: "" });
  // the version the sync service heard from the dashboard API wins over the packaged remote.json
  assert.deepEqual(packageVersions(BUNDLE, { version: "1.7.0" }, { server_version: "1.8.0" }), { client: "1.7.0", server: "1.8.0" });
  rmSync(join(BUNDLE, "VERSION"), { force: true });
});

test("the side panel's row says what ✓ and ✗ mean", () => {
  stage(true);
  assert.ok(get("opencode", "sidebar").lines.some((l) => l.includes("✓") && l.includes("✗")));
});

test("outcome: what a toast says after a sync action", () => {
  stage(false);
  writeFileSync(join(CARL, "client-sync.json"), JSON.stringify({ bundle: BUNDLE, applied: HASH }));
  const check = { id: "check", label: "Check for a new config now", args: ["once"] };
  assert.deepEqual(outcome(check, 0, "opencode"), { ok: true, message: "CARL updated the model list. Restart OpenCode to use it." });
  assert.equal(outcome({ id: "auto-off", label: "", args: ["auto", "off"] }, 0, "pi").message, "New configs from the dashboard now wait for you.");
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
  const a = get("opencode", "sidebar").actions[0];
  const said = await act(a, "opencode");
  assert.deepEqual(calls(), ["set NO_SIDEBAR=1"]);
  assert.deepEqual(said, { ok: true, message: "CARL turned the subagents side panel off. Restart OpenCode to use it." });
  const s = get("opencode", "sidebar");
  assert.equal(s.summary, "off");
  assert.ok(s.lines.includes("Restart OpenCode to use the change."), s.lines.join(" | "));
  assert.deepEqual(s.actions[0].args, ["set", "NO_SIDEBAR=on"]);
  // back as it was: no restart needed
  fake({ files: { [join(OC, "tui.json")]: JSON.stringify({ plugin: ["file:/x/session-switcher", "file:/x/subagents-sidebar"] }) },
         out: { ok: true, restart: ["opencode"] } });
  assert.equal((await act(s.actions[0], "opencode")).ok, true);
  assert.ok(!get("opencode", "sidebar").lines.some((l) => l.startsWith("Restart")));
});

test("web search: both clients restart, and OpenCode from a new terminal", async () => {
  stage(true);
  fake({ files: { [join(CARL, "opencode.env")]: envFile("parallel") }, out: { ok: true, restart: ["opencode", "pi"], new_terminal: true } });
  const a = get("opencode", "web").actions.find((x) => x.label === "Use parallel");
  const said = await act(a, "opencode");
  assert.deepEqual(calls(), ["set WEB_SEARCH=parallel"]);
  assert.equal(said.message, "CARL set web search to parallel. Restart OpenCode and Pi to use it. Start OpenCode from a new terminal.");
});

test("a switch that did not change the part, and a setup that failed, say so", async () => {
  stage(false);
  fake({ out: { ok: true, restart: ["pi"] } });                           // the setup did not turn it on (a fake)
  let said = await act(get("pi", "coder").actions[0], "pi");
  assert.deepEqual(calls(), ["set NO_CODER=on CODER=1"]);
  assert.equal(said.ok, false);
  assert.match(said.message, /still off\. The setup's output is in ~\/\.config\/carl\/client-sync\.log\./);
  fake({ code: 1, out: { ok: false, error: "The installer stopped with an error (exit code 3)." } });
  said = await act(get("pi", "browser").actions[0], "pi");
  assert.deepEqual(said, { ok: false, message: "CARL could not change the setting. The installer stopped with an error (exit code 3)." });
  rmSync(join(BUNDLE, "carl-sync.py"));
  said = await act(get("pi", "browser").actions[0], "pi");
  assert.match(said.message, /cannot find carl-sync\.py/);
});

// ------------------------------------------------------------------ the renderers

/** A copy of a client's panel folder with carl-panel.js next to it (as the setup installs it). */
function copyPanel(src, file) {
  const dir = mkdtempSync(join(tmpdir(), "carl-panel-ui-"));
  copyFileSync(join(REPO, src, file), join(dir, file));
  copyFileSync(join(REPO, "client/shared/carl-panel.js"), join(dir, "carl-panel.js"));
  return pathToFileURL(join(dir, file)).href;
}

test("OpenCode: /carl lists the rows; a row opens with its switch first; the switch runs and a toast says so", async () => {
  stage(true);
  fake({ files: { [join(OC, "tui.json")]: JSON.stringify({ plugin: ["file:/x/subagents-sidebar"] }) }, out: { ok: true, restart: ["opencode"] } });
  const plugin = (await import(copyPanel("client/opencode/plugins/carl-panel", "tui.js"))).default;
  let dialog;
  const toasts = [];
  const commands = [];
  const api = {
    ui: { DialogSelect: (props) => props, dialog: { replace: (f) => { dialog = f(); } }, toast: (t) => toasts.push(t) },
    command: { register: (f) => { commands.push(...f()); return () => {}; } },
    lifecycle: { onDispose: () => {} },
  };
  await plugin.tui(api);
  assert.equal(commands[0].slash.name, "carl");
  commands[0].onSelect();
  assert.equal(dialog.title, "CARL");
  // the copy started with the config that is applied now (the sync row of this module says "restart needed")
  assert.deepEqual(dialog.options.map((o) => o.title).slice(1), sections("opencode").map((s) => s.row).slice(1));
  assert.equal(dialog.options[0].title, row("Config sync", "a config waits"));
  dialog.onSelect({ value: "switcher" });
  assert.equal(dialog.title, "CARL › Session switcher");
  assert.equal(dialog.options[0].title, "▸ Turn it off");
  assert.ok(dialog.options.some((o) => o.category === "Details"));
  await dialog.onSelect(dialog.options[0]);
  assert.deepEqual(calls(), ["set NO_SWITCHER=1"]);
  assert.deepEqual(toasts.map((t) => t.variant), ["info", "success"]);
  assert.equal(toasts[0].message, "CARL is turning the session switcher off…");
  assert.equal(toasts[1].message, "CARL turned the session switcher off. Restart OpenCode to use it.");
  assert.equal(dialog.options[0].title, "▸ Turn it on");                  // the row again, with its new state
});

test("Pi: /carl lists the rows; a row opens; its switch runs and a notice says so", async (t) => {
  stage(true);
  fake({ files: { [join(PI, "mcp.json")]: JSON.stringify({ mcpServers: {} }) }, out: { ok: true, restart: ["opencode", "pi"] } });
  let ext;
  try {
    ext = (await import(copyPanel("client/pi/extensions/carl-panel", "index.ts"))).default;
  } catch (e) {
    if (String(e?.code).includes("UNKNOWN_FILE_EXTENSION")) return t.skip("this node does not run TypeScript");
    throw e;
  }
  const commands = {};
  ext({ on: () => {}, registerCommand: (name, c) => { commands[name] = c; } });
  const asked = [];
  const notes = [];
  const rows = sections("pi").map((s) => s.row);
  const answers = [rows[PI_ROWS.indexOf("browser")], "▸ Turn it off", "‹ back", undefined];
  const ctx = { ui: { select: async (title, items) => { asked.push([title, items]); return answers.shift(); },
                      notify: (m, kind) => notes.push([kind, m]) } };
  await commands.carl.handler("", ctx);
  assert.equal(asked[0][0], "CARL");
  assert.deepEqual(asked[0][1].slice(1), rows.slice(1));
  assert.equal(asked[1][0], "CARL › Browser");
  assert.equal(asked[1][1][0], "▸ Turn it off");
  assert.ok(asked[1][1].includes("── Details ──"));
  assert.deepEqual(calls(), ["set NO_BROWSER=1"]);
  assert.deepEqual(notes, [["info", "CARL is turning the browser off…"],
                           ["info", "CARL turned the browser off. Restart OpenCode and Pi to use it."]]);
  assert.equal(asked[2][1][0], "▸ Turn it on");
});

test("when, duration, wrap: one time format, short lines", () => {
  const now = new Date(2026, 9, 4, 15, 0);
  assert.equal(when("2026-10-04 10:53:33", now), "today 10:53");
  assert.equal(when("2026-10-03 23:01:00", now), "yesterday 23:01");
  assert.equal(when("2026-09-28 08:00:00", now), "2026-09-28 08:00");
  assert.equal(when(undefined, now), "at an unknown time");
  assert.deepEqual([42, 61, 3 * 60, 72 * 60, 3 * 3600, 49 * 3600].map(duration), ["42 s", "1 min", "3 min", "1 h 12 min", "3 h", "2 days"]);
  assert.deepEqual(wrap("aaa bbb ccc", 7), ["aaa bbb", "ccc"]);
  assert.deepEqual(wrap("", 7), []);
});
