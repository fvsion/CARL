// @ts-check
// A test plugin of the compaction bench, not shipped (Phase 23.5.2.2.4, the hook test; user, 2026-10-15): when
// OpenCode compacts a session with its own compaction, the user's rules (tools/monitor/rules.py, from the session's
// user messages) go into its summary prompt through experimental.session.compacting, with a request to keep them
// word for word. Options: { rulesPy: "<path to rules.py>", tier: "spacy+B:setfit2", log: "<a JSONL file>",
// env: { CARL_NLP_MODELS, CARL_NLP_DIR } }.
import { spawn } from "node:child_process";
import { appendFileSync } from "node:fs";

const KEEP = "Keep every one of the user's rules below in the summary's Important Details section, word for word.";

/** The rules list for these messages, from rules.py ("" when it fails). @param {string} py @param {string} tier
 * @param {{ role: string, content: string }[]} messages @param {Record<string, string>} env
 * @returns {Promise<{ text: string, tier: string }>} */
function rulesList(py, tier, messages, env) {
  return new Promise((resolve) => {
    const child = spawn("python3", [py, "--tier", tier], { stdio: ["pipe", "pipe", "pipe"], env: { ...process.env, ...env } });
    let out = "";
    child.stdout.on("data", (d) => { out += d; });
    child.on("error", () => resolve({ text: "", tier: "error" }));
    child.on("close", () => {
      try {
        const r = JSON.parse(out);
        resolve({ text: String(r.text ?? ""), tier: String(r.tier ?? "") });
      } catch {
        resolve({ text: "", tier: "error" });
      }
    });
    child.stdin.end(JSON.stringify({ messages }));
  });
}

/** @type {import("@opencode-ai/plugin").PluginModule & { id: string, setup: () => void }} */
export default {
  id: "carl-rules-hook",
  setup() {},
  server: async ({ client }, options) => {
    const py = String(options?.rulesPy ?? "");
    const tier = String(options?.tier ?? "auto");
    const log = String(options?.log ?? "");
    // OpenCode may run with another HOME (a harness): the models' folder and the NLP environment by option
    const env = /** @type {Record<string, string>} */ (options?.env && typeof options.env === "object" ? options.env : {});
    return {
      "experimental.session.compacting": async (input, output) => {
        const t0 = Date.now();
        try {
          const r = await client.session.messages({ path: { id: input.sessionID } });
          const all = /** @type {any[]} */ (Array.isArray(r) ? r : r?.data ?? []);
          /** @type {{ role: string, content: string }[]} */
          const messages = [];
          for (const m of all) {
            if (m?.info?.role !== "user") continue;
            const parts = m.parts ?? [];
            if (parts.some((/** @type {any} */ p) => p?.type === "compaction")) continue;
            const text = parts.filter((/** @type {any} */ p) => p?.type === "text" && !p.synthetic)
              .map((/** @type {any} */ p) => String(p.text ?? "")).join("\n").trim();
            if (text && !text.startsWith("Continue if you have next steps")) messages.push({ role: "user", content: text });
          }
          const got = py ? await rulesList(py, tier, messages, env) : { text: "", tier: "no rules.py" };
          if (got.text) output.context.push(`${KEEP}\n\n${got.text}`);
          if (log) appendFileSync(log, JSON.stringify({ at: new Date().toISOString(), session: input.sessionID,
            user_messages: messages.length, tier: got.tier, seconds: (Date.now() - t0) / 1000, rules: got.text }) + "\n");
        } catch (e) {
          if (log) appendFileSync(log, JSON.stringify({ at: new Date().toISOString(), error: String(e) }) + "\n");
        }
      },
    };
  },
};
