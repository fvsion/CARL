// @ts-check
// CARL's prompt cache for OpenCode and Pi (client/shared/carl-cache.js: the OpenCode plugin carl-cache and
// the Pi extension carl-cache each carry a copy, installed by client/configure.py).
//
// Each request to CARL's llama.cpp server goes through before() and each finished turn through after():
//   - before() moves the parts of the system prompt that change between projects and days (OpenCode's
//     <env> block and project instructions, Pi's project context and folder) to the first user message, so the system
//     text and the tools are the same everywhere; picks the slot (the session's own, else an idle one);
//     fills it with the best saved state (the session's file, else the agent's prompt file, else it reads
//     the agent's prompt once and saves it); and pins the request there (id_slot).
//   - after() saves the session's slot to its file (main sessions only) when a reply ends the turn (not a
//     tool call), first setting it back to the end of the last prompt when the model's template doesn't
//     re-render a reply as it was generated. The clients call it before they see the reply end, so the
//     save is done before a one-shot run (opencode run, pi -p) exits.
// Router mode: before() loads the model asked for first, so its saved state can go in before the request.
//
// It only uses llama-server's API (it works from a VM too). On this Mac it also reads the Caching settings
// (~/.config/carl/config.json "cache") and keeps ~/.config/carl/slots within the disk limit. Every failure
// leaves the request as it was: the cache is never in the way. CARL_CACHE_LOG=FILE writes what it did and
// each failure it let go to that file (never a key or a prompt).

import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import * as fs from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

export const MIN_PREFIX_TOKENS = 1024;   // a shorter prompt is quick to read: no file
export const MIN_SESSION_TOKENS = 4096;  // a shorter conversation is quick to read again
export const MIN_FREE_BYTES = 10 * 2 ** 30;  // no saves on this Mac below this much free disk
export const DEFAULT_GB = 10;                // the disk limit (a 74K-token session on the 35B is ~1 GB)
// When a session is saved: auto = when the part not saved yet would take autoS seconds to read again,
// and before it leaves the server; turn = after every turn; switch = before it leaves the server (its
// slot is needed, a router switch, a server stop); stop = before a server stop or router switch only.
export const SAVES = ["auto", "turn", "switch", "stop"];
export const AUTO_S = 120;                   // the default: a crash costs at most ~2 min of reading; stops save anyway
const READ_SPEED = 500;                       // tokens/s until this model's speed is measured
const CLAIM_STALE_MS = 120_000;
export const TURN_STALE_MS = 600_000;  // a turn mark this old is a client that went away (the dashboard ignores it)
export const NO_PIN_AGENTS = new Set(["title", "summary"]);            // small one-off prompts
export const NO_SAVE_AGENTS = new Set(["title", "summary", "compaction"]);
const LOAD_WAIT_MS = 180_000;        // a switch takes 30 s to 2 min
const LOAD_RETRY_MS = 5_000;
const SETTINGS_MS = 5_000;
const OFF = new Set(["none", "minimal", "off", "disable", "disabled"]);
const STATE_MODE = 0o600;            // the claim, turn and record files and the log: only this user
// a saved state's name as carl-cache.js and the dashboard make them (safeName parts): never a path
const STATE_NAME = /^carl-(?:prefix|session)\+[A-Za-z0-9._+-]+\.bin$/;

/** @typedef {{ [key: string]: unknown }} JsonObject */
/**
 * A chat request as the clients send it (the OpenAI shape); the fields the cache does not read pass through.
 * @typedef {{ model?: unknown, messages?: unknown, [key: string]: unknown }} Payload
 */
/**
 * One slot of the server (GET /slots): its id, busy or not, the tokens it holds, its last task id.
 * @typedef {{ id: number, busy: boolean, n: number, task: number }} Slot
 */
/**
 * The Caching settings (Settings > Caching on the server).
 * @typedef {{ enabled: boolean, prefix: boolean, sessions: boolean, diskGb: number, save: string, autoS: number }} Settings
 */

// ------------------------------------------------------------------ the debug log

/**
 * One line in the CARL_CACHE_LOG file, when it is set (created for this user only). For the plugins too:
 * a failure that they let go, so that the request goes on, is written here.
 * @param {string} msg
 */
export function debugLog(msg) {
  const file = process.env.CARL_CACHE_LOG;
  if (!file) return;
  try {
    fs.appendFileSync(file, `${new Date().toISOString()} ${msg}\n`, { mode: STATE_MODE });
  } catch { /* no log */ }
}

/** The message of a thrown value. @param {unknown} e @returns {string} */
export function errorText(e) {
  return e instanceof Error ? e.message : String(e);
}

// ------------------------------------------------------------------ names and small helpers (pure)

/** @param {unknown} s @param {number} n */
export function safeName(s, n) {
  return String(s).replace(/[^A-Za-z0-9._-]/g, "_").slice(0, n);
}

/** @param {string} s @param {number} [n] */
export function shortHash(s, n = 12) {
  return createHash("sha256").update(s).digest("hex").slice(0, n);
}

/** The saved conversation of a session. @param {string} model @param {string} key @param {string} session */
export function sessionFile(model, key, session) {
  return `carl-session+${safeName(model, 60)}+${key}+${safeName(session, 80)}.bin`;
}

/** The saved prompt of an agent. @param {string} model @param {string} agent @param {string} hash */
export function prefixFile(model, agent, hash) {
  return `carl-prefix+${safeName(model, 60)}+${safeName(agent, 30)}+${hash}.bin`;
}

/** A JSON file, or undefined. @param {string} path @returns {unknown} */
function json(path) {
  try {
    return parse(fs.readFileSync(path, "utf8"));
  } catch {
    return undefined;
  }
}

/** JSON text as a value to check before use. @param {string} text @returns {unknown} */
function parse(text) {
  return JSON.parse(text);
}

/** The value if it is a JSON object, else an empty one. @param {unknown} x @returns {JsonObject} */
export function obj(x) {
  return x !== null && typeof x === "object" && !Array.isArray(x) ? /** @type {JsonObject} */ (x) : {};
}

/** The value if it is an array, else an empty one. @param {unknown} x @returns {unknown[]} */
function arr(x) {
  return Array.isArray(x) ? x : [];
}

/** An integer, or undefined. @param {unknown} x @returns {number | undefined} */
function int(x) {
  return typeof x === "number" && Number.isInteger(x) ? x : undefined;
}

/** The error code of a failed file call ("EEXIST", ...). @param {unknown} e @returns {unknown} */
function errCode(e) {
  return e !== null && typeof e === "object" && "code" in e ? e.code : undefined;
}

/** Is it the name of a saved state (no folder part)? @param {unknown} name @returns {name is string} */
export function isStateName(name) {
  return typeof name === "string" && STATE_NAME.test(name);
}

