// @ts-check
// The /carl panel's content (client/shared/carl-panel.js: OpenCode's carl-panel plugin and Pi's carl-panel
// extension each carry a copy): every CARL part on this computer with its state, read from the config files
// the installer wrote, and the actions the panel offers (the client config sync: carl-sync.py).
//
// sections(client) -> [{ id, title, summary, lines, details, actions }]; an action runs carl-sync.py with its
// args, and outcome() says what happened. `lines` are plain sentences (what the part does, its state);
// `details` hold the addresses, the versions and the installer's switches. Every line is at most 90
// characters: OpenCode's dialog cuts at about 100 columns. The words follow reference/glossary.md.
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

/** The longest line the panel makes (a wrapped line's next part is indented by 2: 90 in all). */
export const WIDTH = 88;

/** @typedef {{ [key: string]: unknown }} JsonObject */
/** @typedef {"opencode" | "pi"} Client */

/** A JSON file, or undefined. @param {string} path @returns {unknown} */
function json(path) {
  try {
    return /** @type {unknown} */ (JSON.parse(fs.readFileSync(path, "utf8")));
  } catch {
    return undefined;
  }
}

/** The value if it is a JSON object, else an empty one. @param {unknown} x @returns {JsonObject} */
function obj(x) {
  return x !== null && typeof x === "object" && !Array.isArray(x) ? /** @type {JsonObject} */ (x) : {};
}

/** A JSON object file, or undefined (no file, or not an object). @param {string} path @returns {JsonObject | undefined} */
function jsonObject(path) {
  const v = json(path);
  return v !== null && typeof v === "object" && !Array.isArray(v) ? obj(v) : undefined;
}

/** The client folder the installer recorded (client-sync.json "bundle"), or "". @param {JsonObject} st */
function bundleOf(st) {
  return typeof st.bundle === "string" ? st.bundle : "";
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
  const st = obj(json(join(CARL, "client-sync.json")));
  return typeof st.applied === "string" ? st.applied : "";
}

/** A config applied since this OpenCode or Pi started (it needs a restart to use it); "" when none. */
export function appliedSinceStart() {
  const now = applied();
  return now && now !== STARTED_WITH ? now : "";
}

/** The program's name in a sentence. @param {Client} client */
export function appName(client) {
  return client === "pi" ? "Pi" : "OpenCode";
}

