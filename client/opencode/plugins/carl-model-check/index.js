// OpenCode server plugin (installed by CARL's client/install.sh): before each request to
// CARL's provider, checks the model picked against the server (check.js) and shows a
// toast when they don't match:
//   single-model mode   the server runs another model (it answers with that one)
//   router mode         the model isn't installed (HTTP 400), or it is being loaded
// Each situation is said once (also in OpenCode's log, service=carl-model-check). Options: { provider: "<our provider id>" } (configure.py).
// Export nothing else from this file: older OpenCode versions call every export of the
// entry module as a plugin function.
import { serverModels, verdict } from "./check.js";

const CACHE_MS = 5000;

/** @type {import("@opencode-ai/plugin").PluginModule & { id: string }} */
export default {
  id: "carl-model-check",
  server: async ({ client }, options) => {
    const ours = typeof options?.provider === "string" ? options.provider : "llamacpp";
    /** @type {Set<string>} */
    const said = new Set();
    /** @type {{ t: number, url: string, state: Awaited<ReturnType<typeof serverModels>> }} */
    let cache = { t: 0, url: "", state: undefined };
    return {
      "chat.params": async (input) => {
        try {
          if (input?.model?.providerID !== ours) return;
          const opts = input.provider?.options ?? {};
          const url = typeof opts.baseURL === "string" ? opts.baseURL : "";
          if (!url) return;
          const now = Date.now();
          if (cache.url !== url || now - cache.t > CACHE_MS) {
            cache = { t: now, url, state: await serverModels(url, typeof opts.apiKey === "string" ? opts.apiKey : undefined) };
          }
          const v = cache.state && verdict(String(input.model.id), cache.state);
          if (!v || said.has(v.key)) return;
          said.add(v.key);
          // OpenCode's log too: `opencode run` and other headless uses have no TUI for the toast
          await client.app.log({ body: { service: "carl-model-check", level: v.variant === "info" ? "info" : "warn",
                                         message: v.message } }).catch(() => {});
          await client.tui.showToast({ body: { title: "CARL", message: v.message, variant: v.variant, duration: 12000 } });
        } catch {
          // never block or break a request
        }
      },
    };
  },
};
