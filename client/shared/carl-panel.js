// The /carl panel's content (client/shared/carl-panel.js: OpenCode's carl-panel plugin and Pi's carl-panel
// extension each carry a copy): every CARL piece on this computer with its state, read from the config files
// the installer wrote, and the actions the panel offers (the client config sync: carl-sync.py).
//
// sections(client) -> [{ id, title, summary, lines, actions }]; an action runs carl-sync.py with its args.
// Read-only except run(); a file that can't be read shows as "not installed".

import { spawn } from "node:child_process";
import * as fs from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const HOME = homedir();
const CARL = join(HOME, ".config", "carl");
// OpenCode and Pi read their configs when they start: a config applied after that waits for a restart
const STARTED_WITH = applied();
const OC = join(HOME, ".config", "opencode");
const PI = process.env.PI_CODING_AGENT_DIR || join(HOME, ".pi", "agent");

/** @param {string} path @returns {any} */
function json(path) {
  try {
    return JSON.parse(fs.readFileSync(path, "utf8"));
  } catch {
    return undefined;
  }
}

/** @param {string} path */
function text(path) {
  try {
    return fs.readFileSync(path, "utf8");
  } catch {
    return "";
  }
}

/** @param {string} path */
function exists(path) {
  try {
    fs.statSync(path);
    return true;
  } catch {
    return false;
  }
}

/** The client config version this computer has now ("" when none). */
function applied() {
  const st = json(join(CARL, "client-sync.json"));
  return typeof st?.applied === "string" ? st.applied : "";
}

/** A config applied since this OpenCode or Pi started (it needs a restart to use it); "" when none. */
export function appliedSinceStart() {
  const now = applied();
  return now && now !== STARTED_WITH ? now : "";
}

/**
 * Call `tell` once when a config is applied while this OpenCode or Pi runs (checked every 30 s).
 * @param {(version: string) => void} tell
 */
export function watchApplied(tell) {
  const t = setInterval(() => {
    const v = appliedSinceStart();
    if (v) {
      clearInterval(t);
      tell(v);
    }
  }, 30_000);
  t.unref?.();
}

/** @param {number} t seconds since 1970 */
function ago(t) {
  const s = Math.max(0, Math.round(Date.now() / 1000 - t));
  return s < 90 ? `${s} s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`;
}

/**
 * @typedef {{ label: string, args: string[] }} Action
 * @typedef {{ id: string, title: string, summary: string, lines: string[], actions: Action[] }} Section
 */

/** The client config sync (carl-sync.py's state). @returns {Section} */
function syncSection() {
  const st = json(join(CARL, "client-sync.json")) ?? {};
  const bundle = typeof st.bundle === "string" ? st.bundle : "";
  const remote = bundle ? json(join(bundle, "remote.json")) : undefined;
  const live = Boolean(st.service && st.connected && st.alive && Date.now() / 1000 - st.alive < 90);  // pinged lately
  const lines = [];
  if (!remote) {
    lines.push("This computer installs from the server itself (./carl.sh install): nothing to sync.");
  } else {
    lines.push(`server: ${remote.host}:${remote.port} · the dashboard's API: ${remote.cache_api}`);
    lines.push(st.service ? `service: installed, ${live ? `connected (last heard ${ago(st.alive)})`
      : "not connected (is the dashboard running? is the service?)"}` : "service: not installed (OpenCode and Pi check when they start)");
    lines.push(`applied: ${st.applied ? `${st.applied} at ${st.applied_at ?? "?"}` : "nothing yet"}`);
    if (st.pending) lines.push(`waiting: ${st.pending} (auto-apply is off: Apply it now below)`);
    if (appliedSinceStart()) lines.push("applied after this program started: restart it to use the new config");
    if (st.error) lines.push(`last error: ${st.error}`);
  }
  const auto = st.auto_apply !== false;
  lines.push(`auto-apply: ${auto ? "on: a pushed config is applied at once" : "off: a pushed config waits for you"}`);
  const actions = remote ? [
    { label: auto ? "Turn auto-apply off" : "Turn auto-apply on", args: ["auto", auto ? "off" : "on"] },
    ...(st.pending ? [{ label: `Apply ${st.pending} now`, args: ["apply"] }] : []),
    { label: "Check the server now", args: ["once"] },
  ] : [];
  const summary = !remote ? "on the server's computer" : appliedSinceStart() ? "new config: restart to use it"
    : st.pending ? `config ${st.pending} waiting`
    : `${st.service ? (live ? "service connected" : "service not connected") : "no service"} · auto-apply ${auto ? "on" : "off"}`;
  return { id: "sync", title: "Config sync", summary, lines, actions };
}

