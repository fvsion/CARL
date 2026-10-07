// client/shared/carl-panel.js: the /carl panel's sections, read from a temporary HOME's config files.
// Run: node --test tests/js (tests/scripts/test_js.py runs it with the other suites).
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

const HOME = mkdtempSync(join(tmpdir(), "carl-panel-"));
process.env.HOME = HOME;                     // read when the module loads
delete process.env.PI_CODING_AGENT_DIR;
const { duration, openCodeSearch, outcome, packageVersions, piSearch, restartNotice, sections, when, wrap } =
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
const web = (client) => sections(client).find((s) => s.id === "web");

test("OpenCode web search: the provider that is on, off only when neither is set", () => {
  assert.equal(openCodeSearch(envFile("exa")), "exa");
  assert.equal(openCodeSearch(envFile("parallel")), "parallel");
  assert.equal(openCodeSearch("export OPENCODE_EXPERIMENTAL_LSP_TOOL=1\n"), "");
  assert.equal(openCodeSearch("# export OPENCODE_ENABLE_EXA=1 was here\n"), "");      // a comment is not a switch
  assert.equal(openCodeSearch(""), "");
  for (const p of ["exa", "parallel"]) {
    writeFileSync(join(CARL, "opencode.env"), envFile(p));
    const s = web("opencode");
    assert.equal(s.summary, `on (${p})`);
    assert.ok(s.lines.includes(`On. The search queries go to ${p}, outside this computer.`), s.lines.join(" | "));
  }
  writeFileSync(join(CARL, "opencode.env"), "export OPENCODE_EXPERIMENTAL_LSP_TOOL=1\n");
  assert.equal(web("opencode").summary, "off");
});

test("Pi web search: the provider from the MCP server's address", () => {
  assert.equal(piSearch({ url: "https://mcp.exa.ai/mcp" }), "exa");
  assert.equal(piSearch({ url: "https://search.parallel.ai/mcp" }), "parallel");
  assert.equal(piSearch({ url: "https://exa.ai.example.com/mcp" }), "on");
  assert.equal(piSearch(undefined), "");
  writeFileSync(join(PI, "mcp.json"), JSON.stringify({ mcpServers: { "carl-web-search": { url: "https://search.parallel.ai/mcp" } } }));
  assert.equal(web("pi").summary, "on (parallel)");
  writeFileSync(join(PI, "mcp.json"), JSON.stringify({ mcpServers: {} }));
  assert.equal(web("pi").summary, "off");
});

test("the sections read broken or missing files as not installed", () => {
  writeFileSync(join(CARL, "client-sync.json"), "{broken");
  mkdirSync(join(HOME, ".config", "opencode"), { recursive: true });
  writeFileSync(join(HOME, ".config", "opencode", "opencode.json"), "[]");              // not an object
  const all = sections("opencode");
  assert.equal(all[0].id, "sync");
  assert.equal(all[0].summary, "this computer runs the server");
  assert.equal(all.find((s) => s.id === "cache").summary, "off");
});

// ------------------------------------------------------------------ the words and the widths (Phase 21)

const OC = join(HOME, ".config", "opencode");
const BUNDLE = join(HOME, "client");
const HASH = "c084921eda7e";
const WAITING = "d1e2f3a4b5c6";

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
    plugin: ["file:/x/carl-cache", ["file:/x/carl-model-check", {}]], agent: { coder: {} }, mcp: { "carl-browser": {} }, lsp: true,
  } : {}));
  writeFileSync(join(OC, "tui.json"), JSON.stringify(allOn ? { plugin: ["file:/x/session-switcher", "file:/x/subagents-sidebar"] } : {}));
  writeFileSync(join(CARL, "opencode.env"), allOn ? envFile("exa") + "export OPENCODE_EXPERIMENTAL_LSP_TOOL=1\n" : "");
  for (const f of ["extensions/carl-cache/index.ts", "extensions/subagent/index.ts", "agents/coder.md"]) {
    if (allOn) writeFileSync(join(PI, f), "x");
    else rmSync(join(PI, f), { force: true });
  }
  writeFileSync(join(PI, "mcp.json"), JSON.stringify(allOn ? { mcpServers: { "carl-browser": {}, "carl-web-search": { url: "https://mcp.exa.ai/mcp" } } } : {}));
  writeFileSync(join(PI, "carl.json"), JSON.stringify({ background_subagents: allOn }));
}

/** Every text a section shows, with where it is. */
function texts(s) {
  return [["title", `${s.title} · ${s.summary}`], ...s.actions.map((a) => ["action", `▸ ${a.label}`]),
          ...s.lines.map((l) => ["line", l]), ...s.details.map((l) => ["detail", l])];
}

