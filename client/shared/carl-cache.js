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
// leaves the request as it was: the cache is never in the way.

import { createHash } from "node:crypto";
import * as fs from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

export const MIN_PREFIX_TOKENS = 1024;   // a shorter prompt is quick to read: no file
export const MIN_SESSION_TOKENS = 4096;  // a shorter conversation is quick to read again
export const MIN_FREE_BYTES = 10 * 2 ** 30;  // no saves on this Mac below this much free disk
export const NO_PIN_AGENTS = new Set(["title", "summary"]);            // small one-off prompts
export const NO_SAVE_AGENTS = new Set(["title", "summary", "compaction"]);
const LOAD_WAIT_MS = 180_000;        // a switch takes 30 s to 2 min
const LOAD_RETRY_MS = 5_000;
const SETTINGS_MS = 5_000;
const OFF = new Set(["none", "minimal", "off", "disable", "disabled"]);

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

/** @param {readonly number[]} a @param {readonly number[]} b */
export function commonPrefix(a, b) {
  let n = 0;
  while (n < a.length && n < b.length && a[n] === b[n]) n++;
  return n;
}

/**
 * The slot to use among (id, busy, tokens): an idle empty one, else the idle one holding least.
 * @param {{ id: number, busy: boolean, n: number }[]} slots
 */
export function chooseSlot(slots) {
  const idle = slots.filter((s) => !s.busy).sort((a, b) => a.n - b.n || a.id - b.id);
  return idle[0];
}

/** Does the request turn thinking off? @param {Record<string, any>} p */
export function thinkingOff(p) {
  const kw = p.chat_template_kwargs ?? {};
  return kw.enable_thinking === false || (typeof p.reasoning_effort === "string" && OFF.has(p.reasoning_effort)) ||
    (typeof kw.reasoning_effort === "string" && OFF.has(kw.reasoning_effort));
}

/**
 * A one-off request (a title, a summary) without thinking: thinking there only holds a slot longer
 * (measured: a title on the 35B, 35 s with thinking on).
 * @param {Record<string, any>} p
 */
export function quick(p) {
  return { ...p, reasoning_effort: "none", chat_template_kwargs: { ...(p.chat_template_kwargs ?? {}), enable_thinking: false } };
}

