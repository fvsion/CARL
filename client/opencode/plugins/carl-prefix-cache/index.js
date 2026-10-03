// OpenCode server plugin (installed by CARL's client/install.sh): records the system prompt
// and the tool definitions OpenCode sends to CARL's provider in ~/.config/carl/prefix/MODEL.json
// (the main agent's, written only when they change). The CARL dashboard turns that into a
// pre-read prompt cache: after a server start it restores (or builds once and saves) the
// KV cache of this shared prefix, so the first request reads only the new part.
// Options: { provider: "<our provider id>" } (configure.py).
// Export nothing else from this file: older OpenCode versions call every export of the
// entry module as a plugin function.
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileName, prefixSpec } from "./spec.js";

const DIR = join(homedir(), ".config", "carl", "prefix");

/** @type {import("@opencode-ai/plugin").PluginModule & { id: string }} */
export default {
  id: "carl-prefix-cache",
  server: async ({ client }, options) => {
    const ours = typeof options?.provider === "string" ? options.provider : "llamacpp";
    /** @type {Map<string, boolean>} sessions: is it a subagent's (its system prompt is not the main one) */
    const child = new Map();
    return {
      "experimental.chat.system.transform": async (input, output) => {
        const model = input?.model;
        if (!model || model.providerID !== ours || !Array.isArray(output?.system)) return;
        const system = [...output.system];
        // in the background: the request goes out now, the spec follows
        void (async () => {
          try {
            const sid = input.sessionID;
            if (sid && !child.has(sid)) {
              const s = await client.session.get({ path: { id: sid } });
              child.set(sid, Boolean((s?.data ?? s)?.parentID));
            }
            if (sid && child.get(sid)) return;
            const res = await client.tool.list({ query: { provider: model.providerID, model: model.id } });
            const tools = res?.data ?? res;
            if (!Array.isArray(tools)) return;
            const text = JSON.stringify(prefixSpec(model.providerID, model.id, system, tools), null, 1);
            const path = join(DIR, fileName(model.id));
            let old = "";
            try { old = readFileSync(path, "utf8"); } catch { /* none yet */ }
            if (old === text) return;
            mkdirSync(DIR, { recursive: true });
            writeFileSync(path + ".tmp", text, { mode: 0o600 });
            renameSync(path + ".tmp", path);
          } catch {
            // never in the way of a request
          }
        })();
      },
    };
  },
};