/** What OpenCode and Pi say when a config was applied while they run. @param {Client} client */
export function restartNotice(client) {
  return `CARL updated the model list. Restart ${appName(client)} to use it.`;
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

/**
 * The web search provider that OpenCode's tool switches turn on (~/.config/carl/opencode.env, WEB_SEARCH=exa|parallel):
 * "exa", "parallel", or "" when web search is off.
 * @param {string} env the file's text
 * @returns {"exa" | "parallel" | ""}
 */
export function openCodeSearch(env) {
  const set = (/** @type {string} */ name) => new RegExp(`^\\s*(?:export\\s+)?${name}\\b`, "m").test(env);
  const provider = /^\s*(?:export\s+)?OPENCODE_WEBSEARCH_PROVIDER=["']?(exa|parallel)\b/m.exec(env)?.[1];
  if ((provider === "exa" || provider === "parallel") && set(`OPENCODE_ENABLE_${provider.toUpperCase()}=1`)) return provider;
  return set("OPENCODE_ENABLE_EXA=1") ? "exa" : set("OPENCODE_ENABLE_PARALLEL=1") ? "parallel" : "";
}

/**
 * The web search provider of Pi's MCP server entry (mcp.json "carl-web-search"): "exa", "parallel", "on" for
 * another address, or "" when there is no entry.
 * @param {unknown} entry
 * @returns {string}
 */
export function piSearch(entry) {
  if (!entry) return "";
  const url = String(obj(entry).url ?? "");
  return /(^|[/.])exa\.ai(\/|:|$)/.test(url) ? "exa" : /(^|[/.])parallel\.ai(\/|:|$)/.test(url) ? "parallel" : "on";
}

/**
 * Words in lines of at most `width` characters (a word longer than that stays whole).
 * @param {string} s
 * @param {number} [width]
 * @returns {string[]}
 */
export function wrap(s, width = WIDTH) {
  /** @type {string[]} */
  const out = [];
  let line = "";
  for (const word of String(s).split(/\s+/).filter(Boolean)) {
    if (line && line.length + 1 + word.length > width) {
      out.push(line);
      line = word;
    } else {
      line = line ? `${line} ${word}` : word;
    }
  }
  if (line) out.push(line);
  return out;
}

/** Sentences as panel lines: each one wrapped, its next lines indented. @param {string[]} texts */
function para(texts) {
  return texts.flatMap((t) => wrap(t).map((l, i) => (i ? `  ${l}` : l)));
}

/**
 * A time span as CARL writes it everywhere (tools/carl_core/domain/units.py): 42 s, 3 min, 1 h 12 min,
 * 2 days.
 * @param {number} seconds
 * @returns {string}
 */
export function duration(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 48) return m % 60 ? `${h} h ${m % 60} min` : `${h} h`;
  return `${Math.floor(h / 24)} days`;
}

/** @param {number} t seconds since 1970 @returns {string} e.g. "12 min ago" */
function ago(t) {
  return `${duration(Date.now() / 1000 - t)} ago`;
}

/**
 * A local time stamp ("2026-10-04 10:53:33", as carl-sync.py writes it) as "today 10:53",
 * "yesterday 10:53" or "2026-10-02 10:53".
 * @param {unknown} stamp
 * @param {Date} [now]
 * @returns {string}
 */
export function when(stamp, now = new Date()) {
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(String(stamp ?? ""));
  if (!m) return "at an unknown time";
  const day = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])).getTime();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const days = Math.round((today - day) / 86_400_000);
  const hm = `${m[4]}:${m[5]}`;
  return days === 0 ? `today ${hm}` : days === 1 ? `yesterday ${hm}` : `${m[1]}-${m[2]}-${m[3]} ${hm}`;
}

/**
 * @typedef {{ id: string, label: string, args: string[], busy?: string }} Action
 * @typedef {{ id: string, title: string, summary: string, lines: string[], details: string[], actions: Action[] }} Section
 */

/**
 * The version of the client package (VERSION in the client folder, written by ./carl.sh package) and the CARL
 * version of the server: the one the sync service last heard from the dashboard API (state server_version), else
 * remote.json "version" (written at each server start; it comes with the package, so it is the same at first);
 * "" when not known.
 * @param {string} bundle
 * @param {JsonObject} remote
 * @param {JsonObject} [st]
 * @returns {{ client: string, server: string }}
 */
export function packageVersions(bundle, remote, st = {}) {
  const ok = (/** @type {unknown} */ v) => (typeof v === "string" && /^[0-9A-Za-z.+-]{1,40}$/.test(v.trim()) ? v.trim() : "");
  return { client: bundle ? ok(text(join(bundle, "VERSION"))) : "", server: ok(st.server_version) || ok(remote.version) };
}

const APPLY = "Apply the new config now";
const INSTALLER = "The installer is ./setup in the client folder, or ./carl.sh install on the server.";