/** @param {readonly number[]} a @param {readonly number[]} b */
export function commonPrefix(a, b) {
  let n = 0;
  while (n < a.length && n < b.length && a[n] === b[n]) n++;
  return n;
}

/**
 * The idle slots in the order to use them: empty ones first, then the one holding least.
 * @template {{ id: number, busy: boolean, n: number }} T
 * @param {T[]} slots
 * @returns {T[]}
 */
export function rankSlots(slots) {
  return slots.filter((s) => !s.busy).sort((a, b) => a.n - b.n || a.id - b.id);
}

/** Does the request turn thinking off? @param {Payload} p */
export function thinkingOff(p) {
  const kw = obj(p.chat_template_kwargs);
  return kw.enable_thinking === false || (typeof p.reasoning_effort === "string" && OFF.has(p.reasoning_effort)) ||
    (typeof kw.reasoning_effort === "string" && OFF.has(kw.reasoning_effort));
}

/**
 * A one-off request (a title, a summary) without thinking: thinking there only holds a slot longer
 * (measured: a title on the 35B, 35 s with thinking on).
 * @param {Payload} p
 * @returns {Payload}
 */
export function quick(p) {
  return { ...p, reasoning_effort: "none", chat_template_kwargs: { ...obj(p.chat_template_kwargs), enable_thinking: false } };
}

/** The request fields the chat template reads. @param {Payload} p @returns {JsonObject} */
export function templateFields(p) {
  /** @type {JsonObject} */
  const out = {};
  for (const k of ["chat_template_kwargs", "reasoning_effort", "tools", "tool_choice", "parallel_tool_calls"]) {
    if (p[k] !== undefined) out[k] = p[k];
  }
  return out;
}

// ------------------------------------------------------------------ moving the volatile prompt parts

const OC_ENV = "Here is some useful information about the environment you are running in:";
const OC_SKILLS = "\n\nSkills provide specialized instructions";
const OC_INSTR = /^Instructions from: (.+)$/gm;

/**
 * OpenCode's system text as [kept, moved]: moved = the <env> block and the instructions of files in
 * the working folder (AGENTS.md and the like); kept = the rest, as it was.
 * @param {string} text
 * @returns {[string, string]}
 */
export function splitOpenCode(text) {
  const i = text.indexOf(OC_ENV);
  const e = i < 0 ? -1 : text.indexOf("</env>", i);
  if (e < 0) return [text, ""];
  let j = e + "</env>".length;
  if (text[j] === "\n") j++;
  const env = text.slice(i, j);
  const roots = [...env.matchAll(/^\s*(?:Working directory|Workspace root folder): (.+)$/gm)]
    .map((m) => m[1].trim()).filter((r) => r && r !== "/");
  const rest = text.slice(j);
  const s = rest.indexOf(OC_SKILLS);
  const body = s < 0 ? rest : rest.slice(0, s);
  const tail = s < 0 ? "" : rest.slice(s);
  const starts = [...body.matchAll(OC_INSTR)].map((m) => m.index ?? 0);
  const kept = [text.slice(0, i), body.slice(0, starts[0] ?? body.length)];
  const moved = [env.trimEnd()];
  starts.forEach((at, k) => {
    const block = body.slice(at, starts[k + 1] ?? body.length);
    const path = block.slice("Instructions from: ".length, block.indexOf("\n") < 0 ? undefined : block.indexOf("\n")).trim();
    const own = roots.some((r) => path === r || path.startsWith(r.endsWith("/") ? r : r + "/"));
    (own ? moved : kept).push(own ? block.trimEnd() : block);
  });
  kept.push(tail);
  return [kept.join(""), moved.join("\n\n")];
}

const PI_BLOCKS = [/<project_context>[\s\S]*?<\/project_context>\n*/, /<cwd>[\s\S]*?<\/cwd>\n*/];

/**
 * Pi's system text as [kept, moved]: moved = the <project_context> block (AGENTS.md files) and the
 * <cwd> block.
 * @param {string} text
 * @returns {[string, string]}
 */
export function splitPi(text) {
  /** @type {string[]} */
  const moved = [];
  let kept = text;
  for (const re of PI_BLOCKS) {
    const m = kept.match(re);
    if (m) {
      moved.push(m[0].trim());
      kept = kept.replace(m[0], "");
    }
  }
  return [kept, moved.join("\n\n")];
}

/**
 * The request with the system text's volatile parts at the start of the first user message.
 * @param {Payload} payload
 * @param {(text: string) => [string, string]} split
 * @returns {Payload}
 */
export function relocate(payload, split) {
  const msgs = arr(payload.messages);
  const sys = obj(msgs[0]);
  if (sys.role !== "system") return payload;
  const text = typeof sys.content === "string" ? sys.content
    : Array.isArray(sys.content) ? sys.content.map((p) => { const t = obj(p).text; return typeof t === "string" ? t : ""; }).join("")
    : null;
  const u = msgs.findIndex((m) => obj(m).role === "user");
  if (text === null || u < 0) return payload;
  const [kept, moved] = split(text);
  if (!moved) return payload;
  const user = obj(msgs[u]);
  const note = `${moved}\n\n`;
  const content = typeof user.content === "string" ? note + user.content
    : Array.isArray(user.content) ? [{ type: "text", text: note }, ...user.content] : user.content;
  const out = msgs.slice();
  out[0] = { ...sys, content: kept };
  out[u] = { ...user, content };
  return { ...payload, messages: out };
}

// ------------------------------------------------------------------ the disk limit (this Mac only)

/**
 * The files to remove so the rest fit `limit` bytes: the oldest conversations first, then the oldest
 * prompts; `keep` last (only when it alone is over the limit). The same rule as the dashboard's.
 * @param {{ name: string, bytes: number, mtime: number, base?: string }[]} files
 * @param {number} limit
 * @param {string} [keep]
 */
export function overBudget(files, limit, keep = "") {
  let total = files.reduce((a, f) => a + f.bytes, 0);
  const rank = (/** @type {{ name: string, mtime: number }} */ f) => [f.name.startsWith("carl-prefix+") ? 1 : 0, f.mtime];
  const order = files.filter((f) => f.name !== keep).sort((a, b) => {
    const [x, y] = [rank(a), rank(b)];
    return x[0] - y[0] || x[1] - y[1];
  });
  /** @type {string[]} */
  const out = [];
  for (const f of order) {
    if (total <= limit) break;
    out.push(f.name);
    total -= f.bytes;
  }
  if (total > limit && files.some((f) => f.name === keep)) out.push(keep);
  // a conversation stored as a patch against a prompt that goes, goes with it
  const gone = new Set(out);
  for (const f of files) if (f.base && gone.has(f.base) && !gone.has(f.name)) out.push(f.name);
  return out;
}

