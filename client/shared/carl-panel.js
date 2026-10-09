// @ts-check
// The /carl panel's content (client/shared/carl-panel.js: OpenCode's carl-panel plugin and Pi's carl-panel
// extension each carry a copy): a control panel. One row for each CARL part on this computer: its label and its
// state (read from the config files the setup wrote and ~/.config/carl/client-install.env); Enter changes it in
// place. No explanations (user, 2026-10-08: "a control panel, not the readme").
//
// panel(client) -> { title, rows }: each row is { id, label, state, kind } with its actions: a switch (on / off), a
// choice (web search: exa, parallel, off), a view (the sync service: label rows) or an action (check for a new
// config). A switch runs `carl-sync.py set KEY=VALUE` (the setup's switches, then the config step again), so /carl,
// ./setup and the dashboard's sync use the same switches. act() runs an action and says what happened (the toast).
// The words follow reference/glossary.md and the 23.2 writing rules.
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
 * @typedef {{ id: string, args: string[], busy: string, row?: string, want?: string, name?: string }} Action
 *   busy: the row's state while it runs ("turning off…"); a switch's action also has the row it changes, the state
 *   it asks for and the part's name in a sentence (the toast).
 * @typedef {object} Row
 * @property {string} id
 * @property {string} label
 * @property {string} state      short, lower case: on, off, exa, connected; "" for an action
 * @property {"switch" | "choice" | "view" | "action"} kind
 *   switch: Enter turns it on or off; choice: Enter opens `values`; view: Enter shows `view`; action: Enter runs it
 * @property {string[]} [values] a choice's values
 * @property {Record<string, string>} [notes] a note next to some of a choice's values (where web search queries go)
 * @property {Record<string, Action>} [actions] a switch's or a choice's action for each other state
 * @property {Action} [action]  an action row's action
 * @property {[string, string][]} [view] a view row's label rows
 * @property {string} [viewTitle] the view's title
 * @typedef {{ title: string, rows: Row[] }} Panel
 *   title: "CARL", or "CARL" and the restart note
 */