// reference/glossary.md: the names that go away (and G16: one path notation)
const FORBIDDEN = [/prompt cache/i, /auto-apply/i, /\bpush/i, /\bpiece/i, /\bwindow/i, /\bmonitor\b/i, /cache API/,
                   /dashboard's API/, /→/, /\bctx\b/, /conversation/i, /\bdone\b/];

for (const allOn of [true, false]) {
  test(`the panel ${allOn ? "with every part on" : "with every part off"}: glossary names, no hash in the main text, short lines`, () => {
    stage(allOn);
    for (const client of ["opencode", "pi"]) {
      for (const s of sections(client)) {
        for (const [where, t] of texts(s)) {
          for (const bad of FORBIDDEN) assert.ok(!bad.test(t), `${client} ${s.id} ${where}: ${bad} in "${t}"`);
          assert.ok(t.length <= 90, `${client} ${s.id} ${where}: ${t.length} characters: "${t}"`);
          if (where !== "detail") {
            assert.ok(!t.includes(HASH) && !t.includes(WAITING), `${client} ${s.id} ${where}: a hash in "${t}"`);
            assert.ok(!/\b[A-Z_]+=/.test(t), `${client} ${s.id} ${where}: an installer variable in "${t}"`);
            assert.ok(!t.includes("192.168.42.1"), `${client} ${s.id} ${where}: an address in "${t}"`);
          }
        }
      }
    }
  });
}

test("the sync section: plain sentences, the version and the addresses in the details", () => {
  stage(true);
  const s = sections("opencode")[0];
  assert.ok(s.lines.some((l) => l.startsWith("Last config from the dashboard: ")), s.lines.join(" | "));
  assert.ok(s.details.includes("Dashboard API: http://192.168.42.1:8081"), s.details.join(" | "));
  assert.ok(s.details.some((l) => l.includes(HASH)), s.details.join(" | "));
  assert.deepEqual(s.actions.map((a) => a.id), ["auto-on", "apply", "check"]);
  assert.equal(s.summary, "new config: restart OpenCode to use it");
  stage(false);
  assert.equal(sections("pi")[0].summary, "new config: restart Pi to use it");
  writeFileSync(join(CARL, "client-sync.json"), JSON.stringify({ bundle: BUNDLE, service: false }));
  assert.equal(sections("pi")[0].summary, "checks at start · applies new configs at once");
});

test("the sync section: the version of the client package, and a note when the server runs another", () => {
  stage(true);
  rmSync(join(BUNDLE, "VERSION"), { force: true });
  let s = sections("opencode")[0];
  assert.ok(!s.details.some((l) => l.startsWith("Client package")), s.details.join(" | "));   // no package file
  writeFileSync(join(BUNDLE, "VERSION"), "1.7.0\n");
  writeFileSync(join(BUNDLE, "remote.json"), JSON.stringify({ host: "192.168.42.1", port: 8080,
                                                            cache_api: "http://192.168.42.1:8081", version: "1.7.0" }));
  s = sections("opencode")[0];
  assert.ok(s.details.includes("Client package: version 1.7.0"), s.details.join(" | "));
  assert.ok(s.details.includes("Server: CARL 1.7.0 (from the client package)"), s.details.join(" | "));
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

test("the coder's section says what ✓ and ✗ mean in the sidebar", () => {
  stage(true);
  const sidebar = sections("opencode").find((s) => s.id === "sidebar");
  assert.ok(sidebar.lines.some((l) => l.includes("✓") && l.includes("✗")), sidebar.lines.join(" | "));
});

test("outcome: what a toast says after an action", () => {
  stage(false);
  writeFileSync(join(CARL, "client-sync.json"), JSON.stringify({ bundle: BUNDLE, applied: HASH }));
  const check = { id: "check", label: "Check for a new config now", args: ["once"] };
  assert.deepEqual(outcome(check, 0, "opencode"), { ok: true, message: "CARL updated the model list. Restart OpenCode to use it." });
  assert.equal(outcome({ id: "auto-off", label: "", args: [] }, 0, "pi").message, "CARL: new configs from the dashboard now wait for you.");
  assert.equal(outcome(check, 1, "pi").ok, false);
  assert.match(outcome(check, -1, "pi").message, /Run the installer again/);
  writeFileSync(join(CARL, "client-sync.json"), JSON.stringify({ bundle: BUNDLE, error: "x" }));
  assert.equal(outcome(check, 0, "pi").ok, false);
  assert.equal(restartNotice("pi"), "CARL updated the model list. Restart Pi to use it.");
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