/**
 * Create a claim file unless it exists (a stale one, older than CLAIM_STALE_MS, is taken over);
 * true when this process has it. A folder it can't write to: true (no claim possible).
 * @param {string} path
 * @returns {boolean}
 */
export function takeFile(path) {
  for (let i = 0; i < 2; i++) {
    try {
      fs.writeFileSync(path, String(process.pid), { flag: "wx", mode: STATE_MODE });
      return true;
    } catch (e) {
      if (errCode(e) !== "EEXIST") {
        debugLog(`claim ${path}: ${errorText(e)} (the request goes on without a claim)`);
        return true;
      }
      try {
        if (Date.now() - fs.statSync(path).mtimeMs <= CLAIM_STALE_MS) return false;
        fs.unlinkSync(path);
      } catch { /* gone meanwhile: try again */ }
    }
  }
  return false;
}

/**
 * The saved states in the folder, each in all its forms (the file; for a conversation stored as a patch
 * against its prompt, the patch .zst and its .json: tools/monitor/slotpack.py), as { name, bytes, mtime, base }.
 * @param {string} dir
 * @returns {{ name: string, bytes: number, mtime: number, base: string }[]}
 */
function listing(dir) {
  /** @type {Map<string, { name: string, bytes: number, mtime: number, base: string }>} */
  const found = new Map();
  try {
    for (const n of fs.readdirSync(dir)) {
      const m = n.match(/^(carl-(?:prefix|session)\+.*\.bin)(\.zst|\.json)?$/);
      if (!m) continue;
      const st = fs.statSync(join(dir, n));
      const f = found.get(m[1]) ?? { name: m[1], bytes: 0, mtime: 0, base: "" };
      f.bytes += st.size;
      f.mtime = Math.max(f.mtime, st.mtimeMs);
      if (m[2] === ".json") {
        try {
          f.base = String(obj(parse(fs.readFileSync(join(dir, n), "utf8"))).base ?? "");
        } catch { /* no base */ }
      }
      found.set(m[1], f);
    }
  } catch { /* no folder */ }
  return [...found.values()];
}

/** A saved state in every form. @param {string} dir @param {string} name */
function removeState(dir, name) {
  for (const p of [name, `${name}.zst`, `${name}.json`]) {
    try {
      fs.unlinkSync(join(dir, p));
    } catch { /* not there */ }
  }
}

/**
 * Write a small state file (a turn mark, a record) whole: a temporary file first (this user only), then
 * renamed over the old one, so a reader never sees half of it.
 * @param {string} path @param {string} text
 */
function writeAtomic(path, text) {
  fs.writeFileSync(`${path}.tmp`, text, { mode: STATE_MODE });
  fs.renameSync(`${path}.tmp`, path);
}

/**
 * A model's status in router mode (GET /v1/models: { status: { value } }); undefined for a single model.
 * @param {JsonObject | undefined} m
 * @returns {unknown}
 */
function modelStatus(m) {
  const st = m?.status;
  return st && typeof st === "object" ? obj(st).value : undefined;
}

/**
 * The slot, task and base of a record (a file on this Mac, or the cache API's answer); undefined when it
 * is not one.
 * @param {JsonObject} r
 * @returns {{ slot: number, task: number, base: number } | undefined}
 */
function residentOf(r) {
  const slot = int(r.slot);
  const task = int(r.task);
  return slot !== undefined && task !== undefined ? { slot, task, base: Number(r.base) || 0 } : undefined;
}

// ------------------------------------------------------------------ the cache

/**
 * A request's marks: the session, the agent, and whether the session is a subagent's.
 * @typedef {{ session?: string, agent: string, sub?: boolean }} Meta
 */
/**
 * A session of this process as the cache last saw it.
 * @typedef {{ slot: number, epoch: number, model: string, router: boolean, payload: Payload,
 *             at: number, saved?: string, sub?: boolean, base?: number, savedN?: number }} SessionState
 */
/**
 * @typedef {{ epoch: number, maxTask: number, key?: string, stable: Map<string, boolean>,
 *             prefixes: Map<string, { file: string, tokens: number[] } | null>, speed?: number }} ModelState
 */

export class CarlCache {
  /**
   * @param {{ baseURL: string, apiKey?: string, fetch?: typeof fetch, split?: (text: string) => [string, string],
   *           log?: (msg: string) => void, home?: string, sendsReasoning?: boolean, cacheApi?: string }} o
   *   cacheApi: the dashboard's cache API (monitor/cacheapi.py), for a client whose slots folder is on
   *   another computer: the Caching settings, slot claims and records go through it
   */
  constructor(o) {
    this.base = String(o.baseURL).replace(/\/+$/, "").replace(/\/v1$/, "");
    this.cacheApi = o.cacheApi ? String(o.cacheApi).replace(/\/+$/, "") : "";
    this.apiAt = 0;
    /** @type {JsonObject | undefined} the cache API's settings (a remote client) */
    this.fromApi = undefined;
    this.apiKey = o.apiKey;
    this.fetch = o.fetch ?? globalThis.fetch;
    this.split = o.split;
    const say = o.log ?? (() => {});
    /** What the cache did (never keys or prompts): the client's log, and CARL_CACHE_LOG. @param {string} m */
    this.log = (m) => {
      say(m);
      debugLog(m);
    };
    this.home = o.home ?? homedir();
    this.sendsReasoning = o.sendsReasoning ?? true;
    this.local = /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$/.test(this.base);
    this.dir = join(this.home, ".config", "carl", "slots");
    /** @type {Map<string, SessionState>} */
    this.sessions = new Map();
    /** @type {Map<string, ModelState>} */
    this.models = new Map();
    /** @type {Map<string, number>} model:slot -> requests of this process about to run there */
    this.claimed = new Map();
    /** @type {Map<string, string>} model:slot -> the session of this process that ran there last */
    this.owner = new Map();
    this.settingsAt = 0;
    /** @type {Settings} */
    this.settingsCache = { enabled: true, prefix: true, sessions: true, diskGb: DEFAULT_GB, save: "auto", autoS: AUTO_S };
  }

  /** Is the slots folder somewhere else (a VM's client) with the dashboard's cache API to use? */
  remote() {
    return Boolean(this.cacheApi) && !(this.local && this.dirExists());
  }

  /** The headers of a call to the server or the cache API (the key only goes to these two). */
  headers() {
    /** @type {Record<string, string>} */
    const headers = { "Content-Type": "application/json" };
    if (this.apiKey) headers.Authorization = `Bearer ${this.apiKey}`;
    return headers;
  }