/** @param {"opencode" | "pi"} client @returns {Section[]} */
function pieces(client) {
  const out = [];
  const add = (/** @type {string} */ id, /** @type {string} */ title, /** @type {boolean} */ on, /** @type {string} */ how,
               /** @type {string[]} */ more = []) =>
    out.push({ id, title, summary: on ? "on" : "off", lines: [`state: ${on ? "on" : "off"}`, ...more, how], actions: [] });
  const env = text(join(CARL, "opencode.env"));
  if (client === "opencode") {
    const cfg = json(join(OC, "opencode.json")) ?? {};
    const tui = json(join(OC, "tui.json")) ?? {};
    const plugins = JSON.stringify(cfg.plugin ?? []);
    const tuiPlugins = JSON.stringify(tui.plugin ?? []);
    add("cache", "Prompt cache", plugins.includes("carl-cache"), "off / on: NO_CACHE=1 ./install.sh, or ./install.sh",
        ["each agent's prompt and each session saved on the server's disk (Settings > Caching on the server)"]);
    add("check", "Model check", plugins.includes("carl-model-check"), "off / on: NO_MODEL_CHECK=1 ./install.sh, or ./install.sh",
        ["warns when the model you pick isn't the one the server runs"]);
    add("switcher", "Session switcher", tuiPlugins.includes("session-switcher"), "off / on: NO_SWITCHER=1 ./install.sh",
        ["‹ › in the prompt box, /switch"]);
    add("sidebar", "Subagents sidebar", tuiPlugins.includes("subagents-sidebar"), "off / on: NO_SIDEBAR=1 ./install.sh");
    const bg = /OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=1/.test(env);
    add("coder", "Coder subagent", Boolean(cfg.agent?.coder || cfg.agent?.["carl-coder"]),
        "on when the server runs 2 slots (the installer decides) · background off: NO_BACKGROUND_SUBAGENTS=1 ./install.sh",
        ["tools: everything but task (LSP and web search too)",
         bg ? "background: on: the main session goes on while the coder works (both slots busy)"
           : "background: off: the main session waits for the coder"]);
    add("browser", "Browser", Boolean(cfg.mcp?.["carl-browser"]), "off: NO_BROWSER=1 ./install.sh · a visible window: BROWSER_HEADED=1",
        ["its tools belong to the browser subagent"]);
    add("web", "Web search", /OPENCODE_ENABLE_EXA=1/.test(env), "WEB_SEARCH=exa|parallel|off ./install.sh",
        ["the queries leave this computer"]);
    add("lsp", "LSP", cfg.lsp === true && /OPENCODE_EXPERIMENTAL_LSP_TOOL=1/.test(env), "off: NO_LSP=1 ./install.sh",
        ["OpenCode downloads and runs language servers"]);
  } else {
    const mcp = json(join(PI, "mcp.json")) ?? {};
    const servers = mcp.mcpServers ?? mcp.servers ?? {};
    add("cache", "Prompt cache", exists(join(PI, "extensions", "carl-cache", "index.ts")), "off / on: NO_CACHE=1 ./install.sh, or ./install.sh",
        ["each agent's prompt and each session saved on the server's disk (Settings > Caching on the server)"]);
    const bg = json(join(PI, "carl.json"))?.background_subagents !== false;
    add("coder", "Coder subagent", exists(join(PI, "agents", "coder.md")) || exists(join(PI, "agents", "carl-coder.md")),
        "on when the server runs 2 slots (the installer decides) · background off: NO_BACKGROUND_SUBAGENTS=1 ./install.sh",
        ["tools: every tool but subagent (web search too when it is on)",
         bg ? "background: on: the main session goes on while the coder works (both slots busy); /subagents lists them"
           : "background: off: the main session waits for the coder"]);
    add("subagent", "Subagent tool", exists(join(PI, "extensions", "subagent", "index.ts")), "comes with the coder");
    add("browser", "Browser", Boolean(servers["carl-browser"]), "off: NO_BROWSER=1 ./install.sh", ["an MCP server, loaded on demand"]);
    add("web", "Web search", Boolean(servers["carl-web-search"]), "WEB_SEARCH=exa|parallel|off ./install.sh",
        ["the queries leave this computer"]);
  }
  return out;
}

/** Every section for this client: the sync first. @param {"opencode" | "pi"} client */
export function sections(client) {
  return [syncSection(), ...pieces(client)];
}

/**
 * Run carl-sync.py (in the client folder the installer recorded) with args; its exit code (-1: none).
 * @param {string[]} args
 * @returns {Promise<number>}
 */
export function run(args) {
  const st = json(join(CARL, "client-sync.json")) ?? {};
  const tool = typeof st.bundle === "string" ? join(st.bundle, "carl-sync.py") : "";
  if (!tool || !exists(tool)) return Promise.resolve(-1);
  return new Promise((ok) => {
    const p = spawn("python3", [tool, ...args], { stdio: "ignore" });
    p.on("close", (code) => ok(code ?? -1));
    p.on("error", () => ok(-1));
  });
}

/**
 * Without the sync service: check the server once in the background (a plugin or extension starting).
 * Nothing when the service runs, or this computer installs from the server itself.
 */
export function checkOnce() {
  const st = json(join(CARL, "client-sync.json")) ?? {};
  if (st.service || typeof st.bundle !== "string" || !exists(join(st.bundle, "remote.json"))) return;
  const p = spawn("python3", [join(st.bundle, "carl-sync.py"), "once"], { stdio: "ignore", detached: true });
  p.on("error", () => {});
  p.unref();
}
