// @ts-check
// CARL: the hand-off to the coder in OpenCode (installed by CARL's client setup with the coder). The rules are in
// carl-delegation.js (shared with the Pi extension). Phase 23 (1.8.0):
//   - the delegation rule is for a session's main agent only. OpenCode gives its global instructions (CARL's
//     delegation.md among them) to every agent, subagents too; a coder that read "your first action is to delegate"
//     acted as the main session and did nothing (the Gemma 4 E4B, 2026-10-06). The rule is taken out of the system
//     prompt of every subagent session: the coder, explore, the browser agent, ...
//   - the reminder (option "reminder", on unless false): the main agent's user messages end with one line about the
//     coder;
//   - the gate: the dashboard's setting delegation.gate (GateSetting; option "cacheApi" on another computer).
// A session is a subagent's when OpenCode created it with a parent (its events, else the session API). When that is
// not known, the session counts as a main one: a main agent never loses its rule.
// Phase 23.4.3 (carl-brief.js, next to carl-delegation.js):
//   - the brief check (option "brief", on unless false): a task call for the coder whose TOML brief fails the check
//     is refused before the coder starts (thrown in tool.execute.before, as the gate does); a call with task_id
//     (it continues an earlier task) is not checked; a JSON brief of the same structure is read and checked too
//     (option "briefFormat": "json" names JSON in the refusal of a task that has no brief: agent-bench's brief_json);
//   - the coder's gates: chat.message names the agent of each user message; the first message of a coder session
//     is its brief, kept for that session (a later one replaces it only when it is a complete brief too); the
//     coder's writes outside the brief's files, or against its mode, are refused in tool.execute.before;
//   - the chain (option "chain", on unless false; carl-chain.js): a task call for the coder whose brief has mode code
//     and tests = "new" becomes the test session (tool.execute.before rewrites its prompt to the test brief). When
//     the test session ends, the plugin starts the code session itself through the SDK (a child session of the main
//     session, agent coder, the brief and CARL's [test_session]) and waits for it (its session.idle event, or its
//     messages: the last assistant message completed). With tests = "existing": the named test files are hashed
//     before and after, and the result says what changed.
//     THE BASE (any OpenCode version; only the documented hooks and the SDK): the task runs in the foreground (the
//     plugin sets background: false on the rewritten task), and tool.execute.after replaces the task's output with
//     the one result.
//     THE BACKGROUND HOLD (a layer, a workaround until OpenCode has a supported hook to hold a task's completion):
//     the test session's completion message (OpenCode's <task id=... state="completed"> user message to the main
//     session) is held back in chat.message (a throw: OpenCode 1.18 then neither saves it nor starts a turn), the
//     code session runs, and one combined completion is sent with session.promptAsync. Only when (holdGate) the
//     OpenCode version is one of VERIFIED_VERSIONS (openCodeVersion: the SDK's global.health() where the client has
//     it, else the package.json of the running program's npm package, else `<program> --version`;
//     CARL_TEST_OPENCODE_VERSION replaces it, for tests) and the self-check at load passes
//     (selfCheck: the SDK has session.create, session.promptAsync, session.messages and session.get; nothing is
//     sent). Else the base, and once a notice (OpenCode's log, service carl-delegation, and a toast in the TUI).
//     A held chain is recorded in ~/.config/carl/chains/<main session>.json (ChainStore, mode 0600): its stage
//     (test, held, code), the code session's id, the chain's state. When OpenCode starts again and a chain of this
//     project was not delivered (its OpenCode process is gone), the plugin delivers what exists: the one result
//     when the code session finished, else the test session's report and a sentence that the code session did not
//     finish. A delivered chain's record is removed.
// Phase 23.4.4 (thinking per role): the coder's thinking follows the model it runs on. Option "coderThinking":
// { "<provider>/<model>": reasoningEffort } (the dashboard's Coder thinking, written by CARL's client setup). The
// chat.params hook sets output.options.reasoningEffort on each request of the coder agent (its test and code
// sessions of the chain too) whose model has an entry; OpenCode sends it as reasoning_effort (checked with OpenCode
// 1.18.34 against agent-bench's fake server, 2026-10-09). A model without an entry ("same as main", the default):
// the request stays as it is, so the model entry's own reasoningEffort (the main session's thinking) applies.