  /**
   * A call to the cache API; { status, json } (status 0: no API, or it didn't answer).
   * @param {"GET" | "POST"} method @param {string} path @param {JsonObject} [body]
   * @returns {Promise<{ status: number, json: unknown }>}
   */
  async api(method, path, body) {
    try {
      const r = await this.fetch(this.cacheApi + path, { method, headers: this.headers(),
                                                         body: body ? JSON.stringify(body) : undefined,
                                                         signal: AbortSignal.timeout(3_000) });
      const text = await r.text();
      return { status: r.status, json: text ? parse(text) : {} };
    } catch (e) {
      debugLog(`cache API ${method} ${path.split("?")[0]}: ${errorText(e)}`);
      return { status: 0, json: {} };
    }
  }

  /** The settings, from the cache API for a remote client (every few seconds); then settings(). */
  async loadSettings() {
    if (this.remote() && Date.now() - this.apiAt >= SETTINGS_MS) {
      this.apiAt = Date.now();
      const r = await this.api("GET", "/carl/cache/settings");
      if (r.status === 200) this.fromApi = obj(r.json);
      this.settingsAt = 0;
    }
    return this.settings();
  }

  /**
   * The Caching settings (config.json "cache" on this Mac; the cache API's for a remote client; else the defaults).
   * @returns {Settings}
   */
  settings() {
    const now = Date.now();
    if (now - this.settingsAt < SETTINGS_MS) return this.settingsCache;
    this.settingsAt = now;
    /** @type {Settings} */
    const s = { enabled: process.env.CARL_CACHE !== "0", prefix: true, sessions: true, diskGb: DEFAULT_GB, save: "auto",
                autoS: AUTO_S };
    if (this.local || this.fromApi) {
      try {
        const c = this.fromApi ?? obj(obj(parse(fs.readFileSync(join(this.home, ".config", "carl", "config.json"), "utf8"))).cache);
        s.prefix = c.prefix !== false;
        s.sessions = c.sessions !== false;
        const disk = int(c.disk_gb);
        if (disk !== undefined && disk > 0) s.diskGb = disk;
        if (typeof c.save === "string" && SAVES.includes(c.save)) s.save = c.save;
        const auto = int(c.auto_s);
        if (auto !== undefined && auto > 0) s.autoS = auto;
      } catch { /* no settings file: the defaults */ }
    }
    const env = process.env.CARL_CACHE_SAVE;      // a VM's clients: the Caching panel doesn't reach them
    if (env && SAVES.includes(env)) s.save = env;
    s.enabled = s.enabled && (s.prefix || s.sessions);
    this.settingsCache = s;
    return s;
  }

  /**
   * A call to llama-server; its answer (an error for an HTTP error or a timeout).
   * @param {"GET" | "POST"} method @param {string} path @param {JsonObject} [body] @param {number} [timeoutMs]
   * @returns {Promise<unknown>}
   */
  async call(method, path, body, timeoutMs = 600_000) {
    const r = await this.fetch(this.base + path, {
      method, headers: this.headers(), body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(timeoutMs),
    });
    const text = await r.text();
    if (!r.ok) throw new Error(`${path}: HTTP ${r.status} ${text.slice(0, 160)}`);
    return text ? parse(text) : {};
  }

  /**
   * The models the server offers (GET /v1/models: in router mode each with its status).
   * @param {number} timeoutMs
   * @returns {Promise<JsonObject[]>}
   */
  async listModels(timeoutMs) {
    return arr(obj(await this.call("GET", "/v1/models", undefined, timeoutMs)).data).map(obj);
  }

  /** @param {string} model @param {boolean} router */
  withModel(model, router) {
    return router ? { model } : {};
  }

  /**
   * Is the server a router; in router mode the model is loaded first (undefined: not ours to handle).
   * @param {string} model
   * @returns {Promise<{ router: boolean } | undefined>}
   */
  async server(model) {
    const data = await this.listModels(5_000);
    if (!data.some((m) => typeof modelStatus(m) === "string")) return { router: false };
    const me = data.find((m) => m.id === model);
    if (!me) return undefined;                     // not installed: the router answers with its own error
    if (modelStatus(me) === "loaded") return { router: true };
    const loaded = data.find((m) => modelStatus(m) === "loaded");
    if (loaded && this.settings().sessions) {
      await this.saveRecorded(String(loaded.id)).catch((e) => debugLog(`saving the recorded sessions: ${errorText(e)}`));
    }
    this.log(`loading ${model} (router) before the request, to restore its saved state`);
    // a load asked for while the router switches to another model (a client's title request, say) is
    // refused: ask again while the model is still unloaded
    const end = Date.now() + LOAD_WAIT_MS;
    let asked = 0;
    while (Date.now() < end) {
      const now = await this.listModels(5_000).catch(() => []);
      const st = modelStatus(now.find((m) => m.id === model));
      if (st === "loaded") return { router: true };
      if (st === "failed") return undefined;
      if (st !== "loading" && Date.now() - asked > LOAD_RETRY_MS) {
        asked = Date.now();
        await this.call("POST", "/models/load", { model }, 30_000).catch((e) => debugLog(`load ${model}: ${errorText(e)}`));
      }
      await new Promise((ok) => setTimeout(ok, 1000));
    }
    return undefined;
  }

  /**
   * A one-off request (a title) for a model the router hasn't loaded goes to the loaded one instead:
   * a switch for it would unload the model the session runs on, twice.
   * @param {Payload} payload
   * @returns {Promise<Payload>}
   */
  async loadedInstead(payload) {
    try {
      const data = await this.listModels(5_000);
      const loaded = data.find((m) => modelStatus(m) === "loaded");
      if (!loaded || loaded.id === payload.model || !data.some((m) => m.id === payload.model)) return payload;
      this.log(`${payload.model} isn't loaded: this one-off request goes to ${loaded.id}`);
      return { ...payload, model: loaded.id };
    } catch (e) {
      debugLog(`the loaded model: ${errorText(e)}`);
      return payload;
    }
  }

  /**
   * The model's slots as (id, busy, tokens, task).
   * @param {string} model @param {boolean} router
   * @returns {Promise<Slot[]>}
   */
  async slots(model, router) {
    const r = await this.call("GET", `/slots${router ? `?model=${encodeURIComponent(model)}` : ""}`, undefined, 5_000);
    /** @type {Slot[]} */
    const out = [];
    for (const s of arr(r).map(obj)) {
      const id = int(s.id);
      if (id === undefined) continue;
      out.push({ id, busy: Boolean(s.is_processing),
                 n: (Number(s.n_prompt_tokens) || 0) + (Number(obj(arr(s.next_token)[0]).n_decoded) || 0),
                 task: int(s.id_task) ?? -1 });
    }
    return out;
  }