/** The client config sync (carl-sync.py's state). @param {Client} client @returns {Section} */
function syncSection(client) {
  const app = appName(client);
  const st = obj(json(join(CARL, "client-sync.json")));
  const bundle = bundleOf(st);
  const remote = bundle ? jsonObject(join(bundle, "remote.json")) : undefined;
  if (!remote) {
    return {
      id: "sync", title: "Config sync", summary: "this computer runs the server", details: [], actions: [],
      lines: para(["The server runs on this computer, so there is nothing to sync.",
                   "To update the configs of OpenCode and Pi, run ./carl.sh install."]),
    };
  }
  const alive = typeof st.alive === "number" ? st.alive : 0;
  const live = Boolean(st.service && st.connected && alive && Date.now() / 1000 - alive < 90);  // pinged lately
  const auto = st.auto_apply !== false;
  const restart = appliedSinceStart();
  /** @type {string[]} */
  const said = [];
  if (restart) said.push(`A new config came after ${app} started. Restart ${app} to use it.`);
  if (st.pending) said.push(`A new config from the dashboard waits. Select "${APPLY}" to apply it.`);
  said.push(auto ? "New configs that the dashboard sends are applied at once."
                 : "New configs that the dashboard sends wait until you apply them.");
  said.push(!st.service ? "There is no sync service. OpenCode and Pi check for a new config when they start."
    : live ? `The sync service is connected to the dashboard. Last contact: ${ago(alive)}.`
    : "The sync service is not connected. Make sure that the dashboard runs on the server.");
  said.push(st.applied ? `Last config from the dashboard: ${when(st.applied_at)}.` : "No config from the dashboard yet.");
  if (st.error) said.push(`Last error: ${String(st.error)}`);
  const pkg = packageVersions(bundle, remote, st);
  if (pkg.client && pkg.server && pkg.client !== pkg.server) {
    said.push(`The client package is version ${pkg.client}, but the server runs CARL ${pkg.server}.`,
              "To update: make a new client package on the server, unzip it here and run ./setup.");
  }
  const details = para([
    ...(pkg.client ? [`Client package: version ${pkg.client}`] : []),
    ...(pkg.server ? [`Server: CARL ${pkg.server} (${st.server_version ? "last contact with the dashboard" : "from the client package"})`] : []),
    `Server: ${remote.host}:${remote.port}`,
    `Dashboard API: ${remote.cache_api}`,
    ...(st.applied ? [`Config version ${st.applied}, applied ${st.applied_at ?? "at an unknown time"}`] : []),
    ...(st.pending ? [`Waiting config version ${st.pending}`] : []),
    st.service ? "To remove the sync service, run the installer again with NO_SYNC_SERVICE=1."
               : "To add the sync service, run the installer again without NO_SYNC_SERVICE=1.",
    INSTALLER,
  ]);
  /** @type {Action[]} */
  const actions = [
    auto ? { id: "auto-off", label: "Do not apply new configs at once", args: ["auto", "off"] }
         : { id: "auto-on", label: "Apply new configs at once", args: ["auto", "on"] },
    ...(st.pending ? [{ id: "apply", label: APPLY, args: ["apply"], busy: "CARL: applying the new config…" }] : []),
    { id: "check", label: "Check for a new config now", args: ["once"], busy: "CARL: checking for a new config…" },
  ];
  const summary = restart ? `new config: restart ${app} to use it`
    : st.pending ? "a new config waits for you"
    : `${!st.service ? "checks at start" : live ? "sync service connected" : "sync service not connected"} · `
      + (auto ? "applies new configs at once" : "new configs wait for you");
  return { id: "sync", title: "Config sync", summary, lines: para(said), details, actions };
}

/**
 * @typedef {object} Piece
 * @property {string[]} on        what it does when it is on (the first sentence: its state)
 * @property {string[]} off       when it is off
 * @property {string[]} [howOn]   how to turn it off, and the other switches (when it is on)
 * @property {string[]} [howOff]  how to turn it on (when it is off)
 * @property {string} [extra]     the summary's word in brackets when it is on ("exa")
 */

