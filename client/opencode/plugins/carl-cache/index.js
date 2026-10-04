// @ts-check
// OpenCode server plugin (installed by CARL's client/install.sh): CARL's prompt cache (carl-cache.js) for
// the requests OpenCode sends to CARL's provider. Each session's conversation is saved on the server's
// disk after its turn and restored before its next request when the server no longer holds it (after a
// restart, a model switch, or many other sessions); each agent's prompt is read once and saved.
//
// How: chat.headers marks our provider's requests with the session, the agent and whether it is a
// subagent; a wrapper around fetch (the AI SDK calls the global fetch) reads those marks, takes them off
// and lets carl-cache.js prepare the request body; when a reply that ends the turn has streamed, the
// session is saved before OpenCode sees the end.
// Options: { provider: "<our provider id>", cacheApi: "<the dashboard's cache API>" } (configure.py).
// Export nothing else from this file: older OpenCode versions call every export of the entry module as
// a plugin function.
import { CarlCache, debugLog, errorText, obj, splitOpenCode } from "./carl-cache.js";
import { checkOnce, watchApplied } from "./carl-panel.js";

/** @typedef {import("./carl-cache.js").Meta} Meta */
/**
 * One per OpenCode process (the plugin can load more than once): the fetch before the wrapper, a cache
 * for each server, the dashboard's cache API.
 * @typedef {{ original: typeof fetch, caches: Map<string, CarlCache>, cacheApi?: string }} Shared
 */

const MARK = "x-carl-cache";
const STATE = Symbol.for("carl-cache.opencode");
const CHAT = "/chat/completions";
/** globalThis with the shared state. */
const global = /** @type {{ [STATE]?: Shared }} */ (/** @type {unknown} */ (globalThis));

/**
 * The marks of a request (the chat.headers hook wrote them), or undefined.
 * @param {string} mark
 * @returns {Meta | undefined}
 */
function parseMark(mark) {
  const m = obj(JSON.parse(mark));
  return typeof m.session === "string" && typeof m.agent === "string"
    ? { session: m.session, agent: m.agent, sub: m.sub === true } : undefined;
}

/** Is the session a subagent's (it has a parent)? @param {unknown} res the SDK's answer, or the session */
function hasParent(res) {
  const r = obj(res);
  return Boolean(obj(r.data ?? r).parentID);
}

/**
 * The response with its stream passed through; when it ends with a reply that ends the turn (not a tool
 * call), `save` runs before the end reaches OpenCode (so the save is done before `opencode run` exits).
 * @param {Response} res @param {() => Promise<void>} save
 */
function saveAtTheEnd(res, save) {
  const dec = new TextDecoder();
  let tail = "";
  let final = false;
  const scan = new TransformStream({
    transform(chunk, ctl) {
      tail = (tail + dec.decode(chunk, { stream: true })).slice(-4096);
      const m = [...tail.matchAll(/"finish_reason"\s*:\s*"([a-z_]+)"/g)].pop();
      if (m) final = m[1] !== "tool_calls";
      ctl.enqueue(chunk);
    },
    async flush() {
      if (final) await save().catch((e) => debugLog(`save after the reply: ${errorText(e)}`));
    },
  });
  return new Response(/** @type {ReadableStream} */ (res.body).pipeThrough(scan),
                      { status: res.status, statusText: res.statusText, headers: res.headers });
}

/** @type {import("@opencode-ai/plugin").PluginModule & { id: string }} */
export default {
  id: "carl-cache",
  server: async ({ client }, options) => {
    const ours = typeof options?.provider === "string" ? options.provider : "llamacpp";
    checkOnce();                  // without the sync service: a config pushed from the server, applied once
    watchApplied((v) => {         // OpenCode reads its config when it starts
      const message = `CARL: the server's client config ${v} was applied (models, windows): restart OpenCode to use it.`;
      void client.app.log({ body: { service: "carl-cache", level: "info", message } }).catch(() => {});
      void client.tui.showToast({ body: { title: "CARL", message, variant: "info", duration: 15000 } }).catch(() => {});
    });
    const log = (/** @type {string} */ message) =>
      client.app.log({ body: { service: "carl-cache", level: "info", message } }).catch(() => {});
    /** @type {Shared} */
    const g = global[STATE] ?? { original: globalThis.fetch, caches: new Map() };
    if (typeof options?.cacheApi === "string") g.cacheApi = options.cacheApi;
    if (!global[STATE]) {
      global[STATE] = g;
      globalThis.fetch = /** @type {typeof fetch} */ (async (input, init) => {
        const headers = new Headers(init?.headers ?? undefined);
        const mark = headers.get(MARK);
        if (!mark || typeof init?.body !== "string" || !(typeof input === "string" || input instanceof URL)) {
          return g.original(input, init);
        }
        headers.delete(MARK);
        const url = String(input);
        const at = url.lastIndexOf(CHAT);
        if (at < 0) return g.original(input, { ...init, headers });   // not a chat request: as it is, without the mark
        const base = url.slice(0, at);
        let release = () => {};
        let body = init.body;
        /** @type {Meta | undefined} */
        let meta;
        /** @type {CarlCache | undefined} */
        let cache;
        try {
          meta = parseMark(mark);
          cache = meta && g.caches.get(base);
          if (meta && !cache) {
            const key = (headers.get("authorization") ?? "").replace(/^Bearer\s+/i, "") || undefined;
            cache = new CarlCache({ baseURL: base, apiKey: key, fetch: g.original, split: splitOpenCode, log,
                                    cacheApi: g.cacheApi });
            g.caches.set(base, cache);
          }
          if (meta && cache) {
            const r = await cache.before(JSON.parse(body), meta);
            body = JSON.stringify(r.payload);
            release = r.release;
          }
        } catch (e) {
          debugLog(`request: ${errorText(e)} (it goes as it is)`);   // never in the way of a request
        }
        let res;
        try {
          res = await g.original(input, { ...init, headers, body });
        } finally {
          release();
        }
        const done = meta;
        const c = cache;
        return done && c && res.ok && res.body ? saveAtTheEnd(res, () => c.after(done)) : res;
      });
    }
    /** @type {Map<string, boolean>} sessions: is it a subagent's */
    const child = new Map();
    return {
      // a session goes idle (its turn ended, was stopped with Esc, or failed): its turn marks go, so the
      // dashboard's Stop does not wait for it (carl-cache.js turn())
      event: async ({ event }) => {
        const idle = event.type === "session.idle" || (event.type === "session.status" && event.properties.status.type === "idle");
        const sid = idle ? event.properties.sessionID : undefined;
        if (typeof sid !== "string") return;
        for (const c of g.caches.values()) await c.turnsDone(sid).catch((e) => debugLog(`turn end ${sid}: ${errorText(e)}`));
      },
      "chat.headers": async (input, output) => {
        try {
          if (input?.model?.providerID !== ours || !input.sessionID) return;
          const sid = input.sessionID;
          if (!child.has(sid)) child.set(sid, hasParent(await client.session.get({ path: { id: sid } })));
          output.headers[MARK] = JSON.stringify({ session: sid, agent: String(input.agent ?? "build"), sub: child.get(sid) });
        } catch (e) {
          debugLog(`chat.headers: ${errorText(e)} (no mark: the request goes as it is)`);
        }
      },
    };
  },
};