  /**
   * The model's state; a new epoch when the server restarted (its task ids start again).
   * @param {string} model @param {{ task: number }[]} slots
   */
  model(model, slots) {
    let m = this.models.get(model);
    if (!m) {
      m = { epoch: 0, maxTask: -1, stable: new Map(), prefixes: new Map() };
      this.models.set(model, m);
    }
    const max = Math.max(-1, ...slots.map((s) => s.task));
    if (max < m.maxTask) {
      m.epoch++;
      m.key = undefined;
      m.stable.clear();
      m.prefixes.clear();
    }
    m.maxTask = max;
    return m;
  }

  /**
   * What a saved state depends on besides its tokens: the model file and the llama.cpp build.
   * @param {string} model @param {boolean} router @param {ModelState} m
   */
  async serverKey(model, router, m) {
    if (!m.key) {
      const p = obj(await this.call("GET", `/props${router ? `?model=${encodeURIComponent(model)}` : ""}`, undefined, 5_000));
      m.key = shortHash(`${p.model_path ?? model}|${p.build_info ?? ""}`, 10);
    }
    return m.key;
  }

  /** @param {string} model @param {number} slot */
  isClaimed(model, slot) {
    return (this.claimed.get(`${model}:${slot}`) ?? 0) > 0;
  }

  /**
   * Take a slot for a request of this process until the returned release() (the request reached the
   * server); undefined when another request has it. On this Mac a claim file (.claim+MODEL+SLOT in the
   * slots folder, created only if absent) keeps the other OpenCode and Pi processes off it too; a remote
   * client claims through the cache API (the same files; no API: as before the claims).
   * @param {string} model @param {number} slot
   * @returns {Promise<(() => void) | undefined>}
   */
  async tryClaim(model, slot) {
    const k = `${model}:${slot}`;
    if (this.isClaimed(model, slot)) return undefined;
    const path = this.local && this.dirExists() ? join(this.dir, `.claim+${safeName(model, 60)}+${slot}`) : "";
    if (path && !takeFile(path)) return undefined;
    const remote = !path && this.remote();
    if (remote && (await this.api("POST", "/carl/cache/claim", { model, slot })).status === 409) return undefined;
    this.claimed.set(k, (this.claimed.get(k) ?? 0) + 1);
    let done = false;
    const release = () => {
      if (done) return;
      done = true;
      this.claimed.set(k, Math.max(0, (this.claimed.get(k) ?? 1) - 1));
      if (path) {
        try {
          fs.unlinkSync(path);
        } catch { /* gone */ }
      } else if (remote) {
        void this.api("POST", "/carl/cache/release", { model, slot });
      }
    };
    setTimeout(release, 60_000).unref?.();
    return release;
  }

  /**
   * Prepare a request: the volatile parts moved, the slot filled and pinned. Returns the payload to send
   * and a function to call once the server has the request (it frees the slot for this process's others).
   * @param {Payload} payload @param {Meta} meta
   * @returns {Promise<{ payload: Payload, release: () => void }>}
   */
  async before(payload, meta) {
    const none = { payload, release: () => {} };
    const set = await this.loadSettings();
    if (!set.enabled || !Array.isArray(payload?.messages)) return none;
    const out = set.prefix && this.split ? relocate(payload, this.split) : payload;
    const moved = { payload: out, release: () => {} };
    if (NO_PIN_AGENTS.has(meta.agent)) return { ...moved, payload: await this.loadedInstead(quick(out)) };
    try {
      const model = String(out.model ?? "");
      const srv = await this.server(model);
      if (!srv) return moved;
      const slots = await this.slots(model, srv.router);
      const m = this.model(model, slots);
      const id = `${model}\n${meta.session ?? ""}\n${meta.agent}`;
      const known = meta.session ? this.sessions.get(id) : undefined;
      let slot;
      let how = "its own";
      /** @type {(() => void) | undefined} */
      let release;
      let base = known?.base;
      if (known && known.epoch === m.epoch) {      // its own slot, unless another session of ours ran there since
        const s = slots.find((x) => x.id === known.slot);
        if (s && !s.busy && this.owner.get(`${model}:${s.id}`) === id && (release = await this.tryClaim(model, s.id))) slot = s.id;
      } else if (meta.session && !meta.sub) {         // new to this process: still in a slot, says a record on this Mac?
        const file = sessionFile(model, await this.serverKey(model, srv.router, m), meta.session);
        const rec = await this.recorded(model, file);
        const s = rec && slots.find((x) => x.id === rec.slot && x.task === rec.task && !x.busy);
        if (s && rec && (release = await this.tryClaim(model, s.id))) {
          slot = s.id;
          base = rec.base;
          how = "its own, as recorded";
        }
      }
      if (slot === undefined) {
        let pick;
        for (const c of await this.rankFree(model, slots, meta.session)) {
          if ((release = await this.tryClaim(model, c.id))) {
            pick = c;
            break;
          }
        }
        if (!pick || !release) return moved;          // every slot busy or taken: llama.cpp decides
        slot = pick.id;
        // from the disk: a session new to this process (or to this server), or one whose slot another
        // session took since its last save; else llama.cpp finds it in its RAM cache
        if (!(known && known.epoch === m.epoch) || known.saved) {
          try {
            base = await this.fill(out, model, srv.router, m, pick, meta, set);
          } catch (e) {
            release();
            throw e;
          }
          how = "filled";
        } else {
          how = "the RAM cache's";
        }
      }
      this.owner.set(`${model}:${slot}`, id);
      this.log(`request: ${meta.agent} ${meta.session ?? "-"} -> slot ${slot} (${how})`);
      if (meta.session) {
        this.sessions.set(id, { ...(known ?? {}), slot, epoch: m.epoch, model, router: srv.router, payload: out,
                                at: Date.now(), sub: Boolean(meta.sub), base });
        await this.turn(model, slot, meta.session, true);
      }
      return { payload: { ...out, id_slot: slot }, release: release ?? (() => {}) };
    } catch (e) {
      this.log(`before: ${errorText(e)}`);
      return moved;
    }
  }