/** @param {Client} client @returns {Section[]} */
function pieces(client) {
  /** @type {Section[]} */
  const out = [];
  const add = (/** @type {string} */ id, /** @type {string} */ title, /** @type {boolean} */ on, /** @type {Piece} */ p) => {
    const summary = on ? (p.extra ? `on (${p.extra})` : "on") : "off";
    const [first, ...rest] = on ? p.on : p.off;
    const how = (on ? p.howOn : p.howOff) ?? [];
    out.push({ id, title, summary, lines: para([`${on ? "On" : "Off"}. ${first}`, ...rest]),
               details: para([...how, INSTALLER]), actions: [] });
  };
  const turnOff = (/** @type {string} */ v) => `To turn it off, run the installer again with ${v}=1.`;
  const turnOn = (/** @type {string} */ v) => `To turn it on, run the installer again with ${v}=0.`;
  const cache = {
    on: ["CARL saves each agent's prompt and each session on the server's disk.",
         "They stay when the server stops, restarts or switches the model.",
         "The dashboard sets the limits of the disk cache (Settings > Caching).",
         "The parts of the prompt that change per project (the folder, the date, AGENTS.md) go after the shared part: " +
         "with Qwen as system text; with Gemma 4 and other models where they are (Settings > Caching > Other templates)."],
    off: ["CARL does not save prompts and sessions on the server's disk."],
    howOn: [turnOff("NO_CACHE"), "On other computers, the disk cache works through the dashboard API."],
    howOff: [turnOn("NO_CACHE")],
  };
  const web = (/** @type {string} */ provider) => ({
    on: [`The search queries go to ${provider === "on" ? "the address in mcp.json" : provider}, outside this computer.`],
    off: ["There is no web search tool."],
    howOn: ["To change the provider, run the installer again with WEB_SEARCH=exa or WEB_SEARCH=parallel.",
            "To turn it off, use WEB_SEARCH=off."],
    howOff: ["To turn it on, run the installer again with WEB_SEARCH=exa or WEB_SEARCH=parallel."],
    extra: provider === "on" ? "" : provider,
  });
  const coder = (/** @type {boolean} */ bg, /** @type {string[]} */ tools) => ({
    on: ["The main agent gives large tasks to the coder subagent.", ...tools,
         ...(bg ? ["Background: on. The main session goes on while the coder works.", "Then both slots are busy."]
                : ["Background: off. The main session waits for the coder."])],
    off: ["The installer turns the coder on when the server runs 2 slots."],
    howOn: ["The installer turns the coder on when the server runs 2 or more slots.",
            "CODER=1 always turns it on. NO_CODER=1 turns it off.",
            bg ? "To turn the background off, run the installer again with NO_BACKGROUND_SUBAGENTS=1."
               : "To turn the background on, run the installer again with NO_BACKGROUND_SUBAGENTS=0."],
    howOff: ["The installer turns the coder on when the server runs 2 or more slots.",
             "CODER=1 always turns it on."],
  });
  const env = text(join(CARL, "opencode.env"));
  if (client === "opencode") {
    const cfg = obj(json(join(OC, "opencode.json")));
    const tui = obj(json(join(OC, "tui.json")));
    const agents = obj(cfg.agent);
    const plugins = JSON.stringify(cfg.plugin ?? []);
    const tuiPlugins = JSON.stringify(tui.plugin ?? []);
    add("cache", "Disk cache", plugins.includes("carl-cache"), cache);
    add("check", "Model check", plugins.includes("carl-model-check"), {
      on: ["OpenCode tells you when the server runs a different model than the one you select."],
      off: ["OpenCode does not compare your model with the model on the server."],
      howOn: [turnOff("NO_MODEL_CHECK")], howOff: [turnOn("NO_MODEL_CHECK")],
    });
    add("switcher", "Session switcher", tuiPlugins.includes("session-switcher"), {
      on: ["The prompt box shows the sessions of this project.",
           "Use ‹ and › to go to the next session, or /switch to select one."],
      off: ["The prompt box shows no session switcher."],
      howOn: [turnOff("NO_SWITCHER")], howOff: [turnOn("NO_SWITCHER")],
    });
    add("sidebar", "Subagents sidebar", tuiPlugins.includes("subagents-sidebar"), {
      on: ["The sidebar shows the subagents of this session and what they do.",
           "In its first line, ✓ counts the subagents that finished and ✗ those that failed."],
      off: ["The sidebar does not show the subagents."],
      howOn: [turnOff("NO_SIDEBAR")], howOff: [turnOn("NO_SIDEBAR")],
    });
    add("coder", "Coder subagent", Boolean(agents.coder || agents["carl-coder"]), coder(
      /OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=1/.test(env),
      ["The coder has every tool but task. It also has LSP and web search when they are on.",
       "It has no browser. It gives the checks of live pages back to the browser agent."]));
    add("browser", "Browser", Boolean(obj(cfg.mcp)["carl-browser"]), {
      on: ["The browser subagent opens and checks web pages. Only it has the browser tools."],
      off: ["There is no browser subagent."],
      howOn: [turnOff("NO_BROWSER"), "To see the browser on the screen, run the installer again with BROWSER_HEADED=1."],
      howOff: [turnOn("NO_BROWSER")],
    });
    add("web", "Web search", Boolean(openCodeSearch(env)), web(openCodeSearch(env)));
    add("lsp", "LSP", cfg.lsp === true && /OPENCODE_EXPERIMENTAL_LSP_TOOL=1/.test(env), {
      on: ["OpenCode downloads and runs language servers for your code."],
      off: ["OpenCode does not run language servers."],
      howOn: [turnOff("NO_LSP")], howOff: [turnOn("NO_LSP")],
    });
  } else {
    const mcp = obj(json(join(PI, "mcp.json")));
    const servers = obj(mcp.mcpServers ?? mcp.servers);
    add("cache", "Disk cache", exists(join(PI, "extensions", "carl-cache", "index.ts")), cache);
    const bg = obj(json(join(PI, "carl.json"))).background_subagents !== false;
    add("coder", "Coder subagent", exists(join(PI, "agents", "coder.md")) || exists(join(PI, "agents", "carl-coder.md")),
        coder(bg, ["The coder has every tool but subagent and tool_search. It has web search when it is on.",
                   "It has no browser. It gives the checks of live pages back to the main agent.",
                   ...(bg ? ["Use /subagents to see the subagents that run in the background."] : [])]));
    add("subagent", "Subagent tool", exists(join(PI, "extensions", "subagent", "index.ts")), {
      on: ["Pi has the subagent tool. The coder needs it."],
      off: ["Pi has no subagent tool. It comes with the coder."],
      howOn: ["The installer adds it with the coder."], howOff: ["The installer adds it with the coder."],
    });
    add("browser", "Browser", Boolean(servers["carl-browser"]), {
      on: ["Pi loads the browser tools (an MCP server) when it needs them."],
      off: ["Pi has no browser tools."],
      howOn: [turnOff("NO_BROWSER")], howOff: [turnOn("NO_BROWSER")],
    });
    const search = piSearch(servers["carl-web-search"]);
    add("web", "Web search", Boolean(search), web(search));
  }
  return out;
}