import { execFile } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { basename, dirname, join } from "node:path";
import { parseBrief, checkBrief } from "./carl-brief.js";
import { Chain, Watch, coderPlan } from "./carl-chain.js";
import { CoderGate, GateSetting, RULE_BEGIN, RULE_END, Turn, briefCheck, isCoderName, withReminder, withoutRule }
  from "./carl-delegation.js";

export { RULE_BEGIN, RULE_END, withoutRule };

export const HOLD_MARK = "[CARL] Held";
/** The OpenCode versions on which the background hold was checked end to end (the <task …> message form; a throw in
 * chat.message drops the message and starts no turn). Another version: the foreground chain. */
export const VERIFIED_VERSIONS = ["1.18.34", "1.18.35"];
const POLL_MS = 5_000;                                       // the code session's messages, besides session.idle
const RECOVER_MS = 2_000;                                    // after the load: the undelivered chains of this project

/** @typedef {{ chain?: Chain, watch?: Watch, parent: string, description: string, hold?: boolean, test?: string,
 *              code?: string, agent?: string, model?: { providerID?: string, modelID?: string } }} Pending */
/** @typedef {{ ok: boolean, why: string }} Hold */

/**
 * OpenCode's version: CARL_TEST_OPENCODE_VERSION (for tests), else the SDK's global.health() where the plugin's
 * client has it (GET /global/health: { healthy, version }; 1.18's plugin client has no health method), else the
 * package.json of the npm package that holds the running program (opencode-ai/bin/opencode.exe or
 * opencode-<os>-<cpu>/bin/opencode), else `<the running program> --version` when the program is OpenCode's
 * (its name starts with "opencode"; once, 5 s limit). "" when none says.
 * @param {any} client @param {number} [ms] the time limit of each way that waits @returns {Promise<string>}
 */
export async function openCodeVersion(client, ms = 5_000) {
  const forced = process.env.CARL_TEST_OPENCODE_VERSION;
  if (forced) return forced;
  const semver = (/** @type {unknown} */ v) => {
    const m = /^\s*v?(\d+\.\d+\.\d+(?:[-+][\w.-]+)?)\s*$/.exec(String(v ?? ""));
    return m ? m[1] : "";
  };
  /** @param {Promise<unknown>} p @returns {Promise<unknown>} */
  const limit = (p) => {
    /** @type {ReturnType<typeof setTimeout> | undefined} */
    let timer;
    return Promise.race([p, new Promise((done) => { timer = setTimeout(() => done(null), ms); })])
      .finally(() => clearTimeout(timer));
  };
  try {
    if (typeof client?.global?.health === "function") {
      const r = /** @type {any} */ (await limit(client.global.health()));
      const v = semver((r?.data ?? r)?.version);
      if (v) return v;
    }
  } catch { /* the next way */ }
  const exe = process.execPath;
  if (!/^opencode/i.test(basename(exe))) return "";
  try {
    const pkg = JSON.parse(readFileSync(join(dirname(dirname(exe)), "package.json"), "utf8"));
    const v = /^opencode/.test(String(pkg?.name)) ? semver(pkg.version) : "";
    if (v) return v;
  } catch { /* no package.json */ }
  const out = await limit(new Promise((done) => {
    try {
      execFile(exe, ["--version"], { timeout: ms, env: { ...process.env, NO_COLOR: "1" } }, (err, stdout) => done(err ? "" : stdout));
    } catch {
      done("");
    }
  }));
  return semver(out);
}

/** The self-check at load: the SDK methods that the hold needs, missing ones by name. Nothing is called.
 * @param {any} client @returns {string[]} */
export function selfCheck(client) {
  return ["session.create", "session.promptAsync", "session.messages", "session.get"].filter((n) => {
    const [a, b] = n.split(".");
    return typeof client?.[a]?.[b] !== "function";
  });
}

/** Whether the background hold is on: a verified version and a self-check that passed.
 * @param {string} version @param {string[]} missing @returns {Hold} */