  /**
   * Put the best saved state into the slot: the session's file, else the agent's prompt file (read once
   * and saved when there is none).
   * @param {Payload} payload @param {string} model @param {boolean} router @param {ModelState} m
   * @param {Slot} pick @param {Meta} meta @param {Settings} set
   * @returns {Promise<number | undefined>} the tokens put in
   */
  async fill(payload, model, router, m, pick, meta, set) {
    const key = await this.serverKey(model, router, m);
    const before = this.owner.get(`${model}:${pick.id}`);
    const other = before ? this.sessions.get(before) : undefined;
    if (before && other && other.epoch === m.epoch && set.sessions && set.save !== "stop" && pick.n >= MIN_SESSION_TOKENS) {
      // another session of ours is in the slot: saved first (set back to the end of its last prompt, so its
      // next prompt continues it), then put back from its file when it returns
      const [, session, agent] = before.split("\n");
      if (session && !other.sub && !NO_SAVE_AGENTS.has(agent)) await this.save(other, session, pick, m, true);
    }
    if (pick.n > 0) {          // the slot's conversation goes to the RAM cache first (llama.cpp does that for a new task)
      await this.call("POST", "/completion", { prompt: [0], n_predict: 0, cache_prompt: true, id_slot: pick.id,
                                               ...this.withModel(model, router) }, 60_000);
    }
    const got = set.sessions && meta.session && !meta.sub
      ? await this.restore(model, router, pick.id, sessionFile(model, key, meta.session)) : undefined;
    if (got !== undefined) {
      this.log(`session ${meta.session}: restored into slot ${pick.id}`);
      return got;
    }
    if (!set.prefix) return undefined;
    const pf = await this.prefix(payload, model, router, m, meta.agent);
    if (!pf) return undefined;
    if (await this.restore(model, router, pick.id, pf.file) !== undefined) {
      this.log(`${meta.agent}'s prompt (${pf.tokens.length} tokens) restored into slot ${pick.id}`);
      return pf.tokens.length;
    }
    if (this.diskFull()) return undefined;
    const t0 = Date.now();
    await this.call("POST", "/completion", { prompt: pf.tokens, n_predict: 0, cache_prompt: true, id_slot: pick.id,
                                             ...this.withModel(model, router) }, 3_600_000);
    await this.call("POST", `/slots/${pick.id}?action=save`, { filename: pf.file, ...this.withModel(model, router) });
    const secs = (Date.now() - t0) / 1000;
    m.speed = pf.tokens.length / Math.max(secs, 0.1);       // this model's read speed (the auto save rule)
    this.log(`${meta.agent}'s prompt (${pf.tokens.length} tokens) read in ${secs.toFixed(1)} s and saved`);
    this.wrote(pf.file, pf.file.slice(0, pf.file.lastIndexOf("+") + 1));
    return pf.tokens.length;
  }

  /**
   * Put a saved state into the slot; the tokens it holds, undefined when it can't (no such file).
   * @param {string} model @param {boolean} router @param {number} slot @param {string} file
   * @returns {Promise<number | undefined>}
   */
  async restore(model, router, slot, file) {
    if (this.local && !this.exists(file)) return undefined;
    if (!(await this.whole(file))) return undefined;
    try {
      const r = await this.call("POST", `/slots/${slot}?action=restore`, { filename: file, ...this.withModel(model, router) },
                                120_000);
      return Number(obj(r).n_restored) || 0;
    } catch (e) {
      debugLog(`restore ${file} into slot ${slot}: ${errorText(e)}`);
      return undefined;
    }
  }

  /** @param {string} file */
  exists(file) {
    for (const f of [file, `${file}.zst`]) {
      try {
        fs.statSync(join(this.dir, f));
        return true;
      } catch { /* the next form */ }
    }
    return !this.dirExists();             // the folder isn't here (the server is elsewhere): try it
  }

  /**
   * A conversation stored as a patch against its prompt (tools/monitor/slotpack.py) made whole again
   * before a restore: here with zstd, for a remote client by the dashboard's API. True when it is whole.
   * @param {string} file
   */
  async whole(file) {
    if (!file.startsWith("carl-session+")) return true;
    if (!(this.local && this.dirExists())) {
      if (!this.remote()) return true;    // nothing to do from here: the restore says
      return (await this.api("POST", "/carl/cache/unpack", { file })).status !== 404;
    }
    if (!isStateName(file)) return false;
    const path = join(this.dir, file);
    if (fs.existsSync(path)) return true;
    const meta = obj(json(`${path}.json`));
    // the prompt it is a patch against: a name in the same folder, never a path
    const base = isStateName(meta.base) ? join(this.dir, meta.base) : "";
    if (!base || !fs.existsSync(base) || !fs.existsSync(`${path}.zst`)) return false;
    const r = spawnSync("zstd", ["-q", "-f", "--long=31", "-d", `--patch-from=${base}`, `${path}.zst`, "-o", `${path}.tmp`],
                        { stdio: "ignore", timeout: 600_000 });
    try {
      if (r.status !== 0 || fs.statSync(`${path}.tmp`).size !== meta.size) throw new Error(`zstd exit ${r.status}`);
      if (typeof meta.packed_at !== "number") throw new Error("no packed_at");
      fs.utimesSync(`${path}.tmp`, meta.packed_at, meta.packed_at);   // a copy: the dashboard removes it later
      fs.renameSync(`${path}.tmp`, path);
      return true;
    } catch (e) {
      debugLog(`unpack ${file}: ${errorText(e)}`);
      try {
        fs.unlinkSync(`${path}.tmp`);
      } catch { /* none */ }
      return false;
    }
  }

  /** Less than MIN_FREE_BYTES free where the server saves (this Mac only; elsewhere unknown: false). */
  diskFull() {
    if (!this.local || !this.dirExists()) return false;
    try {
      const st = fs.statfsSync?.(this.dir);         // not in every runtime: then unknown
      return st ? st.bavail * st.bsize < MIN_FREE_BYTES : false;
    } catch {
      return false;
    }
  }

  dirExists() {
    try {
      return fs.statSync(this.dir).isDirectory();
    } catch {
      return false;
    }
  }

  /**
   * The agent's prompt as tokens (the system text and the tools, up to where the first user message
   * starts) and its file; undefined when it is too short to be worth one.
   * @param {Payload} payload @param {string} model @param {boolean} router @param {ModelState} m @param {string} agent
   * @returns {Promise<{ file: string, tokens: number[] } | undefined>}
   */
  async prefix(payload, model, router, m, agent) {
    const sys = obj(arr(payload.messages)[0]);
    if (sys.role !== "system") return undefined;
    const fields = templateFields(payload);
    const id = shortHash(JSON.stringify([sys.content, fields]), 16);
    if (m.prefixes.has(id)) return m.prefixes.get(id) ?? undefined;
    const render = async (/** @type {string} */ text) => {
      const r = await this.call("POST", "/apply-template", { messages: [sys, { role: "user", content: text }], ...fields,
                                                            ...this.withModel(model, router) });
      return this.tokens(String(obj(r).prompt ?? ""), model, router);
    };
    const [a, b] = [await render("x"), await render("y")];
    const n = commonPrefix(a, b);
    const key = await this.serverKey(model, router, m);
    const pf = n >= MIN_PREFIX_TOKENS
      ? { file: prefixFile(model, agent, shortHash(key + JSON.stringify(a.slice(0, n)))), tokens: a.slice(0, n) } : null;
    m.prefixes.set(id, pf);
    return pf ?? undefined;
  }

  /** @param {string} text @param {string} model @param {boolean} router @returns {Promise<number[]>} */
  async tokens(text, model, router) {
    const r = await this.call("POST", "/tokenize", { content: text, add_special: true, parse_special: true,
                                                     ...this.withModel(model, router) });
    return arr(obj(r).tokens).map(Number);
  }