/** One reading on one line: the label column, then the value. @param {string} label @param {string} value @param {number} [width] */
export function row(label, value, width = 16) {
  return `${label.padEnd(width - 1)} ${value}`;
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

/** The state of each row before /carl changed it in this run of OpenCode or Pi (it needs a restart). */
const BEFORE = new Map();

/** A switch row. @param {string} id @param {string} label @param {string} name @param {string} key @param {boolean} on @returns {Row} */
function switchRow(id, label, name, key, on) {
  const want = on ? "off" : "on";
  return { id, label, state: on ? "on" : "off", kind: "switch", actions: { [want]: {
    id: `set:${key}`, args: ["set", `${key}=${on ? "1" : "on"}`, ...(key === "NO_CODER" && !on ? ["CODER=1"] : [])],
    busy: `turning ${want}…`, row: id, want, name } } };
}

/** The client config sync (carl-sync.py's state): none when the server runs on this computer. @returns {Row[]} */
function syncRows() {
  const st = obj(json(join(CARL, "client-sync.json")));
  const bundle = bundleOf(st);
  const remote = bundle ? jsonObject(join(bundle, "remote.json")) : undefined;
  if (!remote) return [];
  const alive = typeof st.alive === "number" ? st.alive : 0;
  const live = Boolean(st.service && st.connected && alive && Date.now() / 1000 - alive < 90);  // pinged lately
  const auto = st.auto_apply !== false;
  const service = st.error ? "error" : !st.service ? "not installed" : live ? "connected" : "not connected";
  const pkg = packageVersions(bundle, remote, st);
  /** @type {[string, string][]} */
  const view = [
    ["Sync service", !st.service ? "not installed" : live ? "connected" : "not connected"],
    ...(st.service && alive ? [/** @type {[string, string]} */ (["Last contact", ago(alive)])] : []),
    ["Last config", st.applied ? when(st.applied_at) : "none yet"],
    ...(st.pending ? [/** @type {[string, string]} */ (["Waiting config", String(st.pending)])] : []),
    ...(st.error ? [/** @type {[string, string]} */ (["Last check", `failed: ${String(st.error)}`])] : []),
    ...(pkg.client ? [/** @type {[string, string]} */ (["Client package", pkg.client])] : []),
    ...(pkg.server ? [/** @type {[string, string]} */ (["Server", `CARL ${pkg.server}`])] : []),
    ["Server address", `${remote.host}:${remote.port}`],
    ["Dashboard API", String(remote.cache_api ?? "").replace(/^https?:\/\//, "")],
  ];
  /** @type {Row[]} */
  const out = [
    { id: "sync", label: "Sync service", state: service, kind: "view", viewTitle: "Config sync", view },
    { id: "auto", label: "Apply new configs at once", state: auto ? "on" : "off", kind: "switch", actions: {
      [auto ? "off" : "on"]: { id: auto ? "auto-off" : "auto-on", args: ["auto", auto ? "off" : "on"],
                               busy: `turning ${auto ? "off" : "on"}…` } } },
  ];
  if (st.pending) {
    out.push({ id: "apply", label: "Apply the waiting config", state: "", kind: "action",
               action: { id: "apply", args: ["apply"], busy: "applying…" } });
  }
  out.push({ id: "once", label: "Check for a new config", state: "", kind: "action",
             action: { id: "check", args: ["once"], busy: "checking…" } });
  return out;
}

/** Web search: exa, parallel or off ("on": Pi's entry has another address). @param {string} provider @returns {Row} */
function webRow(provider) {
  const state = provider || "off";
  const values = ["exa", "parallel", "off"];
  /** @type {Record<string, Action>} */
  const actions = {};
  for (const v of values.filter((x) => x !== state)) {
    actions[v] = { id: `set:WEB_SEARCH=${v}`, args: ["set", `WEB_SEARCH=${v}`],
                   busy: v === "off" ? "turning off…" : `switching to ${v}…`, row: "web", want: v, name: "web search" };
  }
  // the notes stay (user, 2026-10-08): they say where the queries go
  const notes = { exa: "Queries go to exa.ai.", parallel: "Queries go to parallel.ai." };
  return { id: "web", label: "Web search", state, kind: "choice", values, notes, actions };
}

/** The plugin entry of OpenCode's opencode.json whose path has `name`: its options, or undefined. @param {unknown} plugins @param {string} name */
function pluginOptions(plugins, name) {
  for (const e of Array.isArray(plugins) ? plugins : []) {
    if (Array.isArray(e) && String(e[0]).includes(name)) return obj(e[1]);
    if (typeof e === "string" && e.includes(name)) return {};
  }
  return undefined;
}

/** The parts' rows, in the order of their groups: coder, tools, side panels, server. @param {Client} client @returns {Row[]} */
function parts(client) {
  const env = switches();
  const envOn = (/** @type {string} */ key) => env[key] !== "1";
  const oc = text(join(CARL, "opencode.env"));
  let coderOn, bg, reminder, browser, search;
  /** @type {Row[]} */
  let panels = [];
  /** @type {Row[]} */
  let server = [];
  /** @type {Row[]} */
  let lsp = [];
  if (client === "opencode") {
    const cfg = obj(json(join(OC, "opencode.json")));
    const tui = JSON.stringify(obj(json(join(OC, "tui.json"))).plugin ?? []);
    const plugins = JSON.stringify(cfg.plugin ?? []);
    const agents = obj(cfg.agent);
    coderOn = Boolean(agents.coder || agents["carl-coder"]);
    bg = coderOn ? /OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=1/.test(oc) : envOn("NO_BACKGROUND_SUBAGENTS");
    const deleg = pluginOptions(cfg.plugin, "carl-delegation");
    reminder = coderOn && deleg ? deleg.reminder !== false : envOn("NO_REMINDER");
    browser = Boolean(obj(cfg.mcp)["carl-browser"]);
    search = openCodeSearch(oc);
    lsp = [switchRow("lsp", "LSP", "LSP", "NO_LSP", cfg.lsp === true && /OPENCODE_EXPERIMENTAL_LSP_TOOL=1/.test(oc))];
    panels = [switchRow("sidebar", "Subagents side panel", "the subagents side panel", "NO_SIDEBAR", tui.includes("subagents-sidebar")),
              switchRow("switcher", "Session switcher", "the session switcher", "NO_SWITCHER", tui.includes("session-switcher"))];
    server = [switchRow("cache", "Disk cache", "the disk cache", "NO_CACHE", plugins.includes("carl-cache")),
              switchRow("check", "Model check", "the model check", "NO_MODEL_CHECK", plugins.includes("carl-model-check"))];
  } else {
    const mcp = obj(json(join(PI, "mcp.json")));
    const servers = obj(mcp.mcpServers ?? mcp.servers);
    const st = obj(json(join(PI, "carl.json")));
    coderOn = exists(join(PI, "agents", "coder.md")) || exists(join(PI, "agents", "carl-coder.md"));
    bg = coderOn ? st.background_subagents !== false : envOn("NO_BACKGROUND_SUBAGENTS");
    reminder = coderOn ? obj(st.delegation).reminder !== false : envOn("NO_REMINDER");
    browser = Boolean(servers["carl-browser"]);
    search = piSearch(servers["carl-web-search"]);
    server = [switchRow("cache", "Disk cache", "the disk cache", "NO_CACHE", exists(join(PI, "extensions", "carl-cache", "index.ts")))];
  }
  return [
    switchRow("coder", "Coder subagent", "the coder subagent", "NO_CODER", coderOn),
    // they do nothing without the coder (user, 2026-10-08: they disappear while it is off)
    ...(coderOn ? [switchRow("background", "Background coder", "the background coder", "NO_BACKGROUND_SUBAGENTS", bg),
                   switchRow("reminder", "Delegation reminder", "the delegation reminder", "NO_REMINDER", reminder)] : []),
    switchRow("browser", "Browser", "the browser", "NO_BROWSER", browser),
    webRow(search),
    ...lsp,
    ...panels,
    ...server,
  ];
}

/** Every row for this client (the parts, then the config sync) and the title. @param {Client} client @returns {Panel} */
export function panel(client) {
  const rows = [...parts(client), ...syncRows()];
  const changed = rows.filter((r) => BEFORE.has(r.id) && BEFORE.get(r.id) !== r.state).length
    + (appliedSinceStart() ? 1 : 0);
  const title = changed ? `Restart ${appName(client)} to use ${changed === 1 ? "1 change" : `${changed} changes`}.` : "";
  return { title, rows };
}

/** A view row's label rows as text, the labels in one column. @param {[string, string][]} view */
export function viewText(view) {
  const w = Math.max(...view.map(([a]) => a.length)) + 2;
  return view.map(([a, b]) => row(a, b, w)).join("\n");
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
      ? "CARL could not change the setting. Sync service in /carl shows the error."
      : "CARL could not get the new config. Sync service in /carl shows the error." };
  }
  if (action.id === "auto-off") return { ok: true, message: "New configs from the dashboard now wait for you." };
  if (action.id === "auto-on") return { ok: true, message: "New configs from the dashboard are now applied at once." };
  const st = obj(json(join(CARL, "client-sync.json")));
  if (st.error) return { ok: false, message: "CARL could not reach the dashboard. Sync service in /carl shows the error." };
  if (st.pending) return { ok: true, message: "A new config waits. Select Apply the waiting config in /carl." };
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
  const now = panel(client).rows.find((r) => r.id === action.row)?.state ?? "";
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
    const cur = panel(client).rows.find((r) => r.id === action.row);
    if (cur) BEFORE.set(action.row, cur.state);
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
