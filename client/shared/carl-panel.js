// @ts-check
// The /carl panel's content (client/shared/carl-panel.js: OpenCode's carl-panel plugin and Pi's carl-panel
// extension each carry a copy): a control panel. One row for each CARL part on this computer: its label and its
// state (read from the config files the setup wrote and ~/.config/carl/client-install.env), and the actions that
// change it. A switch runs `carl-sync.py set KEY=VALUE` (the setup's switches, then the config step again), so
// /carl, ./setup and the dashboard's sync use the same switches. The config sync has its own rows.
//
// sections(client) -> [{ id, title, summary, row, lines, details, actions }]: `row` is the list's line (the label
// column, then the state: "Coder subagent             on"); `lines` (whole sentences) and `details` (label rows:
// the setup switch, the addresses, the versions) show only when the row is opened. act() runs an action and says
// what happened. Every line is at most 90 characters: OpenCode's dialog cuts at about 100 columns. The words
// follow reference/glossary.md and the 23.2 writing rules (one reading per row, whole sentences).
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

/** Program names in a sentence: "OpenCode", "OpenCode and Pi". @param {Client[]} clients */
function appNames(clients) {
  return clients.map(appName).join(" and ");
}

/**
 * What OpenCode and Pi say when their config changed while they run: what CARL did, then who must restart
 * (`clients`, this one by default). `newTerminal`: OpenCode reads that switch from the shell (opencode.env).
 * @param {Client} client
 * @param {string} [did]
 * @param {Client[]} [clients]
 * @param {boolean} [newTerminal]
 */