  /**
   * Does the model's template re-render a reply as it was generated (then a slot saved after a reply
   * is a prefix of the next prompt)? Checked once per model and thinking mode.
   * @param {Payload} payload @param {string} model @param {boolean} router @param {ModelState} m
   * @returns {Promise<boolean>}
   */
  async stable(payload, model, router, m) {
    const off = thinkingOff(payload);
    const k = off ? "off" : "on";
    const have = m.stable.get(k);
    if (have !== undefined) return have;
    const fields = templateFields(payload);
    delete fields.tools;
    const r = off ? "" : "carl-probe-reasoning";
    const asst = { role: "assistant", content: "carl-probe-answer", ...(this.sendsReasoning && r ? { reasoning_content: r } : {}) };
    const render = async (/** @type {JsonObject[]} */ messages) =>
      String(obj(await this.call("POST", "/apply-template", { messages, ...fields, ...this.withModel(model, router) })).prompt ?? "");
    const p = await render([{ role: "user", content: "carl-probe-question" }]);
    const h = await render([{ role: "user", content: "carl-probe-question" }, asst, { role: "user", content: "carl-probe-next" }]);
    const ok = h.startsWith(p) && (!r || h.includes(r));
    m.stable.set(k, ok);
    return ok;
  }

  /**
   * A turn ended: save the session's conversation (main sessions; not subagents or one-off prompts).
   * @param {Meta} meta
   */
  async after(meta) {
    try {
      await this.saveTurn(meta);
    } finally {
      await this.turnsDone(meta.session);
    }
  }

  /** @param {Meta} meta */
  async saveTurn(meta) {
    const set = await this.loadSettings();
    if (!set.enabled || !set.sessions || meta.sub || !meta.session || NO_SAVE_AGENTS.has(meta.agent)) return;
    /** @type {SessionState | undefined} */
    let e;
    for (const [k, v] of this.sessions) {        // the session's latest request (OpenCode's idle event names no agent)
      const [, s, agent] = k.split("\n");
      if (s === meta.session && !NO_SAVE_AGENTS.has(agent) && (meta.agent === "*" || agent === meta.agent) && (!e || v.at > e.at)) e = v;
    }
    if (!e) {
      this.log(`after: no request of session ${meta.session} seen`);
      return;
    }
    try {
      const { model, router } = e;
      let slots = await this.slots(model, router);
      for (let i = 0; i < 30 && slots.find((x) => x.id === e?.slot)?.busy; i++) {   // the reply's last bytes just left
        await new Promise((ok) => setTimeout(ok, 100));
        slots = await this.slots(model, router);
      }
      const m = this.model(model, slots);
      const s = slots.find((x) => x.id === e.slot);
      if (!s || s.busy || e.epoch !== m.epoch || s.n < MIN_SESSION_TOKENS) {
        this.log(`after: session ${meta.session} not saved (slot ${e.slot}: ${s ? `${s.n} tokens${s.busy ? ", busy" : ""}` : "gone"})`);
        return;
      }
      const unsaved = s.n - Math.max(e.savedN ?? 0, e.base ?? 0);
      const mode = this.settings().save;
      if (mode === "turn" || (mode === "auto" && unsaved >= (m.speed ?? READ_SPEED) * this.settings().autoS)) {
        await this.save(e, meta.session, s, m, false);
        await this.unrecord(model, e.slot);
        return;
      }
      // the slot keeps it: a record says which session it is (saved before the server or model stops,
      // or before its slot is needed), the slot first set back where a reply won't re-render as generated
      const settled = (await this.stable(e.payload, model, router, m)) ? s : await this.settle(e);
      await this.record(model, e.slot, sessionFile(model, await this.serverKey(model, router, m), meta.session), settled.task,
                  Math.max(e.savedN ?? 0, e.base ?? 0));
      this.log(`session ${meta.session}: ${unsaved} tokens not saved yet (save = ${mode})`);
    } catch (err) {
      this.log(`after: ${errorText(err)}`);
    }
  }

  /**
   * Set a session's slot back to the end of its last prompt (the state its next prompt continues);
   * the slot as it is then.
   * @param {SessionState} e
   * @returns {Promise<Slot>}
   */
  async settle(e) {
    const { model, router } = e;
    const r = await this.call("POST", "/apply-template", { messages: e.payload.messages, ...templateFields(e.payload),
                                                          ...this.withModel(model, router) });
    const toks = await this.tokens(String(obj(r).prompt ?? ""), model, router);
    await this.call("POST", "/completion", { prompt: toks, n_predict: 0, cache_prompt: true, id_slot: e.slot,
                                             ...this.withModel(model, router) }, 3_600_000);
    const now = (await this.slots(model, router)).find((x) => x.id === e.slot);
    return now ?? { id: e.slot, busy: false, n: toks.length, task: -1 };
  }

  /**
   * Mark a slot's turn running (each request of an agent loop, tool calls too) or done (the turn's save or
   * record is on disk): the dashboard's Stop, Apply, Auto-tune and router loads can wait for the end of
   * the turn, so the session is saved whole. On this Mac a file (.turn+MODEL+SLOT, its session), else
   * through the cache API. A mark older than TURN_STALE_MS counts as gone.
   * @param {string} model @param {number} slot @param {string} session @param {boolean} running
   */
  async turn(model, slot, session, running) {
    const path = this.local && this.dirExists() ? join(this.dir, `.turn+${safeName(model, 60)}+${slot}`) : "";
    try {
      if (!path) {
        if (this.remote()) await this.api("POST", "/carl/cache/turn", { model, slot, session, running });
      } else if (running) {
        writeAtomic(path, JSON.stringify({ model, slot, session }));
      } else if (obj(json(path)).session === session) {
        fs.unlinkSync(path);                // only its own: another session may run in that slot now
      }
    } catch (e) {                           // no mark: the dashboard waits for an idle slot only
      debugLog(`turn mark ${path || "(cache API)"}: ${errorText(e)}`);
    }
  }