/** The request fields the chat template reads. @param {Record<string, any>} p */
export function templateFields(p) {
  /** @type {Record<string, any>} */
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
 * @param {Record<string, any>} payload
 * @param {(text: string) => [string, string]} split
 */
export function relocate(payload, split) {
  const msgs = payload.messages;
  if (!Array.isArray(msgs) || msgs[0]?.role !== "system") return payload;
  const sys = msgs[0];
  const text = typeof sys.content === "string" ? sys.content
    : Array.isArray(sys.content) ? sys.content.map((p) => (p && typeof p.text === "string" ? p.text : "")).join("") : null;
  const u = msgs.findIndex((m) => m?.role === "user");
  if (text === null || u < 0) return payload;
  const [kept, moved] = split(text);
  if (!moved) return payload;
  const user = msgs[u];
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
 * @param {{ name: string, bytes: number, mtime: number }[]} files
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
  const out = [];
  for (const f of order) {
    if (total <= limit) break;
    out.push(f.name);
    total -= f.bytes;
  }
  if (total > limit && files.some((f) => f.name === keep)) out.push(keep);
  return out;
}

/** @param {string} dir */
function listing(dir) {
  try {
    return fs.readdirSync(dir).filter((n) => /^carl-(prefix|session)\+.*\.bin$/.test(n)).map((name) => {
      const st = fs.statSync(join(dir, name));
      return { name, bytes: st.size, mtime: st.mtimeMs };
    });
  } catch {
    return [];
  }
}

// ------------------------------------------------------------------ the cache

/**
 * @typedef {{ session?: string, agent: string, sub?: boolean }} Meta
 * @typedef {{ slot: number, epoch: number, model: string, router: boolean, payload: Record<string, any>,
 *             at: number, saved?: string, sub?: boolean }} SessionState
 * @typedef {{ epoch: number, maxTask: number, key?: string, stable: Map<string, boolean>,
 *             prefixes: Map<string, { file: string, tokens: number[] } | null> }} ModelState
 */

export class CarlCache {
  /**
   * @param {{ baseURL: string, apiKey?: string, fetch?: typeof fetch, split?: (text: string) => [string, string],
   *           log?: (msg: string) => void, home?: string, sendsReasoning?: boolean }} o
   */
  constructor(o) {
    this.base = String(o.baseURL).replace(/\/+$/, "").replace(/\/v1$/, "");
    this.apiKey = o.apiKey;
    this.fetch = o.fetch ?? globalThis.fetch;
    this.split = o.split;
    const file = process.env.CARL_CACHE_LOG;      // a debug log: what the cache did (never keys or prompts)
    const say = o.log ?? (() => {});
    this.log = file ? (/** @type {string} */ m) => {
      say(m);
      try {
        fs.appendFileSync(file, `${new Date().toISOString()} ${m}\n`);
      } catch { /* no log */ }
    } : say;
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
    this.settingsCache = { enabled: true, prefix: true, sessions: true, diskGb: 5 };
  }

  /** The Caching settings (config.json "cache" on this Mac; the defaults elsewhere). */
  settings() {
    const now = Date.now();
    if (now - this.settingsAt < SETTINGS_MS) return this.settingsCache;
    this.settingsAt = now;
    const s = { enabled: process.env.CARL_CACHE !== "0", prefix: true, sessions: true, diskGb: 5 };
    if (this.local) {
      try {
        const c = JSON.parse(fs.readFileSync(join(this.home, ".config", "carl", "config.json"), "utf8"))?.cache ?? {};
        s.prefix = c.prefix !== false;
        s.sessions = c.sessions !== false;
        if (Number.isInteger(c.disk_gb) && c.disk_gb > 0) s.diskGb = c.disk_gb;
      } catch { /* no settings file: the defaults */ }
    }
    s.enabled = s.enabled && (s.prefix || s.sessions);
    this.settingsCache = s;
    return s;
  }

  /**
   * @param {"GET" | "POST"} method @param {string} path @param {Record<string, any>} [body] @param {number} [timeoutMs]
   * @returns {Promise<any>}
   */
  async call(method, path, body, timeoutMs = 600_000) {
    /** @type {Record<string, string>} */
    const headers = { "Content-Type": "application/json" };
    if (this.apiKey) headers.Authorization = `Bearer ${this.apiKey}`;
    const r = await this.fetch(this.base + path, {
      method, headers, body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(timeoutMs),
    });
    const text = await r.text();
    if (!r.ok) throw new Error(`${path}: HTTP ${r.status} ${text.slice(0, 160)}`);
    return text ? JSON.parse(text) : {};
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
    const r = await this.call("GET", "/v1/models", undefined, 5_000);
    const data = Array.isArray(r?.data) ? r.data : [];
    const status = (/** @type {any} */ m) => (m?.status && typeof m.status === "object" ? m.status.value : undefined);
    if (!data.some((m) => typeof status(m) === "string")) return { router: false };
    const me = data.find((m) => m?.id === model);
    if (!me) return undefined;                     // not installed: the router answers with its own error
    if (status(me) === "loaded") return { router: true };
    this.log(`loading ${model} (router) before the request, to restore its saved state`);
    // a load asked for while the router switches to another model (a client's title request, say) is
    // refused: ask again while the model is still unloaded
    const end = Date.now() + LOAD_WAIT_MS;
    let asked = 0;
    while (Date.now() < end) {
      const now = await this.call("GET", "/v1/models", undefined, 5_000).catch(() => undefined);
      const st = status((now?.data ?? []).find((/** @type {any} */ m) => m?.id === model));
      if (st === "loaded") return { router: true };
      if (st === "failed") return undefined;
      if (st !== "loading" && Date.now() - asked > LOAD_RETRY_MS) {
        asked = Date.now();
        await this.call("POST", "/models/load", { model }, 30_000).catch(() => {});
      }
      await new Promise((ok) => setTimeout(ok, 1000));
    }
    return undefined;
  }

  /**
   * A one-off request (a title) for a model the router hasn't loaded goes to the loaded one instead:
   * a switch for it would unload the model the session runs on, twice.
   * @param {Record<string, any>} payload
   */
  async loadedInstead(payload) {
    try {
      const r = await this.call("GET", "/v1/models", undefined, 5_000);
      const data = Array.isArray(r?.data) ? r.data : [];
      const loaded = data.find((m) => m?.status && typeof m.status === "object" && m.status.value === "loaded");
      if (!loaded || loaded.id === payload.model || !data.some((m) => m?.id === payload.model)) return payload;
      this.log(`${payload.model} isn't loaded: this one-off request goes to ${loaded.id}`);
      return { ...payload, model: loaded.id };
    } catch {
      return payload;
    }
  }

  /**
   * The model's slots as (id, busy, tokens, task).
   * @param {string} model @param {boolean} router
   * @returns {Promise<{ id: number, busy: boolean, n: number, task: number }[]>}
   */
  async slots(model, router) {
    const r = await this.call("GET", `/slots${router ? `?model=${encodeURIComponent(model)}` : ""}`, undefined, 5_000);
    return (Array.isArray(r) ? r : []).filter((s) => Number.isInteger(s?.id)).map((s) => ({
      id: s.id, busy: Boolean(s.is_processing),
      n: (Number(s.n_prompt_tokens) || 0) + (Number(s.next_token?.[0]?.n_decoded) || 0),
      task: Number.isInteger(s.id_task) ? s.id_task : -1,
    }));
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
      const p = await this.call("GET", `/props${router ? `?model=${encodeURIComponent(model)}` : ""}`, undefined, 5_000);
      m.key = shortHash(`${p?.model_path ?? model}|${p?.build_info ?? ""}`, 10);
    }
    return m.key;
  }

  /** @param {string} model @param {number} slot */
  isClaimed(model, slot) {
    return (this.claimed.get(`${model}:${slot}`) ?? 0) > 0;
  }

  /**
   * Mark a slot as taken by a request of this process until release() (the request reached the server).
   * @param {string} model @param {number} slot
   */
  claim(model, slot) {
    const k = `${model}:${slot}`;
    this.claimed.set(k, (this.claimed.get(k) ?? 0) + 1);
    let done = false;
    const release = () => {
      if (done) return;
      done = true;
      this.claimed.set(k, Math.max(0, (this.claimed.get(k) ?? 1) - 1));
    };
    setTimeout(release, 30_000).unref?.();
    return release;
  }

  /**
   * Prepare a request: the volatile parts moved, the slot filled and pinned. Returns the payload to send
   * and a function to call once the server has the request (it frees the slot for this process's others).
   * @param {Record<string, any>} payload @param {Meta} meta
   * @returns {Promise<{ payload: Record<string, any>, release: () => void }>}
   */
  async before(payload, meta) {
    const none = { payload, release: () => {} };
    const set = this.settings();
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
      if (known && known.epoch === m.epoch) {      // its own slot, unless another session of ours ran there since
        const s = slots.find((x) => x.id === known.slot);
        if (s && !s.busy && !this.isClaimed(model, s.id) && this.owner.get(`${model}:${s.id}`) === id) slot = s.id;
      }
      if (slot === undefined) {
        const pick = chooseSlot(slots.filter((s) => !this.isClaimed(model, s.id)));
        if (!pick) return moved;                         // every slot busy: llama.cpp decides
        slot = pick.id;
        // from the disk: a session new to this process (or to this server), or one whose slot another
        // session took since its last save; else llama.cpp finds it in its RAM cache
        if (!(known && known.epoch === m.epoch) || known.saved) {
          await this.fill(out, model, srv.router, m, pick, meta, set);
          how = "filled";
        } else {
          how = "the RAM cache's";
        }
      }
      const release = this.claim(model, slot);
      this.owner.set(`${model}:${slot}`, id);
      this.log(`request: ${meta.agent} ${meta.session ?? "-"} -> slot ${slot} (${how})`);
      if (meta.session) {
        this.sessions.set(id, { ...(known ?? {}), slot, epoch: m.epoch, model, router: srv.router, payload: out,
                                at: Date.now(), sub: Boolean(meta.sub) });
      }
      return { payload: { ...out, id_slot: slot }, release };
    } catch (e) {
      this.log(`before: ${e instanceof Error ? e.message : e}`);
      return moved;
    }
  }

  /**
   * Put the best saved state into the slot: the session's file, else the agent's prompt file (read once
   * and saved when there is none).
   * @param {Record<string, any>} payload @param {string} model @param {boolean} router @param {ModelState} m
   * @param {{ id: number, n: number }} pick @param {Meta} meta @param {{ prefix: boolean, sessions: boolean }} set
   */
  async fill(payload, model, router, m, pick, meta, set) {
    const key = await this.serverKey(model, router, m);
    const before = this.owner.get(`${model}:${pick.id}`);
    const other = before ? this.sessions.get(before) : undefined;
    if (other && other.epoch === m.epoch && set.sessions && pick.n >= MIN_SESSION_TOKENS) {
      // another session of ours is in the slot: saved first (set back to the end of its last prompt, so its
      // next prompt continues it), then put back from its file when it returns
      const [, session, agent] = before.split("\n");
      if (session && !other.sub && !NO_SAVE_AGENTS.has(agent)) await this.save(other, session, pick, m, true);
    }
    if (pick.n > 0) {          // the slot's conversation goes to the RAM cache first (llama.cpp does that for a new task)
      await this.call("POST", "/completion", { prompt: [0], n_predict: 0, cache_prompt: true, id_slot: pick.id,
                                               ...this.withModel(model, router) }, 60_000);
    }
    if (set.sessions && meta.session && !meta.sub && await this.restore(model, router, pick.id, sessionFile(model, key, meta.session))) {
      this.log(`session ${meta.session}: restored into slot ${pick.id}`);
      return;
    }
    if (!set.prefix) return;
    const pf = await this.prefix(payload, model, router, m, meta.agent);
    if (!pf) return;
    if (await this.restore(model, router, pick.id, pf.file)) {
      this.log(`${meta.agent}'s prompt (${pf.tokens.length} tokens) restored into slot ${pick.id}`);
      return;
    }
    if (this.diskFull()) return;
    const t0 = Date.now();
    await this.call("POST", "/completion", { prompt: pf.tokens, n_predict: 0, cache_prompt: true, id_slot: pick.id,
                                             ...this.withModel(model, router) }, 3_600_000);
    await this.call("POST", `/slots/${pick.id}?action=save`, { filename: pf.file, ...this.withModel(model, router) });
    this.log(`${meta.agent}'s prompt (${pf.tokens.length} tokens) read in ${((Date.now() - t0) / 1000).toFixed(1)} s and saved`);
    this.wrote(pf.file, pf.file.slice(0, pf.file.lastIndexOf("+") + 1));
  }

  /** @param {string} model @param {boolean} router @param {number} slot @param {string} file */
  async restore(model, router, slot, file) {
    if (this.local && !this.exists(file)) return false;
    try {
      await this.call("POST", `/slots/${slot}?action=restore`, { filename: file, ...this.withModel(model, router) }, 120_000);
      return true;
    } catch {
      return false;
    }
  }

  /** @param {string} file */
  exists(file) {
    try {
      fs.statSync(join(this.dir, file));
      return true;
    } catch {
      return !this.dirExists();           // the folder isn't here (the server is elsewhere): try it
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
   * @param {Record<string, any>} payload @param {string} model @param {boolean} router @param {ModelState} m @param {string} agent
   */
  async prefix(payload, model, router, m, agent) {
    const sys = payload.messages[0];
    if (sys?.role !== "system") return undefined;
    const fields = templateFields(payload);
    const id = shortHash(JSON.stringify([sys.content, fields]), 16);
    if (m.prefixes.has(id)) return m.prefixes.get(id) ?? undefined;
    const render = async (/** @type {string} */ text) => {
      const r = await this.call("POST", "/apply-template", { messages: [sys, { role: "user", content: text }], ...fields,
                                                            ...this.withModel(model, router) });
      return this.tokens(String(r?.prompt ?? ""), model, router);
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
    return Array.isArray(r?.tokens) ? r.tokens : [];
  }

  /**
   * Does the model's template re-render a reply as it was generated (then a slot saved after a reply
   * is a prefix of the next prompt)? Checked once per model and thinking mode.
   * @param {Record<string, any>} payload @param {string} model @param {boolean} router @param {ModelState} m
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
    const render = async (/** @type {any[]} */ messages) =>
      String((await this.call("POST", "/apply-template", { messages, ...fields, ...this.withModel(model, router) }))?.prompt ?? "");
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
    const set = this.settings();
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
      await this.save(e, meta.session, s, m, false);
    } catch (err) {
      this.log(`after: ${err instanceof Error ? err.message : err}`);
    }
  }

  /**
   * Save a session's slot to its file (unless it is saved as it is). settle: first set the slot back to
   * the end of the session's last prompt; else only when the template doesn't re-render replies as
   * generated (stable()).
   * @param {SessionState} e @param {string} session @param {{ id: number, n: number, task?: number }} s
   * @param {ModelState} m @param {boolean} settle
   */
  async save(e, session, s, m, settle) {
    const { model, router } = e;
    if (e.saved === `${s.task}:${s.n}`) return;
    if (this.diskFull()) {
      this.log(`session ${session} not saved: less than 10 GB free on the disk`);
      return;
    }
    if (settle || !(await this.stable(e.payload, model, router, m))) {
      const r = await this.call("POST", "/apply-template", { messages: e.payload.messages, ...templateFields(e.payload),
                                                            ...this.withModel(model, router) });
      const toks = await this.tokens(String(r?.prompt ?? ""), model, router);
      await this.call("POST", "/completion", { prompt: toks, n_predict: 0, cache_prompt: true, id_slot: e.slot,
                                               ...this.withModel(model, router) }, 3_600_000);
    }
    const now = (await this.slots(model, router)).find((x) => x.id === e.slot) ?? s;
    const file = sessionFile(model, await this.serverKey(model, router, m), session);
    const r = await this.call("POST", `/slots/${e.slot}?action=save`, { filename: file, ...this.withModel(model, router) });
    e.saved = `${now.task}:${now.n}`;
    this.log(`session ${session}: ${r?.n_saved ?? now.n} tokens saved from slot ${e.slot}${settle ? " (its slot is needed)" : ""}`);
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
    for (const n of gone) {
      try {
        fs.unlinkSync(join(this.dir, n));
      } catch { /* already gone */ }
    }
  }
}
