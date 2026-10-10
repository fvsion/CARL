// @ts-check
// The /carl panel's content (client/shared/carl-panel.js: OpenCode's carl-panel plugin and Pi's carl-panel
// extension each carry a copy): a control panel. One row for each CARL part on this computer: its label and its
// state (read from the config files the setup wrote and ~/.config/carl/client-install.env); Enter changes it in
// place. No explanations (user, 2026-10-08: "a control panel, not the readme"). Row labels are titles (user,
// 2026-10-10): every word capitalized but the small ones ("Coder Subagent", "Apply the Waiting Config"); values stay
// lower case.
//
// panel(client) -> { title, rows }: each row is { id, label, state, kind } with its actions: a switch (on / off), a
// choice (web search: exa, parallel, off), a list (Coder subagent: its own rows), a view (the sync service: label
// rows) or an action (check for a new config). A switch runs `carl-sync.py set KEY=VALUE` (the setup's switches,
// then the config step again), so /carl, ./setup and the dashboard's sync use the same switches. act() runs an
// action and says what happened (the toast). The words follow reference/glossary.md and the 23.2 writing rules.
//
// Phase 23.4.4: the coder's settings are a sub-list under one top-level row "Coder Subagent" (its state: the coder's
// on / off, and a "›": it opens a list): Coder, Background coder, Delegation reminder (these two only while the coder
// is on), Coder thinking and Coder model. Coder thinking overrides the dashboard's Coder thinking on this computer
// only, per model (CODER_THINKING=MODEL:VALUE in client-install.env, through `carl-sync.py set`; the setup merges it;
// "dashboard default" removes the model's entry). The row is for the model the coder runs on: Coder model "same as
// main" is the model of the session /carl is opened in (the front end passes it: Session); without one (no session
// yet, a model that is not CARL's) the config's default model.
//
// Phase 23.4.5: Coder model offers "same as main" and the external models this computer's client can use (the front
// end asks the client and passes them: Session.external; CARL's own providers are left out, Phase 29 adds CARL's
// other models). Choosing one runs `carl-sync.py set CODER_MODEL=PROVIDER/MODEL` ("main": back to the session's
// model); the setup writes it into the client (OpenCode: the coder agent's "model"; Pi: carl.json "coder_model").
// With an external model, Coder thinking is that model's: "model default" plus the levels the client knows for it
// (CODER_MODEL_THINKING); the dashboard's per-model table does not apply. The provider's keys are the client's own:
// nothing here reads them.
//
// Phase 23.4.6: Tests (after Delegation reminder, only while the coder is on): when CARL's test session runs for new
// code: before code (the default), after code, off. `carl-sync.py set CODER_TESTS=VALUE`; the setup writes it into
// both state files (carl.json "coder_tests"), which the chain reads at each coder task: no restart in either client.
// The 23.4.3 addendum: Request Check (after Tests): on, single reminder (the default), off; CODER_REQUEST_CHECK, carl.json
// "request_check", read by carl-delegation at each coder task: no restart.
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
 * @typedef {{ id: string, args: string[], busy: string, row?: string, want?: string, name?: string, did?: string,
 *             live?: Client[] }} Action
 *   busy: the row's state while it runs ("turning off…"); a switch's action also has the row it changes, the state
 *   it asks for and the part's name in a sentence (the toast); did: the toast's first sentences instead of "CARL set
 *   NAME to WANT."; live: the clients that use the change without a restart (Pi reads the coder's thinking each time
 *   it starts the coder).
 * @typedef {object} Row
 * @property {string} id
 * @property {string} label
 * @property {string} state      short, lower case: on, off, exa, connected; "" for an action
 * @property {"switch" | "choice" | "list" | "view" | "action"} kind
 *   switch: Enter turns it on or off; choice: Enter opens `values` (nothing with fewer than two); list: Enter opens
 *   `rows`; view: Enter shows `view`; action: Enter runs it
 * @property {string} [value]    a choice's current value when `state` shows it in words ("main": same as main)
 * @property {string[]} [values] a choice's values
 * @property {Record<string, string>} [titles] a choice's values in words, where they differ ("main": same as main)
 * @property {Record<string, string>} [notes] a note next to some of a choice's values (where web search queries go)
 * @property {Record<string, Action>} [actions] a switch's or a choice's action for each other state
 * @property {Action} [action]  an action row's action
 * @property {Row[]} [rows]     a list's rows
 * @property {string} [listTitle] the list's title
 * @property {boolean} [arrow]  a list row whose state shows a "›" (it opens a list: Coder subagent)
 * @property {string} [choiceTitle] a choice's title over its values, where it differs from the label
 * @property {[string, string][]} [view] a view row's label rows
 * @property {string} [viewTitle] the view's title
 * @typedef {{ title: string, rows: Row[] }} Panel
 * @typedef {{ model?: string, external?: ExternalModel[] }} Session
 *   the session /carl is opened in: its model as "provider/model" (OpenCode: the newest message of the session;
 *   Pi: ctx.model); none on OpenCode's home screen or before the first message. external: the models this
 *   computer's client can use (openCodeModels, piModels), for Coder model
 * @typedef {{ ref: string, host: string, cost: "free" | "paid" | "", levels: string[] }} ExternalModel
 *   ref: "provider/model"; host: where its requests go (the host of the base URL, else the provider's name); cost:
 *   "free" when the client's model info says that the model costs 0, "paid" when more, "" when not known; levels:
 *   the thinking levels the client knows for it (OpenCode: the model's variants; Pi: its thinking levels)
 *   title: "CARL", or "CARL" and the restart note
 * @typedef {{ message: string, ok: boolean, warn?: boolean }} Said
 *   what the toast or the notice says; ok false: an error; warn: a warning (the change is done)
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
    ["Sync Service", !st.service ? "not installed" : live ? "connected" : "not connected"],
    ...(st.service && alive ? [/** @type {[string, string]} */ (["Last Contact", ago(alive)])] : []),
    ["Last Config", st.applied ? when(st.applied_at) : "none yet"],
    ...(st.pending ? [/** @type {[string, string]} */ (["Waiting Config", String(st.pending)])] : []),
    ...(st.error ? [/** @type {[string, string]} */ (["Last Check", `failed: ${String(st.error)}`])] : []),
    ...(pkg.client ? [/** @type {[string, string]} */ (["Client Package", pkg.client])] : []),
    ...(pkg.server ? [/** @type {[string, string]} */ (["Server", `CARL ${pkg.server}`])] : []),
    ["Server Address", `${remote.host}:${remote.port}`],
    ["Dashboard API", String(remote.cache_api ?? "").replace(/^https?:\/\//, "")],
  ];
  /** @type {Row[]} */
  const out = [
    { id: "sync", label: "Sync Service", state: service, kind: "view", viewTitle: "Config Sync", view },
    { id: "auto", label: "Apply New Configs at Once", state: auto ? "on" : "off", kind: "switch", actions: {
      [auto ? "off" : "on"]: { id: auto ? "auto-off" : "auto-on", args: ["auto", auto ? "off" : "on"],
                               busy: `turning ${auto ? "off" : "on"}…` } } },
  ];
  if (st.pending) {
    out.push({ id: "apply", label: "Apply the Waiting Config", state: "", kind: "action",
               action: { id: "apply", args: ["apply"], busy: "applying…" } });
  }
  out.push({ id: "once", label: "Check for a New Config", state: "", kind: "action",
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
  return { id: "web", label: "Web Search", state, kind: "choice", values, notes, actions };
}

/** The plugin entry of OpenCode's opencode.json whose path has `name`: its options, or undefined. @param {unknown} plugins @param {string} name */
function pluginOptions(plugins, name) {
  for (const e of Array.isArray(plugins) ? plugins : []) {
    if (Array.isArray(e) && String(e[0]).includes(name)) return obj(e[1]);
    if (typeof e === "string" && e.includes(name)) return {};
  }
  return undefined;
}

// ------------------------------------------------------------------ the external models (Phase 23.4.5)

/** A coder model as CODER_MODEL takes it: "provider/model" (the model may have / : @ + in it: openrouter's ids). */
export const CODER_MODEL_RE = /^[A-Za-z0-9][A-Za-z0-9._-]*\/[A-Za-z0-9][A-Za-z0-9._:\/@+-]*$/;
/** A thinking level or variant name as CODER_MODEL_THINKING takes it. */
export const LEVEL_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$/;

/** A model ref that CODER_MODEL can hold. @param {unknown} v */
function isRef(v) {
  return typeof v === "string" && v.length <= 200 && CODER_MODEL_RE.test(v);
}

/** The host of a URL ("openrouter.ai"), or "". @param {unknown} url */
export function hostOf(url) {
  try {
    return typeof url === "string" && url ? new URL(url).hostname : "";
  } catch {
    return "";
  }
}

/**
 * "free" when the client's cost info says the model costs 0 (input, output and the cache prices it has), "paid" when
 * one is more, "" when there is no cost info.
 * @param {unknown} cost @returns {"free" | "paid" | ""}
 */
export function costOf(cost) {
  const c = obj(cost);
  const cache = obj(c.cache);
  const prices = [c.input, c.output, c.cacheRead, c.cacheWrite, cache.read, cache.write].filter((x) => typeof x === "number");
  if (typeof c.input !== "number" || typeof c.output !== "number") return "";
  return prices.every((x) => x === 0) ? "free" : "paid";
}

/**
 * OpenCode's models this computer can use (the TUI's state.provider: the connected providers, each with its models):
 * one entry per model. The host: the provider's baseURL, else the model's API URL, else the provider's name. Only
 * these fields are read (never the provider's key or options.apiKey).
 * @param {unknown} providers @returns {ExternalModel[]}
 */
export function openCodeModels(providers) {
  /** @type {ExternalModel[]} */
  const out = [];
  for (const p of Array.isArray(providers) ? providers : []) {
    const prov = obj(p);
    const id = typeof prov.id === "string" ? prov.id : "";
    const base = obj(prov.options).baseURL;
    for (const [key, m] of Object.entries(obj(prov.models))) {
      const model = obj(m);
      const ref = `${id}/${typeof model.id === "string" ? model.id : key}`;
      if (!isRef(ref)) continue;
      const host = hostOf(base) || hostOf(obj(model.api).url) || String(prov.name ?? id);
      out.push({ ref, host, cost: costOf(model.cost), levels: Object.keys(obj(model.variants)).filter((v) => LEVEL_RE.test(v)) });
    }
  }
  return out;
}

/**
 * Pi's models this computer can use (ctx.modelRegistry.getAvailable(): the models with set-up auth): one entry per
 * model. levels: the thinking levels Pi knows for it (pi-ai's getSupportedThinkingLevels). Only these fields are
 * read (never a key).
 * @param {unknown} models @param {(m: any) => unknown} levels @returns {ExternalModel[]}
 */
export function piModels(models, levels) {
  /** @type {ExternalModel[]} */
  const out = [];
  for (const m of Array.isArray(models) ? models : []) {
    const model = obj(m);
    const ref = `${String(model.provider ?? "")}/${String(model.id ?? "")}`;
    if (!isRef(ref)) continue;
    let ls = [];
    try {
      const l = levels(m);
      ls = Array.isArray(l) ? l.filter((x) => typeof x === "string" && LEVEL_RE.test(x)) : [];
    } catch { /* no levels */ }
    out.push({ ref, host: hostOf(model.baseUrl) || String(model.provider), cost: costOf(model.cost), levels: ls });
  }
  return out;
}

/** An external model's note in Coder model's values: "openrouter.ai, paid"; the host alone when the cost is not known.
 * @param {ExternalModel} x */
function modelNote(x) {
  return x.cost ? `${x.host}, ${x.cost}` : x.host;
}

/** Coder thinking's values for a model of this kind, in the dashboard's order (its Agents panel), after "default"
 * (dashboard default: no value of this computer). @param {string} kind */
export function thinkingValues(kind) {
  return ["default", "main", ...(kind === "effort" ? ["off", "low", "medium", "xhigh"] : ["off", "on"])];
}

/** A thinking value in words ("main": same as main; "default": dashboard default). @param {string} v */
export function thinkingWord(v) {
  return v === "main" ? "same as main" : v === "default" ? "dashboard default" : v;
}

/** An external model's thinking value in words ("default": model default, the model's own). @param {string} v */
export function modelThinkingWord(v) {
  return v === "default" ? "model default" : v;
}

const THINKING = new Set(["main", "on", "off", "low", "medium", "xhigh"]);
const MODEL_ID = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
const FULL_SPEC = "Use it only with a full spec (spec-kit or a similar tool).";

/**
 * @typedef {{ id: string, kind: string, main: string, coder: string, dashboard: string, override: boolean }} StateModel
 *   main, coder: the values written (the override merged); dashboard: the dashboard's Coder thinking ("" when not
 *   known: a state file from before it was written); override: this computer has a value of its own (CODER_THINKING)
 */

/**
 * /carl's Coder thinking of this computer (CODER_THINKING=MODEL:VALUE,... in client-install.env) as {model: value}.
 * @param {string | undefined} text @returns {Record<string, string>}
 */
export function thinkingOverrides(text) {
  /** @type {Record<string, string>} */
  const out = {};
  for (const part of String(text ?? "").split(",")) {
    const [id, value] = part.trim().split(":");
    if (id && MODEL_ID.test(id) && THINKING.has(value ?? "")) out[id] = String(value);
  }
  return out;
}

/**
 * The models CARL wrote into this client's config, from its state file (carl.json "models": each model's thinking
 * kind, the main session's thinking and the coder's, /carl's choice of this computer merged, and the dashboard's
 * coder value): [] when none. over: this computer's overrides (thinkingOverrides).
 * @param {unknown} models @param {Record<string, string>} [over]
 * @returns {StateModel[]}
 */
export function stateModels(models, over = {}) {
  return Object.entries(obj(models)).filter(([id]) => MODEL_ID.test(id)).map(([id, m]) => {
    const o = obj(m);
    const val = (/** @type {unknown} */ v, /** @type {string} */ d) => (typeof v === "string" && THINKING.has(v) ? v : d);
    const coder = val(o.coder, "main");
    const override = Object.hasOwn(over, id);
    return { id, kind: o.thinking === "effort" ? "effort" : "on-off", main: val(o.main, "on"), coder,
             dashboard: val(o.dashboard, override ? "" : coder), override };
  });
}

/**
 * The model the coder runs on, of the models in the state file: Coder model "same as main" (the only value now) is
 * the session's model; without a session, or with a model that is not CARL's, the config's default model, else the
 * first. from: "session" or "default". undefined when the state file has no models.
 * @param {StateModel[]} models @param {string[]} providers CARL's provider ids in this client
 * @param {Session} session @param {string} fallback the config's default model ("provider/model")
 * @returns {{ m: StateModel, from: "session" | "default" } | undefined}
 */
export function coderModel(models, providers, session, fallback) {
  const find = (/** @type {string | undefined} */ ref) => {
    const at = String(ref ?? "").indexOf("/");
    if (at < 1) return undefined;
    const prov = String(ref).slice(0, at);
    const id = String(ref).slice(at + 1);
    return providers.includes(prov) ? models.find((m) => m.id === id) : undefined;
  };
  const m = find(session.model);
  if (m) return { m, from: "session" };
  const d = find(fallback) ?? models[0];
  return d ? { m: d, from: "default" } : undefined;
}

/** CARL's provider ids in a client's state file (carl.json "providers"; ours is "llamacpp", or "carl" next to a user's
 * own "llamacpp"). @param {JsonObject} st @returns {string[]} */
function providerIds(st) {
  const ids = Object.values(obj(st.providers)).filter((v) => typeof v === "string");
  return ids.length ? ids.map(String) : ["llamacpp"];
}

/** Does the coder think as nothing (off) with this value? @param {StateModel} m @param {string} v */
function thinksOff(m, v) {
  const main = m.main === "off";
  if (v === "off" || (v === "main" && main)) return true;
  return v === "default" && (m.dashboard === "off" || (m.dashboard === "main" && main));
}

/**
 * Coder thinking for the model the coder runs on: its values, the current one ("dashboard default" when this computer
 * has no value of its own), and an action for each other value (CODER_THINKING=MODEL:VALUE; "default" removes the
 * model's entry). Grey notes: what "dashboard default" gives, and the main session's thinking with that model (from
 * the config's default model when /carl has no session). Off (or a value that gives off) says to use a full spec.
 * @param {StateModel} m @param {"session" | "default"} from @returns {Row}
 */
function thinkingRow(m, from) {
  const values = thinkingValues(m.kind);
  const cur = m.override && values.includes(m.coder) ? m.coder : "default";
  const name = `the coder's thinking with ${m.id}`;
  const main = thinkingWord(m.main);
  /** @type {Record<string, Action>} */
  const actions = {};
  for (const v of values.filter((x) => x !== cur)) {
    const words = v === "default" && m.dashboard ? `${thinkingWord(v)} (${thinkingWord(m.dashboard)})` : thinkingWord(v);
    actions[v] = { id: `set:CODER_THINKING=${m.id}:${v}`, args: ["set", `CODER_THINKING=${m.id}:${v}`],
                   busy: v === "off" ? "turning off…" : `switching to ${thinkingWord(v)}…`, row: "thinking", want: v, name,
                   did: `CARL set ${name} to ${words} on this computer.${thinksOff(m, v) ? ` ${FULL_SPEC}` : ""}`,
                   live: ["pi"] };
  }
  /** @type {Record<string, string>} */
  const notes = { main: `${from === "session" ? "the main session's" : "the default model's"} thinking (${main})` };
  if (m.dashboard) notes.default = dashText(m);
  return { id: "thinking", label: "Coder Thinking", state: thinkingWord(cur), value: cur, kind: "choice", values,
           titles: { main: thinkingWord("main"), default: thinkingWord("default") }, notes, actions,
           choiceTitle: `Coder Thinking with ${m.id}` };
}

/** What "dashboard default" gives for this model, in words: "medium", "same as main (low)". @param {StateModel} m */
function dashText(m) {
  return m.dashboard === "main" ? `${thinkingWord("main")} (${thinkingWord(m.main)})` : thinkingWord(m.dashboard);
}

/**
 * The Coder thinking row: the model the coder runs on (coderModel). None when the state file has no models (an
 * install from before 23.4.4: the next setup or sync writes them).
 * @param {StateModel[]} models @param {string[]} providers @param {Session} session @param {string} fallback
 * @returns {Row[]}
 */
function coderThinking(models, providers, session, fallback) {
  const c = coderModel(models, providers, session, fallback);
  return c ? [thinkingRow(c.m, c.from)] : [];
}

/**
 * Coder thinking for an external coder model (Phase 23.4.5): "model default" (no value of CARL's: the client's own
 * for that model) and the levels the client knows for it; CODER_MODEL_THINKING=VALUE ("default" removes it). The
 * dashboard's per-model table does not apply. Off says to use a full spec, as for CARL's models.
 * @param {string} ref @param {string[]} levels @param {string} cur this computer's value ("default": none)
 * @returns {Row}
 */
function externalThinkingRow(ref, levels, cur) {
  const values = ["default", ...levels.filter((l) => l !== "default")];
  if (cur !== "default" && !values.includes(cur)) values.push(cur);    // a value the client does not list (now)
  const name = `the coder's thinking with ${ref}`;
  /** @type {Record<string, Action>} */
  const actions = {};
  for (const v of values.filter((x) => x !== cur)) {
    actions[v] = { id: `set:CODER_MODEL_THINKING=${v}`, args: ["set", `CODER_MODEL_THINKING=${v}`],
                   busy: v === "off" ? "turning off…" : `switching to ${modelThinkingWord(v)}…`, row: "thinking", want: v,
                   name, did: `CARL set ${name} to ${modelThinkingWord(v)} on this computer.${v === "off" ? ` ${FULL_SPEC}` : ""}`,
                   live: ["pi"] };
  }
  return { id: "thinking", label: "Coder Thinking", state: modelThinkingWord(cur), value: cur, kind: "choice", values,
           titles: { default: modelThinkingWord("default") }, notes: {}, actions, choiceTitle: `Coder Thinking with ${ref}` };
}

/**
 * The Coder model row (Phase 23.4.5): "same as main" (the coder runs on the main session's model; note "CARL: <the
 * model>"), then the external models this computer's client can use, each with a note "<host>, free" or "<host>,
 * paid". CODER_MODEL=PROVIDER/MODEL, or main (with CODER=1: the coder stays on, as /carl's Coder switch does). The
 * toast says where the coder's work goes and that a paid model costs money. A current model that the client does not
 * list (chosen in the other client, or a provider that went away) stays in the list with a note.
 * @param {string} cur "main" or "provider/model" @param {string} carl CARL's model the coder runs on with "main"
 * @param {ExternalModel[]} external @param {Client} client
 * @returns {Row}
 */
function coderModelRow(cur, carl, external, client) {
  const values = ["main", ...external.map((x) => x.ref)];
  /** @type {Record<string, string>} */
  const notes = carl ? { main: `CARL: ${carl}` } : {};
  for (const x of external) notes[x.ref] = modelNote(x);
  if (cur !== "main" && !values.includes(cur)) {
    values.splice(1, 0, cur);
    notes[cur] = `${appName(client)} does not list this model.`;
  }
  const name = "the coder's model";
  /** @type {Record<string, Action>} */
  const actions = {};
  for (const v of values.filter((x) => x !== cur)) {
    const x = external.find((e) => e.ref === v);
    const where = x ? ` The coder's work goes to ${x.host}${x.cost === "paid" ? ", and the model costs money" : ""}.` : "";
    actions[v] = { id: `set:CODER_MODEL=${v}`, args: ["set", `CODER_MODEL=${v}`, ...(v === "main" ? ["CODER=1"] : [])],
                   busy: `switching to ${v === "main" ? thinkingWord("main") : v}…`, row: "coder-model", want: v, name,
                   did: `CARL set ${name} to ${v === "main" ? thinkingWord("main") : v} on this computer.${where}`,
                   live: ["pi"] };
  }
  return { id: "coder-model", label: "Coder Model", state: cur === "main" ? thinkingWord("main") : cur, value: cur,
           kind: "choice", values, titles: { main: thinkingWord("main") }, notes, actions };
}

/** The Request Check row's values and their words (the 23.4.3 addendum; default reminder). */
const REQUEST = ["on", "reminder", "off"];
/** @type {Record<string, string>} */
const REQUEST_WORDS = { on: "on", reminder: "single reminder", off: "off" };

/**
 * The Request Check row (the 23.4.3 addendum): whether the coder's brief must carry the literals of the user's request.
 * CODER_REQUEST_CHECK=VALUE; both clients read it at each coder task (live: no restart).
 * @param {unknown} v the state file's request_check ("reminder" when missing) @returns {Row}
 */
function requestRow(v) {
  const cur = REQUEST.includes(/** @type {string} */ (v)) ? String(v) : "reminder";
  const name = "the request check";
  /** @type {Record<string, Action>} */
  const actions = {};
  for (const t of REQUEST.filter((x) => x !== cur)) {
    actions[t] = { id: `set:CODER_REQUEST_CHECK=${t}`, args: ["set", `CODER_REQUEST_CHECK=${t}`],
                   busy: t === "off" ? "turning off…" : `switching to ${REQUEST_WORDS[t]}…`, row: "request", want: t, name,
                   did: `CARL set ${name} to ${REQUEST_WORDS[t]} on this computer.`, live: ["opencode", "pi"] };
  }
  return { id: "request", label: "Request Check", state: REQUEST_WORDS[cur], value: cur, kind: "choice", values: REQUEST,
           titles: REQUEST_WORDS, notes: {}, actions };
}

/** The Tests row's values and their words (Phase 23.4.6). */
const TESTS = ["before", "after", "off"];
/** @type {Record<string, string>} */
const TESTS_WORDS = { before: "before code", after: "after code", off: "off" };

/**
 * The Tests row (Phase 23.4.6): when CARL's test session runs for new code. CODER_TESTS=VALUE; both clients read it
 * at each coder task (live: no restart). @param {unknown} v the state file's coder_tests ("before" when missing)
 * @returns {Row}
 */
function testsRow(v) {
  const cur = TESTS.includes(/** @type {string} */ (v)) ? String(v) : "before";
  const name = "the coder's tests";
  /** @type {Record<string, Action>} */
  const actions = {};
  for (const t of TESTS.filter((x) => x !== cur)) {
    actions[t] = { id: `set:CODER_TESTS=${t}`, args: ["set", `CODER_TESTS=${t}`], busy: `switching to ${TESTS_WORDS[t]}…`,
                   row: "tests", want: t, name, did: `CARL set ${name} to ${TESTS_WORDS[t]} on this computer.`,
                   live: ["opencode", "pi"] };
  }
  return { id: "tests", label: "Tests", state: TESTS_WORDS[cur], value: cur, kind: "choice", values: TESTS,
           titles: TESTS_WORDS, notes: {}, actions };
}

/**
 * The parts' rows, in the order of their groups: coder, tools, side panels, server.
 * @param {Client} client @param {Session} session @returns {Row[]}
 */
function parts(client, session) {
  const env = switches();
  const over = thinkingOverrides(env.CODER_THINKING);
  const envOn = (/** @type {string} */ key) => env[key] !== "1";
  const oc = text(join(CARL, "opencode.env"));
  let coderOn, bg, reminder, browser, search, models, providers, fallback, tests, request;
  let coderRef = "main";                     // Coder model: "main" or the external model the setup wrote
  let refThinking = "default";               // its thinking on this computer ("default": model default)
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
    const st = obj(json(join(OC, "carl.json")));
    tests = st.coder_tests;
    request = st.request_check;
    models = stateModels(st.models, over);
    providers = providerIds(st);
    fallback = typeof cfg.model === "string" ? cfg.model : "";
    // the coder agent's model (configure.py writes it for an external coder) and its variant (carl-delegation)
    const ref = obj(agents[typeof st.coder_agent === "string" ? st.coder_agent : "coder"]).model;
    if (isRef(ref)) coderRef = String(ref);
    const variant = deleg?.coderVariant;
    if (coderRef !== "main" && typeof variant === "string" && LEVEL_RE.test(variant)) refThinking = variant;
    lsp = [switchRow("lsp", "LSP", "LSP", "NO_LSP", cfg.lsp === true && /OPENCODE_EXPERIMENTAL_LSP_TOOL=1/.test(oc))];
    panels = [switchRow("sidebar", "Subagents Side Panel", "the subagents side panel", "NO_SIDEBAR", tui.includes("subagents-sidebar")),
              switchRow("switcher", "Session Switcher", "the session switcher", "NO_SWITCHER", tui.includes("session-switcher"))];
    server = [switchRow("cache", "Disk Cache", "the disk cache", "NO_CACHE", plugins.includes("carl-cache")),
              switchRow("check", "Model Check", "the model check", "NO_MODEL_CHECK", plugins.includes("carl-model-check"))];
  } else {
    const mcp = obj(json(join(PI, "mcp.json")));
    const servers = obj(mcp.mcpServers ?? mcp.servers);
    const st = obj(json(join(PI, "carl.json")));
    tests = st.coder_tests;
    request = st.request_check;
    coderOn = exists(join(PI, "agents", "coder.md")) || exists(join(PI, "agents", "carl-coder.md"));
    bg = coderOn ? st.background_subagents !== false : envOn("NO_BACKGROUND_SUBAGENTS");
    reminder = coderOn ? obj(st.delegation).reminder !== false : envOn("NO_REMINDER");
    models = stateModels(st.models, over);
    providers = providerIds(st);
    if (isRef(st.coder_model)) coderRef = String(st.coder_model);    // the subagent extension passes it as --model
    const level = obj(obj(st.thinking).coder)[coderRef];
    if (coderRef !== "main" && typeof level === "string" && LEVEL_RE.test(level)) refThinking = level;
    const sett = obj(json(join(PI, "settings.json")));
    fallback = typeof sett.defaultProvider === "string" && typeof sett.defaultModel === "string"
      ? `${sett.defaultProvider}/${sett.defaultModel}` : "";
    browser = Boolean(servers["carl-browser"]);
    search = piSearch(servers["carl-web-search"]);
    server = [switchRow("cache", "Disk Cache", "the disk cache", "NO_CACHE", exists(join(PI, "extensions", "carl-cache", "index.ts")))];
  }
  // the coder's rows: a sub-list (user, 2026-10-09); without the coder only its switch (user, 2026-10-08: the
  // others do nothing then, and they disappear)
  // the external models of this client (Phase 23.4.5): CARL's own providers are not offered (Phase 29)
  const external = (session.external ?? []).filter((x) => !providers.includes(x.ref.slice(0, x.ref.indexOf("/"))));
  const carl = coderModel(models, providers, session, fallback);
  const thinking = coderRef === "main" ? coderThinking(models, providers, session, fallback)
    : [externalThinkingRow(coderRef, external.find((x) => x.ref === coderRef)?.levels ?? [], refThinking)];
  const coder = [
    switchRow("coder", "Coder", "the coder subagent", "NO_CODER", coderOn),
    ...(coderOn ? [switchRow("background", "Background Coder", "the background coder", "NO_BACKGROUND_SUBAGENTS", bg),
                   switchRow("reminder", "Delegation Reminder", "the delegation reminder", "NO_REMINDER", reminder),
                   testsRow(tests), requestRow(request), ...thinking, coderModelRow(coderRef, carl?.m.id ?? "", external, client)] : []),
  ];
  return [
    { id: "subagent", label: "Coder Subagent", state: coderOn ? "on" : "off", kind: "list", listTitle: "Coder Subagent",
      arrow: true, rows: coder },
    switchRow("browser", "Browser", "the browser", "NO_BROWSER", browser),
    webRow(search),
    ...lsp,
    ...panels,
    ...server,
  ];
}

/** Every row of a panel, the rows of its lists too (depth first). @param {Row[]} rows @returns {Row[]} */
export function allRows(rows) {
  return rows.flatMap((r) => [r, ...allRows(r.rows ?? [])]);
}

/**
 * The rows of the list at `path` (the ids of the list rows opened from the top; [] for the top), or undefined when
 * that list is not there now (the coder went off).
 * @param {Row[]} rows @param {string[]} path @returns {Row[] | undefined}
 */
export function rowsAt(rows, path) {
  let now = rows;
  for (const id of path) {
    const r = now.find((x) => x.id === id);
    if (!r || r.kind !== "list") return undefined;
    now = r.rows ?? [];
  }
  return now;
}

/** A row's value: what a choice has (its `value`), else its state. @param {Row} r */
function valueOf(r) {
  return r.value ?? r.state;
}

/**
 * Does Enter on this row open something (a sub-list, its values, a view)? The Coder model row opens nothing while
 * it has one value.
 * @param {Row} r
 */
export function opens(r) {
  return Boolean(r.arrow) || r.kind === "view" || (r.kind === "choice" && (r.values?.length ?? 0) > 1);
}

/**
 * A row's state as the lists show it: a row that opens something gets " ›" (user, 2026-10-09: "the Coder subagent
 * needs a little right arrow to indicate it expands"; "add the arrow to other rows that open something"); busy: the
 * state while an action runs.
 * @param {Row} r @param {string} [busy]
 */
export function shownState(r, busy) {
  return busy ?? (opens(r) ? `${r.state} ›` : r.state);
}

/**
 * Every row for this client (the parts, then the config sync) and the title.
 * @param {Client} client @param {Session} [session] the session /carl is opened in @returns {Panel}
 */
export function panel(client, session = {}) {
  const rows = [...parts(client, session), ...syncRows()];
  const changed = allRows(rows).filter((r) => r.kind !== "list" && BEFORE.has(r.id) && BEFORE.get(r.id) !== valueOf(r))
    .length + (appliedSinceStart() ? 1 : 0);
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
 * action also reads the row again, and `result` (what `carl-sync.py set` printed) says who must restart and the
 * server's slots. warn: a warning, not a plain success (the coder turned on with 1 slot).
 * @param {Action} action
 * @param {number} code
 * @param {Client} client
 * @param {JsonObject} [result]
 * @param {Session} [session] the session /carl is opened in (the Coder thinking row's model)
 * @returns {Said}
 */
export function outcome(action, code, client, result = {}, session = {}) {
  if (code === -1) return { ok: false, message: "CARL cannot find carl-sync.py. Run the setup again." };
  if (action.args[0] === "set") return switchOutcome(action, code, client, result, session);
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

/**
 * After `carl-sync.py set`. The coder turned on while the server runs 1 slot (the slot count the setup reads for
 * its coder rule; not known: no warning) is a warning (user, 2026-10-09: it turns the coder on, and says that this
 * system has only one slot); also Coder model set back to same as main with 1 slot. Not with an external coder model
 * (Phase 23.4.5: it uses no slot of the server).
 * @param {Action} action @param {number} code @param {Client} client @param {JsonObject} result
 * @param {Session} session @returns {Said}
 */
function switchOutcome(action, code, client, result, session) {
  if (code !== 0) {
    const why = typeof result.error === "string" && result.error ? result.error
      : "Its output is in ~/.config/carl/client-sync.log.";
    return { ok: false, message: `CARL could not change the setting. ${why}` };
  }
  const name = action.name ?? "the part";
  const row = allRows(panel(client, session).rows).find((r) => r.id === action.row);
  const now = row ? valueOf(row) : "";
  if (action.want && now !== action.want) {
    const shown = row?.state || "the same";
    return { ok: false, message: `CARL changed the setting, but ${name} is still ${shown}. The setup's `
      + "output is in ~/.config/carl/client-sync.log." };
  }
  const did = action.did ?? (action.want === "on" || action.want === "off" ? `CARL turned ${name} ${action.want}.`
    : `CARL set ${name} to ${action.want}.`);
  const apps = /** @type {Client[]} */ ((Array.isArray(result.restart) ? result.restart : [client])
    .filter((c) => c === "opencode" || c === "pi"));
  // the coder on CARL's server (Coder model same as main): an external coder uses no slot (Phase 23.4.5)
  const local = allRows(panel(client, session).rows).find((r) => r.id === "coder-model")?.value === "main";
  const oneSlot = ((action.row === "coder" && action.want === "on") || (action.row === "coder-model" && action.want === "main"))
    && local && result.slots === 1;
  const said = oneSlot ? `${did} This server runs 1 slot: the coder takes the main session's slot while it works.` : did;
  // no client to restart (the change reaches them at once): no restart sentence
  const message = Array.isArray(result.restart) && !apps.length ? said
    : restartNotice(client, said, apps, result.new_terminal === true);
  return oneSlot ? { ok: true, warn: true, message } : { ok: true, message };
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
 * @param {Session} [session] the session /carl is opened in
 * @returns {Promise<Said>}
 */
export async function act(action, client, session = {}) {
  if (action.row && !BEFORE.has(action.row) && !action.live?.includes(client)) {
    const cur = allRows(panel(client, session).rows).find((r) => r.id === action.row);
    if (cur) BEFORE.set(action.row, valueOf(cur));
  }
  const r = await exec(action.args);
  return outcome(action, r.code, client, parsed(r.out), session);
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