export function restartNotice(client, did = "CARL updated the model list.", clients = [client], newTerminal = false) {
  const who = clients.length ? clients : [client];
  return `${did} Restart ${appNames(who)} to use it.`
    + (newTerminal && who.includes("opencode") ? " Start OpenCode from a new terminal." : "");
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
 * @typedef {{ id: string, label: string, args: string[], busy?: string, row?: string, want?: string, name?: string }} Action
 *   A switch's action also has the row it changes, the state it asks for and the part's name in a sentence.
 * @typedef {{ id: string, title: string, summary: string, row: string, lines: string[], details: string[], actions: Action[] }} Section
 *   title: the label; summary: the state (on, off, exa); row: the list's line (label column, then the state).
 */

/** The width of the label column (the longest label is 25 characters). */
const LABEL = 27;

/** One reading on one line: the label column, then the value. @param {string} label @param {string} value */
export function row(label, value) {
  return `${label.padEnd(LABEL - 1)} ${value}`;
}

/** @param {string} s */
function cap(s) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/**
 * @param {string} id @param {string} title @param {string} summary
 * @param {string[]} lines @param {string[]} details @param {Action[]} actions
 * @returns {Section}
 */
function section(id, title, summary, lines, details, actions) {
  return { id, title, summary, row: row(title, summary), lines, details, actions };
}

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

/**
 * The setup's switches: ~/.config/carl/client-install.env (KEY=value lines; ./setup and `carl-sync.py set` write it).
 * @returns {Record<string, string>}
 */
export function switches() {
  /** @type {Record<string, string>} */
  const out = {};
  for (const line of text(join(CARL, "client-install.env")).split("\n")) {
    const m = /^\s*([A-Z_]+)=(.*?)\s*$/.exec(line);
    if (m) out[m[1]] = m[2];
  }
  return out;
}

const APPLY = "Apply the new config now";
const SETUP = "The setup is ./setup in the client folder, or ./carl.sh install on the server.";
const SAME = "The setup and the sync of the dashboard use the same switches as /carl.";

/** The state of each row before /carl changed it in this run of OpenCode or Pi (it needs a restart). */
const BEFORE = new Map();

/** A sentence when /carl changed this row since this OpenCode or Pi started. @param {Client} client @param {string} id @param {string} state */
function restartLine(client, id, state) {
  return BEFORE.has(id) && BEFORE.get(id) !== state ? [`Restart ${appName(client)} to use the change.`] : [];
}

/** The client config sync (carl-sync.py's state): its row, and the row that applies new configs at once. @param {Client} client @returns {Section[]} */
function syncRows(client) {
  const app = appName(client);
  const st = obj(json(join(CARL, "client-sync.json")));
  const bundle = bundleOf(st);
  const remote = bundle ? jsonObject(join(bundle, "remote.json")) : undefined;
  if (!remote) {
    return [section("sync", "Config sync", "not needed", para([
      "The server runs on this computer, so there is nothing to sync.",
      "To update the configs of OpenCode and Pi, run ./carl.sh install."]), [], [])];
  }
  const alive = typeof st.alive === "number" ? st.alive : 0;
  const live = Boolean(st.service && st.connected && alive && Date.now() / 1000 - alive < 90);  // pinged lately
  const auto = st.auto_apply !== false;
  const restart = appliedSinceStart();
  const service = !st.service ? "none" : live ? "connected" : "not connected";
  /** @type {string[]} */
  const said = [];
  if (restart) said.push(`A new config came after ${app} started. Restart ${app} to use it.`);
  if (st.pending) said.push(`A new config from the dashboard waits. Select "${APPLY}" to apply it.`);
  said.push(!st.service ? "OpenCode and Pi check for a new config when they start."
    : live ? "The sync service applies each new config that the dashboard sends."
    : "Make sure that the dashboard runs on the server.");
  if (st.error) said.push("The last check failed.", String(st.error));
  const pkg = packageVersions(bundle, remote, st);
  if (pkg.client && pkg.server && pkg.client !== pkg.server) {
    said.push(`The client package is version ${pkg.client}, but the server runs CARL ${pkg.server}.`,
              "To update: make a new client package on the server, unzip it here and run ./setup.");
  }
  const lines = [
    row("Sync service", service),
    ...(st.service && alive ? [row("Last contact", ago(alive))] : []),
    row("Last config", st.applied ? when(st.applied_at) : "none yet"),
    ...para(said),
  ];
  const details = [
    ...(pkg.client ? [row("Client package", `version ${pkg.client}`)] : []),
    ...(pkg.server ? [row("Server version", `CARL ${pkg.server}`), row("Server version from",
                          st.server_version ? "the last contact with the dashboard" : "the client package")] : []),
    row("Server address", `${remote.host}:${remote.port}`),
    row("Dashboard API", String(remote.cache_api)),
    ...(st.applied ? [row("Config version", String(st.applied)), row("Applied", String(st.applied_at ?? "at an unknown time"))] : []),
    ...(st.pending ? [row("Waiting config version", String(st.pending))] : []),
    ...para([st.service ? "To remove the sync service, run the setup again with NO_SYNC_SERVICE=1."
                        : "To add the sync service, run the setup again without NO_SYNC_SERVICE=1.", SETUP]),
  ];
  /** @type {Action[]} */
  const actions = [
    ...(st.pending ? [{ id: "apply", label: APPLY, args: ["apply"], busy: "CARL is applying the new config…" }] : []),
    { id: "check", label: "Check for a new config now", args: ["once"], busy: "CARL is checking for a new config…" },
  ];
  const summary = restart ? "restart needed" : st.pending ? "a config waits" : !st.service ? "checks at start" : service;
  return [
    section("sync", "Config sync", summary, lines, details, actions),
    section("auto", "Apply new configs at once", auto ? "on" : "off",
      para(auto ? ["This setting is on. New configs that the dashboard sends are applied at once."]
                : ["This setting is off. New configs that the dashboard sends wait until you apply them.",
                   "When a config waits, open Config sync to apply it."]),
      [],
      [auto ? { id: "auto-off", label: "Turn it off", args: ["auto", "off"] }
            : { id: "auto-on", label: "Turn it on", args: ["auto", "on"] }]),
  ];
}

/**
 * @typedef {object} Part
 * @property {string} id
 * @property {string} title     the label
 * @property {string} name      its name in a sentence ("the coder subagent")
 * @property {string} key       the setup's switch (1 turns it off)
 * @property {boolean} on
 * @property {string[]} onText  what it does when it is on
 * @property {string[]} offText when it is off
 * @property {string[]} [also]  sentences in both states, after the others
 * @property {string[]} [more]  details (sentences) before the switch rows
 */

/** The switch rows of the details. @param {string} key @param {Record<string, string>} env */
function switchDetails(key, env) {
  return [row("Setup switch", key), row("In client-install.env", key in env ? `${key}=${env[key]}` : "not set")];
}

/** A part with an on / off switch. @param {Client} client @param {Part} p @param {Record<string, string>} env @returns {Section} */
function toggle(client, p, env) {
  const state = p.on ? "on" : "off";
  const want = p.on ? "off" : "on";
  return section(p.id, p.title, state,
    para([`${cap(p.name)} is ${state}.`, ...(p.on ? p.onText : p.offText), ...(p.also ?? []),
          ...restartLine(client, p.id, state)]),
    [...switchDetails(p.key, env), ...para([...(p.more ?? []), SAME, SETUP])],
    [{ id: `set:${p.key}`, label: `Turn it ${want}`, args: ["set", `${p.key}=${p.on ? "1" : "on"}`,
                                                                         ...(p.key === "NO_CODER" && !p.on ? ["CODER=1"] : [])],
       busy: `CARL is turning ${p.name} ${want}…`, row: p.id, want, name: p.name }]);
}

/** Web search: exa, parallel or off ("on": Pi's entry has another address). @param {Client} client @param {string} provider @param {Record<string, string>} env @returns {Section} */
function webRow(client, provider, env) {
  const state = provider || "off";
  const where = provider === "on" ? "the address in mcp.json" : provider;
  return section("web", "Web search", state,
    para([...(provider ? ["Web search is on.", `The search queries go to ${where}, outside this computer.`]
                       : ["Web search is off.", "When it is on, the search queries go to exa or parallel, outside this computer."]),
          "Everything else stays on this computer.", ...restartLine(client, "web", state)]),
    [...switchDetails("WEB_SEARCH", env), ...para([SAME, SETUP])],
    ["exa", "parallel", "off"].filter((v) => v !== state).map((v) => ({
      id: `set:WEB_SEARCH=${v}`, label: v === "off" ? "Turn it off" : `Use ${v}`, args: ["set", `WEB_SEARCH=${v}`],
      busy: v === "off" ? "CARL is turning web search off…" : `CARL is setting web search to ${v}…`,
      row: "web", want: v, name: "web search",
    })));
}

/** The plugin entry of OpenCode's opencode.json whose path has `name`: its options, or undefined. @param {unknown} plugins @param {string} name */
function pluginOptions(plugins, name) {
  for (const e of Array.isArray(plugins) ? plugins : []) {
    if (Array.isArray(e) && String(e[0]).includes(name)) return obj(e[1]);
    if (typeof e === "string" && e.includes(name)) return {};
  }
  return undefined;
}

/** @param {Client} client @returns {Section[]} */
function parts(client) {
  const env = switches();
  const envOn = (/** @type {string} */ key) => env[key] !== "1";
  const oc = text(join(CARL, "opencode.env"));
  const noCoder = ["The coder subagent is off, so this setting has no effect now."];
  /** @type {Section[]} */
  const out = [];
  const add = (/** @type {Part} */ p) => out.push(toggle(client, p, env));
  let coderOn, bg, reminder, browser, search;
  /** @type {string[]} */
  let tools;
  if (client === "opencode") {
    const cfg = obj(json(join(OC, "opencode.json")));
    const agents = obj(cfg.agent);
    coderOn = Boolean(agents.coder || agents["carl-coder"]);
    bg = coderOn ? /OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=1/.test(oc) : envOn("NO_BACKGROUND_SUBAGENTS");
    const deleg = pluginOptions(cfg.plugin, "carl-delegation");
    reminder = coderOn && deleg ? deleg.reminder !== false : envOn("NO_REMINDER");
    browser = Boolean(obj(cfg.mcp)["carl-browser"]);
    search = openCodeSearch(oc);
    tools = ["The coder has every tool but task. It also has LSP and web search when they are on.",
             "It has no browser. It gives the checks of live pages back to the browser agent."];
  } else {
    const mcp = obj(json(join(PI, "mcp.json")));
    const servers = obj(mcp.mcpServers ?? mcp.servers);
    const st = obj(json(join(PI, "carl.json")));
    coderOn = exists(join(PI, "agents", "coder.md")) || exists(join(PI, "agents", "carl-coder.md"));
    bg = coderOn ? st.background_subagents !== false : envOn("NO_BACKGROUND_SUBAGENTS");
    reminder = coderOn ? obj(st.delegation).reminder !== false : envOn("NO_REMINDER");
    browser = Boolean(servers["carl-browser"]);
    search = piSearch(servers["carl-web-search"]);
    tools = ["The coder has every tool but subagent and tool_search. It has web search when it is on.",
             "It has no browser. It gives the checks of live pages back to the main agent."];
  }
  add({ id: "coder", title: "Coder subagent", name: "the coder subagent", key: "NO_CODER", on: coderOn,
        onText: ["The main agent gives large tasks to the coder subagent.", ...tools],
        offText: ["The main agent does all the work itself."],
        more: ["Without a choice, the setup turns the coder on only when the server runs 2 or more slots.",
               "Turn it on here and it stays on, also with 1 slot: a subagent then takes the slot of the main session.",
               ...(client === "pi" ? ["The subagent tool of Pi comes with the coder."] : [])] });
  add({ id: "background", title: "Background coder", name: "the background coder", key: "NO_BACKGROUND_SUBAGENTS",
        on: bg,
        onText: ["The main session goes on while the coder works. Then both slots are busy.",
                 ...(client === "pi" ? ["Use /subagents to see the subagents that run in the background."] : [])],
        offText: ["The main session waits until the coder is finished."], also: coderOn ? [] : noCoder });
  add({ id: "reminder", title: "Delegation reminder", name: "the delegation reminder", key: "NO_REMINDER", on: reminder,
        onText: ["Each of your messages to the main agent ends with a short reminder about the coder.",
                 "It helps the main agent give large tasks and stuck tasks to the coder."],
        offText: ["Your messages go to the main agent as you write them."], also: coderOn ? [] : noCoder });
  add({ id: "browser", title: "Browser", name: "the browser", key: "NO_BROWSER", on: browser,
        onText: client === "opencode"
          ? ["The browser subagent opens and checks web pages. Only it has the browser tools."]
          : ["Pi loads the browser tools (an MCP server) when it needs them."],
        offText: client === "opencode" ? ["There is no browser subagent."] : ["Pi has no browser tools."],
        more: ["To see the browser on the screen, run the setup again with BROWSER_HEADED=1."] });
  out.push(webRow(client, search, env));
  if (client === "opencode") {
    const cfg = obj(json(join(OC, "opencode.json")));
    const tui = obj(json(join(OC, "tui.json")));
    const plugins = JSON.stringify(cfg.plugin ?? []);
    const tuiPlugins = JSON.stringify(tui.plugin ?? []);
    add({ id: "lsp", title: "LSP", name: "LSP", key: "NO_LSP",
          on: cfg.lsp === true && /OPENCODE_EXPERIMENTAL_LSP_TOOL=1/.test(oc),
          onText: ["OpenCode downloads and runs language servers for your code."],
          offText: ["OpenCode does not run language servers."] });
    add({ id: "sidebar", title: "Subagents side panel", name: "the subagents side panel", key: "NO_SIDEBAR",
          on: tuiPlugins.includes("subagents-sidebar"),
          onText: ["The side panel shows the subagents of this session and what they do.",
                   "In its first line, ✓ counts the subagents that finished and ✗ those that failed."],
          offText: ["The side panel does not show the subagents."] });
    add({ id: "switcher", title: "Session switcher", name: "the session switcher", key: "NO_SWITCHER",
          on: tuiPlugins.includes("session-switcher"),
          onText: ["The prompt box shows the sessions of this project.",
                   "Use ‹ and › to go to the next session, or /switch to select one."],
          offText: ["The prompt box shows no session switcher."] });
    add(cachePart(plugins.includes("carl-cache")));
    add({ id: "check", title: "Model check", name: "the model check", key: "NO_MODEL_CHECK",
          on: plugins.includes("carl-model-check"),
          onText: ["OpenCode tells you when the server runs a different model than the one you select."],
          offText: ["OpenCode does not compare your model with the model on the server."] });
  } else {
    add(cachePart(exists(join(PI, "extensions", "carl-cache", "index.ts"))));
  }
  return out;
}

/** The disk cache. @param {boolean} on @returns {Part} */
function cachePart(on) {
  return { id: "cache", title: "Disk cache", name: "the disk cache", key: "NO_CACHE", on,
    onText: ["CARL saves each agent's prompt and each session on the server's disk.",
             "They stay when the server stops, restarts or switches the model.",
             "The dashboard sets the limits of the disk cache (Settings > Caching).",
             "The parts of the prompt that change per project (the folder, the date, AGENTS.md) go after the shared " +
             "part: with Qwen as system text; with Gemma 4 and other models where they are (Settings > Caching > " +
             "Other templates)."],
    offText: ["CARL does not save prompts and sessions on the server's disk."],
    more: ["On other computers, the disk cache works through the dashboard API."] };
}

/** Every row for this client: the config sync first. @param {Client} client @returns {Section[]} */
export function sections(client) {
  return [...syncRows(client), ...parts(client)];
}

/**
 * The JSON that carl-sync.py printed (`set`: { changed, ok, error, restart, new_terminal }), or {}.
 * @param {string} out
 * @returns {JsonObject}
 */
function parsed(out) {
  try {
    return obj(JSON.parse(out));
  } catch {
    return {};
  }
}

/**
 * What a toast or a notice says after an action ran (exit code from run(); -1: no carl-sync.py). A switch's
 * action also reads the row again, and `result` (what `carl-sync.py set` printed) says who must restart.
 * @param {Action} action
 * @param {number} code
 * @param {Client} client
 * @param {JsonObject} [result]
 * @returns {{ message: string, ok: boolean }}
 */
export function outcome(action, code, client, result = {}) {
  if (code === -1) return { ok: false, message: "CARL cannot find carl-sync.py. Run the setup again." };
  if (action.args[0] === "set") return switchOutcome(action, code, client, result);
  if (code !== 0) {
    return { ok: false, message: action.id.startsWith("auto")
      ? "CARL could not change the setting. Open /carl to see the error."
      : "CARL could not get the new config. Open /carl to see the error." };
  }
  if (action.id === "auto-off") return { ok: true, message: "New configs from the dashboard now wait for you." };
  if (action.id === "auto-on") return { ok: true, message: "New configs from the dashboard are now applied at once." };
  const st = obj(json(join(CARL, "client-sync.json")));
  if (st.error) return { ok: false, message: "CARL could not reach the dashboard. Open /carl to see the error." };
  if (st.pending) return { ok: true, message: "A new config waits. Open /carl to apply it." };
  if (appliedSinceStart()) return { ok: true, message: restartNotice(client) };
  return { ok: true, message: "There is no new config." };
}

/** After `carl-sync.py set`. @param {Action} action @param {number} code @param {Client} client @param {JsonObject} result */
function switchOutcome(action, code, client, result) {
  if (code !== 0) {
    const why = typeof result.error === "string" && result.error ? result.error
      : "Its output is in ~/.config/carl/client-sync.log.";
    return { ok: false, message: `CARL could not change the setting. ${why}` };
  }
  const name = action.name ?? "the part";
  const now = sections(client).find((s) => s.id === action.row)?.summary ?? "";
  if (action.want && now !== action.want) {
    return { ok: false, message: `CARL changed the setting, but ${name} is still ${now || "the same"}. The setup's `
      + "output is in ~/.config/carl/client-sync.log." };
  }
  const did = action.want === "on" || action.want === "off" ? `CARL turned ${name} ${action.want}.`
    : `CARL set ${name} to ${action.want}.`;
  const apps = /** @type {Client[]} */ ((Array.isArray(result.restart) ? result.restart : [client])
    .filter((c) => c === "opencode" || c === "pi"));
  return { ok: true, message: restartNotice(client, did, apps, result.new_terminal === true) };
}

/**
 * Run carl-sync.py (in the client folder the setup recorded) with args: its exit code (-1: none) and what it printed.
 * @param {string[]} args
 * @returns {Promise<{ code: number, out: string }>}
 */
function exec(args) {
  const bundle = bundleOf(obj(json(join(CARL, "client-sync.json"))));
  const tool = bundle ? join(bundle, "carl-sync.py") : "";
  if (!tool || !exists(tool)) return Promise.resolve({ code: -1, out: "" });
  return new Promise((ok) => {
    let out = "";
    const p = spawn("python3", [tool, ...args], { stdio: ["ignore", "pipe", "ignore"] });
    p.stdout?.on("data", (d) => {
      if (out.length < 1 << 16) out += String(d);
    });
    p.on("close", (code) => ok({ code: code ?? -1, out }));
    p.on("error", () => ok({ code: -1, out }));
  });
}

/**
 * Run carl-sync.py with args; its exit code (-1: none).
 * @param {string[]} args
 * @returns {Promise<number>}
 */
export function run(args) {
  return exec(args).then((r) => r.code);
}

/**
 * Run an action of a row and say what happened (the toast's text). A switch notes the row's state first, so the
 * opened row can say that OpenCode or Pi must restart.
 * @param {Action} action
 * @param {Client} client
 * @returns {Promise<{ message: string, ok: boolean }>}
 */
export async function act(action, client) {
  if (action.row && !BEFORE.has(action.row)) {
    const cur = sections(client).find((s) => s.id === action.row);
    if (cur) BEFORE.set(action.row, cur.summary);
  }
  const r = await exec(action.args);
  return outcome(action, r.code, client, parsed(r.out));
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