export function holdGate(version, missing) {
  if (missing.length) return { ok: false, why: `the OpenCode SDK lacks ${missing.join(", ")}` };
  if (!version) return { ok: false, why: "CARL could not read the OpenCode version" };
  if (!VERIFIED_VERSIONS.includes(version)) {
    return { ok: false, why: `OpenCode ${version} is not a version that CARL checked the background hold on (${VERIFIED_VERSIONS.join(", ")})` };
  }
  return { ok: true, why: `OpenCode ${version}` };
}

/**
 * The coder's reasoningEffort for a request (the chat.params hook), or undefined: not the coder, or its model has
 * no entry in the table (the coder thinks as the main session).
 * @param {unknown} table the option coderThinking @param {string} agent @param {string} coder CARL's coder name
 * @param {{ providerID?: unknown, id?: unknown } | undefined} model @returns {string | undefined}
 */
export function coderEffort(table, agent, coder, model) {
  if (!agent || (agent !== coder && !isCoderName(agent)) || typeof table !== "object" || table === null) return undefined;
  const v = /** @type {Record<string, unknown>} */ (table)[`${String(model?.providerID ?? "")}/${String(model?.id ?? "")}`];
  return typeof v === "string" && v ? v : undefined;
}

/** A process still runs (the OpenCode that holds a chain). @param {unknown} pid */
function alive(pid) {
  if (typeof pid !== "number" || !Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (e) {
    return /** @type {any} */ (e)?.code === "EPERM";
  }
}

/**
 * The held chains, one file for each main session: DIR/<main session>.json, { chains: [record, ...] }, mode 0600
 * (the folder 0700). A record: v, parent, test (the test session), stage ("test": it runs; "held": its completion
 * was held, the code session did not start yet; "code": the code session runs), code (its id), description, agent
 * (the main session's), directory (the project), pid (the OpenCode process), at, chain (Chain.snapshot()).
 */
export class ChainStore {
  /** @param {string} dir */
  constructor(dir) {
    this.dir = dir;
  }

  /** @param {string} parent */
  file(parent) {
    return /^[A-Za-z0-9_-]{1,200}$/.test(String(parent)) ? join(this.dir, `${parent}.json`) : "";
  }

  /** @param {string} parent @returns {any[]} */
  read(parent) {
    const f = this.file(parent);
    if (!f) return [];
    try {
      const o = JSON.parse(readFileSync(f, "utf8"));
      return Array.isArray(o?.chains) ? o.chains : [];
    } catch {
      return [];
    }
  }

  /** @param {string} parent @param {any[]} list */
  write(parent, list) {
    const f = this.file(parent);
    if (!f) return;
    try {
      if (!list.length) {
        rmSync(f, { force: true });
        return;
      }
      mkdirSync(this.dir, { recursive: true, mode: 0o700 });
      const tmp = `${f}.${process.pid}.tmp`;
      writeFileSync(tmp, JSON.stringify({ chains: list }, null, 1) + "\n", { mode: 0o600 });
      chmodSync(tmp, 0o600);
      renameSync(tmp, f);
    } catch { /* the chain still runs; only a restart could not deliver it */ }
  }

  /** Add or replace a record (by its test session). @param {any} rec */
  put(rec) {
    this.write(rec.parent, [...this.read(rec.parent).filter((r) => r?.test !== rec.test), rec]);
  }

  /** @param {string} parent @param {string} test */
  drop(parent, test) {
    this.write(parent, this.read(parent).filter((r) => r?.test !== test));
  }

  /** Every record. @returns {any[]} */
  all() {
    let names = [];
    try {
      names = readdirSync(this.dir).filter((n) => n.endsWith(".json"));
    } catch {
      return [];
    }
    return names.flatMap((n) => this.read(n.slice(0, -5)));
  }

  /** One process delivers a record: DIR/<parent>.<test>.claim holds its pid. @param {any} rec @returns {boolean} */
  claim(rec) {
    const f = join(this.dir, `${rec.parent}.${rec.test}.claim`);
    try {
      writeFileSync(f, String(process.pid), { flag: "wx", mode: 0o600 });
      return true;
    } catch {
      try {
        if (alive(Number(readFileSync(f, "utf8")))) return false;
        writeFileSync(f, String(process.pid), { mode: 0o600 });
        return true;
      } catch {
        return false;
      }
    }
  }

  /** @param {any} rec */
  release(rec) {
    try {
      rmSync(join(this.dir, `${rec.parent}.${rec.test}.claim`), { force: true });
    } catch { /* gone */ }
  }
}

/** The end of a session from its messages: finished (the last message is an assistant message that completed, not
 * for tool calls, or failed), ok, and its last answer (text, or the error). @param {any[]} list */
export function sessionEnd(list) {
  const last = [...list].reverse().find((m) => m?.info?.role === "assistant");
  const info = list[list.length - 1]?.info;
  const finished = info?.role === "assistant"
    && (Boolean(info.error) || (Boolean(info.time?.completed) && !/tool/.test(String(info.finish ?? ""))));
  const err = last?.info?.error;
  const texts = (last?.parts ?? []).filter((/** @type {any} */ p) => p?.type === "text" && p.text);
  const text = String(texts[texts.length - 1]?.text ?? "");
  if (err) return { finished, ok: false, output: String(err?.data?.message ?? err?.name ?? "the session failed") + (text ? `\n\n${text}` : "") };
  return { finished, ok: Boolean(last), output: text, none: !last };
}

/**
 * A task tool result as OpenCode writes it (OpenCode 1.18: Ur in the task tool):
 *   <task id="ses_..." state="completed">
 *   <summary>Background task completed: ...</summary>     (a background result only)
 *   <task_result>
 *   the subagent's final text
 *   </task_result>
 *   </task>
 * @param {{ id: string, state: string, summary?: string, text: string }} t
 */
export function taskXml(t) {
  const tag = t.state === "error" ? "task_error" : "task_result";
  return [`<task id="${t.id}" state="${t.state}">`, ...(t.summary ? [`<summary>${t.summary}</summary>`] : []),
          `<${tag}>`, t.text, `</${tag}>`, "</task>"].join("\n");
}

/** The parts of a task tool result (null: not one). @param {string} text
 * @returns {{ id: string, state: string, summary: string, text: string } | null} */
export function parseTaskXml(text) {
  const m = /^\s*<task id="([^"]*)" state="([^"]*)">\n(?:<summary>([\s\S]*?)<\/summary>\n)?<(task_result|task_error)>\n?([\s\S]*?)\n?<\/\4>\n<\/task>\s*$/.exec(String(text ?? ""));
  return m ? { id: m[1], state: m[2], summary: m[3] ?? "", text: m[5] } : null;
}

/** @type {import("@opencode-ai/plugin").PluginModule & { id: string }} */
export default {
  id: "carl-delegation",
  server: async (ctx, options) => {
    const opts = /** @type {Record<string, unknown>} */ (options ?? {});
    const reminder = opts.reminder !== false;
    const briefs = opts.brief !== false;
    const gateSetting = new GateSetting({ cacheApi: typeof opts.cacheApi === "string" ? opts.cacheApi : "" });
    const coder = typeof opts.coder === "string" && opts.coder ? opts.coder : "coder";
    const chains = opts.chain !== false;
    // the brief's format in the main agent's instructions, named when a task has no brief at all (agent-bench's
    // brief_json variant sets "json"; a JSON brief is read either way)
    const briefFormat = opts.briefFormat === "json" ? "json" : "toml";
    const cwd = String(/** @type {any} */ (ctx)?.directory ?? process.cwd());
    const client = /** @type {any} */ (ctx)?.client;
    /** @type {Map<string, Pending>} a coder task call (its callID) with a chain or a watch */
    const calls = new Map();
    /** @type {Map<string, Pending>} a background test session (its id) whose completion message is due */
    const waiting = new Map();
    /** @type {Map<string, Array<() => void>>} session id -> what waits for its session.idle */
    const idlers = new Map();
    /** @type {Map<string, string>} session id -> its last error (session.error) */
    const errors = new Map();
    /** @type {Map<string, boolean>} session id -> is a subagent session (has a parent) */
    const sub = new Map();
    /** @type {Map<string, Turn>} */
    const turns = new Map();
    /** @type {Map<string, CoderGate>} a coder session's gates, from its brief */
    const gates = new Map();
    /** @param {string} id */
    const isSub = async (id) => {
      const known = sub.get(id);
      if (known !== undefined) return known;
      try {
        const r = /** @type {any} */ (await /** @type {any} */ (ctx)?.client?.session?.get({ path: { id } }));
        const parent = Boolean((r?.data ?? r)?.parentID);
        sub.set(id, parent);
        return parent;
      } catch {
        return false;                                          // unknown: the main agent's case
      }
    };
    /** @param {string} id */
    const turn = (id) => {
      let t = turns.get(id);
      if (!t) turns.set(id, (t = new Turn()));
      return t;
    };

    // ---------------------------------------------------------------- the chain
    const store = new ChainStore(join(homedir(), ".config", "carl", "chains"));
    /** @type {Map<string, string>} a main session's agent (its last user message's) */
    const agents = new Map();
    /** The background hold: the self-check at load and the version (not awaited here: the first chain waits). */
    /** @type {Promise<Hold>} */
    const holdReady = !chains ? Promise.resolve({ ok: false, why: "the chain is off" }) : (async () => {
      const missing = selfCheck(client);
      return holdGate(missing.length ? "" : await openCodeVersion(client), missing);
    })().catch((e) => ({ ok: false, why: `the self-check failed: ${e instanceof Error ? e.message : String(e)}` }));
    let noticed = false;
    /** Once: the chain runs in the foreground (the hold is off). @param {string} why */
    const notice = (why) => {
      if (noticed) return;
      noticed = true;
      const message = `CARL runs the coder's tests-then-code chain in the foreground (the background hold is off: ${why}). The main session waits for both sessions.`;
      try {
        void client?.app?.log?.({ body: { service: "carl-delegation", level: "warn", message } })?.catch?.(() => {});
        void client?.tui?.showToast?.({ body: { title: "CARL", message, variant: "info", duration: 12000 } })?.catch?.(() => {});
      } catch { /* no log, no toast */ }
    };
    /** A session's messages. @param {string} id @returns {Promise<any[]>} */
    const messages = async (id) => {
      const r = await client.session.messages({ path: { id } });
      return /** @type {any[]} */ (Array.isArray(r) ? r : r?.data ?? []);
    };
    /** The last assistant message of a session: its text, or its error. @param {string} id */
    const lastAnswer = async (id) => {
      const end = sessionEnd(await messages(id));
      if (end.none) return { ok: false, output: errors.get(id) || "the code session gave no answer" };
      return { ok: end.ok, output: end.output };
    };
    /**
     * The end of a session that was started: its session.idle event, or its messages say it finished (checked every
     * POLL_MS, so the chain does not depend on the event alone). Register before the prompt; stop() when the prompt
     * failed.
     * @param {string} id @returns {{ done: Promise<void>, stop: () => void }}
     */
    const ended = (id) => {
      let stop = () => {};
      const done = new Promise((resolve) => {
        let over = false;
        const end = () => {
          if (over) return;
          over = true;
          clearInterval(timer);
          resolve(undefined);
        };
        stop = end;
        const list = idlers.get(id) ?? [];
        list.push(end);
        idlers.set(id, list);
        const timer = setInterval(() => {
          messages(id).then((m) => sessionEnd(m).finished && end(), () => {});
        }, POLL_MS);
        /** @type {any} */ (timer).unref?.();
      });
      return { done: /** @type {Promise<void>} */ (done), stop };
    };
    /** A held chain's record in the state file. @param {Pending} p @param {"test" | "held" | "code"} stage */
    const record = (p, stage) => {
      if (!p.chain || !p.test) return;
      store.put({ v: 1, parent: p.parent, test: p.test, stage, ...(p.code ? { code: p.code } : {}), description: p.description,
                  agent: p.agent ?? "", directory: cwd, pid: process.pid, at: Date.now(), chain: p.chain.snapshot() });
    };
    /**
     * The code session: a child session of the main session, as OpenCode's task tool makes one (the coder agent; no
     * task or todo tool; the main session's deny and external-directory rules), with the code session's brief.
     * @param {Pending} p @param {string} task
     * @returns {Promise<{ id: string, ok: boolean, output: string }>}
     */
    const codeSession = async (p, task) => {
      try {
        /** @type {any[]} */
        let permission = [];
        try {
          const parent = await client.session.get({ path: { id: p.parent } });
          permission = ((parent?.data ?? parent)?.permission ?? [])
            .filter((/** @type {any} */ r) => r?.permission === "external_directory" || r?.action === "deny");
        } catch { /* the main session's rules: not known */ }
        permission.push({ permission: "todowrite", pattern: "*", action: "deny" }, { permission: "task", pattern: "*", action: "deny" });
        const made = await client.session.create({ body: { parentID: p.parent, title: `${p.description}: code (@${coder} subagent)`,
                                                           agent: coder, permission } });
        const id = String((made?.data ?? made)?.id ?? "");
        if (!id) return { id: "", ok: false, output: `CARL could not start the code session: ${JSON.stringify(made?.error ?? made)}` };
        sub.set(id, true);
        if (p.hold) {
          p.code = id;
          record(p, "code");
        }
        const wait = ended(id);
        /** @type {Record<string, unknown>} */
        const body = { agent: coder, parts: [{ type: "text", text: task }] };
        if (p.model?.providerID && p.model?.modelID) body.model = { providerID: p.model.providerID, modelID: p.model.modelID };
        let sent;
        try {
          sent = await client.session.promptAsync({ path: { id }, body });
        } catch (e) {
          wait.stop();
          throw e;
        }
        if (sent?.error) {
          wait.stop();
          return { id, ok: false, output: `CARL could not start the code session: ${JSON.stringify(sent.error)}` };
        }
        await wait.done;
        return { id, ...(await lastAnswer(id)) };
      } catch (e) {
        return { id: "", ok: false, output: `CARL could not run the code session: ${e instanceof Error ? e.message : String(e)}` };
      }
    };
    /**
     * After the test session (or the watched session): the one result. For a chain: the red start, the freeze, the
     * code session, the result text.
     * @param {Pending} p @param {{ id: string, state: string, text: string }} res
     * @returns {Promise<{ id: string, state: string, text: string }>}
     */
    const finish = async (p, res) => {
      if (p.watch) {
        const note = p.watch.note();
        return { id: res.id, state: res.state, text: note ? `${res.text}\n\n${note}` : res.text };
      }
      const chain = /** @type {Chain} */ (p.chain);
      if (res.state !== "completed") {
        chain.testOutput = res.text;
        return { id: res.id, state: "error", text: chain.stopped() };
      }
      const task = await chain.afterTest({ ok: true, output: res.text });
      const code = await codeSession(p, task);
      return { id: code.id || res.id, state: code.ok ? "completed" : "error", text: chain.result(code) };
    };
    /** A completion message to the main session, as OpenCode's own for a background task.
     * @param {string} parent @param {string} agent @param {string} description
     * @param {{ id: string, state: string, text: string }} out */
    const send = async (parent, agent, description, out) => {
      const ok = out.state === "completed";
      const text = taskXml({ id: out.id, state: ok ? "completed" : "error", text: out.text,
                             summary: `Background task ${ok ? "completed" : "failed"}: ${description}` });
      await client.session.promptAsync({ path: { id: parent },
                                         body: { ...(agent ? { agent } : {}), parts: [{ type: "text", synthetic: true, text }] } });
    };
    /** A held chain: the code session, then its one result to the main session as a message; the record goes when
     * the result was sent (else the next start delivers it).
     * @param {Pending} p @param {{ id: string, state: string, text: string }} res @param {string} agent */
    const deliver = async (p, res, agent) => {
      p.agent = agent;
      /** @type {Chain} */ (p.chain).testOutput = res.text;
      record(p, "held");                                       // before the first await: the throw follows
      /** @type {{ id: string, state: string, text: string }} */
      let out;
      try {
        out = await finish(p, res);
      } catch (e) {                                            // never leave the main agent without a result
        out = { id: res.id, state: "error", text: `${res.text}\n\n[CARL] The chain stopped after the test session: ${e instanceof Error ? e.message : String(e)}` };
      }
      try {
        await send(p.parent, agent, p.description, out);
        if (p.test) store.drop(p.parent, p.test);
      } catch { /* OpenCode stopped: the record stays, the next start delivers */ }
    };
    /** At load (after RECOVER_MS): the held chains of this project whose OpenCode is gone get what exists. */
    const recover = async () => {
      if (typeof client?.session?.messages !== "function" || typeof client?.session?.promptAsync !== "function") return;
      for (const r of store.all()) {
        if (!r || r.directory !== cwd || !r.parent || !r.test || !r.chain) continue;
        if (r.pid === process.pid || alive(r.pid) || !store.claim(r)) continue;
        try {
          const chain = Chain.from(r.chain);
          /** @type {{ id: string, state: string, text: string }} */
          let out;
          if (r.stage === "code" && r.code) {
            const end = sessionEnd(await messages(r.code).catch(() => []));
            out = end.finished
              ? { id: r.code, state: end.ok ? "completed" : "error", text: chain.result(end) }
              : { id: r.code, state: "error", text: chain.interrupted("code", end.output) };
          } else if (r.stage === "held") {
            out = { id: r.test, state: "error", text: chain.interrupted("held") };
          } else {
            const end = sessionEnd(await messages(r.test).catch(() => []));
            chain.testOutput = end.output;
            out = { id: r.test, state: "error", text: chain.interrupted(end.finished && end.ok ? "held" : "test") };
          }
          await send(r.parent, String(r.agent ?? ""), String(r.description ?? "coder task"), out);
          store.drop(r.parent, r.test);
        } catch { /* it stays for the next start */ } finally {
          store.release(r);
        }
      }
    };
    if (client) {                                             // also with the chain off now: a record is delivered
      const t = setTimeout(() => void recover().catch(() => {}), typeof opts.recoverMs === "number" ? opts.recoverMs : RECOVER_MS);
      /** @type {any} */ (t).unref?.();
    }

    return {
      // the coder's thinking for the model it runs on (Phase 23.4.4): only for the coder, only with an entry
      "chat.params": async (input, output) => {
        const effort = coderEffort(opts.coderThinking, String(input?.agent ?? ""), coder, /** @type {any} */ (input)?.model);
        if (effort && output && typeof output.options === "object" && output.options !== null) {
          output.options.reasoningEffort = effort;
        }
      },
      event: async ({ event }) => {
        const props = /** @type {any} */ (event)?.properties;
        const info = props?.info;
        if ((event?.type === "session.created" || event?.type === "session.updated") && info?.id) {
          sub.set(String(info.id), Boolean(info.parentID));
        }
        if (event?.type === "session.deleted" && info?.id) gates.delete(String(info.id));
        if (event?.type === "session.error" && props?.sessionID) {
          errors.set(String(props.sessionID), String(props.error?.data?.message ?? props.error?.name ?? "error"));
        }
        if ((event?.type === "session.idle" || (event?.type === "session.status" && props?.status?.type === "idle"))
            && props?.sessionID) {
          const id = String(props.sessionID);
          const list = idlers.get(id);
          idlers.delete(id);
          for (const done of list ?? []) done();
        }
      },
      // in place: OpenCode reads its own array after the hook (a new array would be ignored)
      "experimental.chat.system.transform": async (input, output) => {
        if (!input?.sessionID || !Array.isArray(output?.system) || !(await isSub(input.sessionID))) return;
        output.system.forEach((s, i) => {
          if (typeof s === "string") output.system[i] = withoutRule(s);
        });
      },
      // the reminder at the end of every user message of a main session (the same text each time)
      "experimental.chat.messages.transform": async (_input, output) => {
        if (!reminder) return;
        for (const m of output?.messages ?? []) {
          const info = /** @type {any} */ (m.info);
          if (info?.role !== "user" || !info.sessionID || (await isSub(String(info.sessionID)))) continue;
          const texts = (m.parts ?? []).filter((/** @type {any} */ p) => p?.type === "text" && !p.synthetic);
          const last = /** @type {any} */ (texts[texts.length - 1]);
          if (last) last.text = withReminder(String(last.text ?? ""), "opencode", coder);
        }
      },
      "chat.message": async (input, output) => {
        if (!input?.sessionID) return;
        // a background test session's completion message: held back, the code session runs, one result follows
        for (const part of /** @type {any[]} */ (output?.parts ?? [])) {
          if (part?.type !== "text") continue;
          const res = parseTaskXml(String(part.text ?? ""));
          const p = res ? waiting.get(res.id) : undefined;
          if (!res || !p || p.parent !== input.sessionID) continue;
          waiting.delete(res.id);
          if (p.chain && !p.hold && res.state === "completed") {  // in the background without the hold: no code session
            part.text = taskXml({ ...res, text: p.chain.unheld(res.text) });
            break;
          }
          if (p.watch || res.state !== "completed") {          // in place: the one result is this message
            if (p.test) store.drop(p.parent, p.test);
            const out = await finish(p, res);
            part.text = taskXml({ ...out, summary: res.summary });
            break;
          }
          void deliver(p, res, String(input.agent ?? ""));
          throw new Error(`${HOLD_MARK}: the test session of "${p.description}" ended; CARL runs its code session now and sends one result when it ends.`);
        }
        turns.get(input.sessionID)?.reset();
        const agent = String(input.agent || /** @type {any} */ (output)?.message?.agent || "");
        if (agent) agents.set(input.sessionID, agent);
        if (agent !== coder && !isCoderName(agent)) return;
        const text = (/** @type {any[]} */ (output?.parts ?? [])).filter((p) => p?.type === "text")
          .map((p) => String(p.text ?? "")).join("\n");
        const { brief } = parseBrief(text);
        if (brief && !checkBrief(brief).length) gates.set(input.sessionID, new CoderGate(brief, cwd));
      },
      "tool.execute.before": async (input, output) => {
        const tool = String(input?.tool ?? "");
        const args = /** @type {Record<string, unknown>} */ (output?.args ?? {});
        if (briefs) {
          const why = briefCheck(tool, args, { format: briefFormat });   // a coder task with an incomplete brief
          if (why) throw new Error(why);
        }
        const mine = input?.sessionID ? gates.get(input.sessionID) : undefined;
        if (mine) {                                                // the coder's own session: its brief's gates
          const why = mine.before(tool, args);
          if (why) throw new Error(why);
          return;
        }
        const type = String(args.subagent_type ?? "");
        if (tool === "task" && (type === coder || isCoderName(type)) && input?.callID && input?.sessionID) {
          const plan = coderPlan(String(args.prompt ?? ""), Boolean(args.task_id));
          const description = String(args.description ?? "") || "coder task";
          if (plan.kind === "chain" && chains) {                    // the task tool runs the test session
            const hold = await holdReady;
            const chain = new Chain(plan.brief, cwd, { format: plan.format });
            args.prompt = chain.testTask();
            args.description = `${description}: tests`;
            if (!hold.ok) {                                         // the base: the foreground, on any version
              if (args.background !== false) notice(hold.why);
              args.background = false;
            }
            calls.set(input.callID, { chain, parent: input.sessionID, description, hold: hold.ok,
                                      agent: agents.get(input.sessionID) ?? "" });
          } else if (plan.kind === "watch") {
            calls.set(input.callID, { watch: new Watch(plan.brief, cwd), parent: input.sessionID, description });
          }
        }
        const gate = await gateSetting.get();                     // the dashboard's setting (Connect > Setup)
        if (!gate || !input?.sessionID || (await isSub(input.sessionID))) return;
        const why = turn(input.sessionID).before(gate, "opencode", tool, args, cwd, existsSync, coder);
        if (why) throw new Error(why);
      },
      // a coder task with a chain or a watch: foreground, its output becomes the one result; background, its
      // completion message is due in chat.message
      "tool.execute.after": async (input, output) => {
        const p = input?.callID ? calls.get(input.callID) : undefined;
        if (!p) return;
        calls.delete(input.callID);
        const meta = /** @type {any} */ (output?.metadata ?? {});
        p.model = meta.model;
        const res = parseTaskXml(String(output?.output ?? ""));
        const id = String(meta.sessionId ?? res?.id ?? "");
        if (meta.background === true || res?.state === "running") {
          if (!id) return;
          waiting.set(id, p);
          if (p.chain && p.hold) {                                 // the held chain's record (the state file)
            p.test = id;
            record(p, "test");
          }
          return;
        }
        if (!res) return;
        output.output = taskXml(await finish(p, res));
      },
    };
  },
};