/** Every section for this client: the sync first. @param {Client} client @returns {Section[]} */
export function sections(client) {
  return [syncSection(client), ...pieces(client)];
}

/**
 * What a toast or a notice says after an action ran (exit code from run(); -1: no carl-sync.py).
 * @param {Action} action
 * @param {number} code
 * @param {Client} client
 * @returns {{ message: string, ok: boolean }}
 */
export function outcome(action, code, client) {
  if (code !== 0) {
    if (code === -1) return { ok: false, message: "CARL cannot find carl-sync.py. Run the installer again." };
    return { ok: false, message: action.id.startsWith("auto")
      ? "CARL could not change the setting. Open /carl to see the error."
      : "CARL could not get the new config. Open /carl to see the error." };
  }
  if (action.id === "auto-off") return { ok: true, message: "CARL: new configs from the dashboard now wait for you." };
  if (action.id === "auto-on") return { ok: true, message: "CARL: new configs from the dashboard are now applied at once." };
  const st = obj(json(join(CARL, "client-sync.json")));
  if (st.error) return { ok: false, message: "CARL could not reach the dashboard. Open /carl to see the error." };
  if (st.pending) return { ok: true, message: "CARL: a new config waits. Open /carl to apply it." };
  if (appliedSinceStart()) return { ok: true, message: restartNotice(client) };
  return { ok: true, message: "CARL: there is no new config." };
}

/**
 * Run carl-sync.py (in the client folder the installer recorded) with args; its exit code (-1: none).
 * @param {string[]} args
 * @returns {Promise<number>}
 */
export function run(args) {
  const bundle = bundleOf(obj(json(join(CARL, "client-sync.json"))));
  const tool = bundle ? join(bundle, "carl-sync.py") : "";
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
  const st = obj(json(join(CARL, "client-sync.json")));
  const bundle = bundleOf(st);
  if (st.service || !bundle || !exists(join(bundle, "remote.json"))) return;
  const p = spawn("python3", [join(bundle, "carl-sync.py"), "once"], { stdio: "ignore", detached: true });
  p.on("error", () => {});
  p.unref();
}
