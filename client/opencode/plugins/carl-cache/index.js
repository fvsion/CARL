// OpenCode server plugin (installed by CARL's client/install.sh): CARL's prompt cache (carl-cache.js) for
// the requests OpenCode sends to CARL's provider. Each session's conversation is saved on the server's
// disk after its turn and restored before its next request when the server no longer holds it (after a
// restart, a model switch, or many other sessions); each agent's prompt is read once and saved.
//
// How: chat.headers marks our provider's requests with the session, the agent and whether it is a
// subagent; a wrapper around fetch (the AI SDK calls the global fetch) reads those marks, takes them off
// and lets carl-cache.js prepare the request body; when a reply that ends the turn has streamed, the
// session is saved before OpenCode sees the end.
// Options: { provider: "<our provider id>" } (configure.py).
// Export nothing else from this file: older OpenCode versions call every export of the entry module as
// a plugin function.
import { CarlCache, splitOpenCode } from "./carl-cache.js";

const MARK = "x-carl-cache";
const STATE = Symbol.for("carl-cache.opencode");

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
      if (final) await save().catch(() => {});
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
    const log = (/** @type {string} */ message) =>
      client.app.log({ body: { service: "carl-cache", level: "info", message } }).catch(() => {});
    /** @type {{ original: typeof fetch, caches: Map<string, CarlCache> }} */
    const g = /** @type {any} */ (globalThis)[STATE] ?? { original: globalThis.fetch, caches: new Map() };
    if (!(/** @type {any} */ (globalThis)[STATE])) {
      /** @type {any} */ (globalThis)[STATE] = g;
      globalThis.fetch = /** @type {typeof fetch} */ (async (input, init) => {
        const headers = new Headers(init?.headers ?? undefined);
        const mark = headers.get(MARK);
        if (!mark || typeof init?.body !== "string" || !(typeof input === "string" || input instanceof URL)) {
          return g.original(input, init);
        }
        headers.delete(MARK);
        const url = String(input);
        let release = () => {};
        let body = init.body;
        /** @type {{ session: string, agent: string, sub: boolean } | undefined} */
        let meta;
        const base = url.slice(0, url.lastIndexOf("/chat/completions"));
        try {
          meta = JSON.parse(mark);
          let cache = g.caches.get(base);
          if (!cache) {
            const key = (headers.get("authorization") ?? "").replace(/^Bearer\s+/i, "") || undefined;
            cache = new CarlCache({ baseURL: base, apiKey: key, fetch: g.original, split: splitOpenCode, log });
            g.caches.set(base, cache);
          }
          const r = await cache.before(JSON.parse(body), /** @type {any} */ (meta));
          body = JSON.stringify(r.payload);
          release = r.release;
        } catch {
          // never in the way of a request
        }
        let res;
        try {
          res = await g.original(input, { ...init, headers, body });
        } finally {
          release();
        }
        const cache = meta && g.caches.get(base);
        return cache && res.ok && res.body ? saveAtTheEnd(res, () => cache.after(meta)) : res;
      });
    }
    /** @type {Map<string, boolean>} sessions: is it a subagent's */
    const child = new Map();
    return {
      "chat.headers": async (input, output) => {
        try {
          if (input?.model?.providerID !== ours || !input.sessionID) return;
          const sid = input.sessionID;
          if (!child.has(sid)) {
            const s = await client.session.get({ path: { id: sid } });
            child.set(sid, Boolean((s?.data ?? s)?.parentID));
          }
          output.headers[MARK] = JSON.stringify({ session: sid, agent: String(input.agent ?? "build"), sub: child.get(sid) });
        } catch {
          // no mark: the request goes as it is
        }
      },
    };
  },
};