  /**
   * The free slots, best first: a slot where another session's turn still runs (an agent loop between two
   * requests: a tool runs) last, so a new session doesn't push it out; then the emptiest.
   * @param {string} model @param {Slot[]} slots @param {string | undefined} session
   * @returns {Promise<Slot[]>}
   */
  async rankFree(model, slots, session) {
    const free = rankSlots(slots);
    /** @type {Set<number>} */
    const running = new Set();
    if (this.local && this.dirExists()) {
      const head = `.turn+${safeName(model, 60)}+`;
      let names = /** @type {string[]} */ ([]);
      try {
        names = fs.readdirSync(this.dir);
      } catch (e) {
        debugLog(`turn marks: ${errorText(e)}`);
      }
      for (const n of names) {
        if (!n.startsWith(head) || !/^\d+$/.test(n.slice(head.length))) continue;    // not a mark (a .tmp being written)
        const slot = Number(n.slice(head.length));
        try {                               // one mark at a time: one that goes meanwhile does not hide the others
          const path = join(this.dir, n);
          if (Date.now() - fs.statSync(path).mtimeMs > TURN_STALE_MS) continue;
          if (obj(json(path)).session !== session) running.add(slot);
        } catch { /* gone meanwhile */ }
      }
    } else if (this.remote()) {
      const r = await this.api("GET", `/carl/cache/turns?model=${encodeURIComponent(model)}`);
      for (const t of r.status === 200 ? arr(obj(r.json).turns).map(obj) : []) {
        const slot = int(t.slot);
        if (slot !== undefined && t.session !== session) running.add(slot);
      }
    }
    return [...free.filter((s) => !running.has(s.id)), ...free.filter((s) => running.has(s.id))];
  }

  /** Every turn of a session is done (its slots). @param {string | undefined} session */
  async turnsDone(session) {
    if (!session) return;
    for (const [k, v] of this.sessions) {
      if (k.split("\n")[1] === session) await this.turn(v.model, v.slot, session, false);
    }
  }

  /** The record file of a slot (this Mac only). @param {string} model @param {number} slot */
  recordPath(model, slot) {
    return this.local && this.dirExists() ? join(this.dir, `.resident+${safeName(model, 60)}+${slot}.json`) : "";
  }

  /**
   * Record which session a slot holds (its task id: a slot used since is not saved under its name), and
   * how much of it a file already holds (base: the auto rule counts only the rest).
   * @param {string} model @param {number} slot @param {string} file @param {number} task @param {number} base
   */
  async record(model, slot, file, task, base) {
    const path = this.recordPath(model, slot);
    if (!path) {
      if (this.remote()) await this.api("POST", "/carl/cache/record", { model, slot, file, task, base });
      return;
    }
    try {
      writeAtomic(path, JSON.stringify({ model, slot, file, task, base }));
    } catch (e) {                           // no record: the session waits for its next save
      debugLog(`record ${path}: ${errorText(e)}`);
    }
  }

  /**
   * The slot a record on this Mac says holds this session's state (its task id then).
   * @param {string} model @param {string} file
   * @returns {Promise<{ slot: number, task: number, base: number } | undefined>}
   */
  async recorded(model, file) {
    if (!this.local || !this.dirExists()) {
      if (!this.remote()) return undefined;
      const r = await this.api("GET", `/carl/cache/record?model=${encodeURIComponent(model)}&file=${encodeURIComponent(file)}`);
      return r.status === 200 ? residentOf(obj(r.json)) : undefined;
    }
    const head = `.resident+${safeName(model, 60)}+`;
    try {
      for (const n of fs.readdirSync(this.dir)) {
        if (!n.startsWith(head) || !n.endsWith(".json")) continue;
        const r = obj(json(join(this.dir, n)));
        const got = r.model === model && r.file === file ? residentOf(r) : undefined;
        if (got) return got;
      }
    } catch (e) {
      debugLog(`records: ${errorText(e)}`);
    }
    return undefined;
  }

  /** @param {string} model @param {number} slot */
  async unrecord(model, slot) {
    const path = this.recordPath(model, slot);
    if (!path && this.remote()) await this.api("POST", "/carl/cache/unrecord", { model, slot });
    if (path) {
      try {
        fs.unlinkSync(path);
      } catch { /* none */ }
    }
  }

  /**
   * Before the model stops (a router switch): save the sessions the clients on this Mac recorded in its
   * slots, each only if its slot still holds that state.
   * @param {string} model
   */
  async saveRecorded(model) {
    if (this.settings().save === "turn") return;
    if (this.remote()) {                            // the dashboard has the files: it saves them
      await this.api("POST", "/carl/cache/save-recorded", { model });
      return;
    }
    if (!this.local || !this.dirExists() || this.diskFull()) return;
    const head = `.resident+${safeName(model, 60)}+`;
    let names;
    try {
      names = fs.readdirSync(this.dir).filter((n) => n.startsWith(head) && n.endsWith(".json"));
    } catch {
      return;
    }
    if (!names.length) return;
    const slots = await this.slots(model, true);
    for (const n of names) {
      try {
        const r = obj(json(join(this.dir, n)));
        const s = slots.find((x) => x.id === r.slot);
        if (r.model !== model || !s || s.busy || s.task !== r.task || !isStateName(r.file) || !r.file.startsWith("carl-session+")) {
          continue;
        }
        await this.call("POST", `/slots/${s.id}?action=save`, { filename: r.file, model });
        fs.unlinkSync(join(this.dir, n));
        this.log(`${r.file}: saved from slot ${s.id} before ${model} stops`);
      } catch (e) {                         // the next one
        debugLog(`save from record ${n}: ${errorText(e)}`);
      }
    }
  }

  /**
   * Save a session's slot to its file (unless it is saved as it is). settle: first set the slot back to
   * the end of the session's last prompt; else only when the template doesn't re-render replies as
   * generated (stable()).
   * @param {SessionState} e @param {string} session @param {Slot} s
   * @param {ModelState} m @param {boolean} settle
   */
  async save(e, session, s, m, settle) {
    const { model, router } = e;
    if (e.saved === `${s.task}:${s.n}`) return;
    if (this.diskFull()) {
      this.log(`session ${session} not saved: less than 10 GB free on the disk`);
      return;
    }
    const now = settle || !(await this.stable(e.payload, model, router, m)) ? await this.settle(e) : s;
    const file = sessionFile(model, await this.serverKey(model, router, m), session);
    const r = await this.call("POST", `/slots/${e.slot}?action=save`, { filename: file, ...this.withModel(model, router) });
    e.saved = `${now.task}:${now.n}`;
    e.savedN = Number(obj(r).n_saved) || now.n;
    this.log(`session ${session}: ${e.savedN} tokens saved from slot ${e.slot}${settle ? " (its slot is needed)" : ""}`);
    this.wrote(file, "");
  }

  /**
   * After a save on this Mac: the same agent's older prompt files go (`group`), then the oldest files
   * over the disk limit.
   * @param {string} file @param {string} group
   */
  wrote(file, group) {
    if (!this.local || !this.dirExists()) return;
    const files = listing(this.dir);
    const same = (/** @type {string} */ n) => n.startsWith(group) && /^[0-9a-f]+\.bin$/.test(n.slice(group.length));
    const gone = group ? files.filter((f) => same(f.name) && f.name !== file).map((f) => f.name) : [];
    const rest = files.filter((f) => !gone.includes(f.name));
    gone.push(...overBudget(rest, this.settings().diskGb * 1e9, file));
    for (const n of gone) removeState(this.dir, n);
  }
}
