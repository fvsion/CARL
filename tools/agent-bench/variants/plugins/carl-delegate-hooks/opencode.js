// @ts-check
// CARL (Phase 23 variants V4 / V5): the delegation hooks in OpenCode (delegate-hooks.js has the rules).
// Only main sessions: a session OpenCode created with a parent (a subagent: the coder, explore ...) is left alone.
import { existsSync } from "node:fs";
import { Turn, on, withReminder } from "./delegate-hooks.js";

/** @type {string} */
const MODE = "__MODE__";

/** @type {import("@opencode-ai/plugin").PluginModule & { id: string }} */
export default {
  id: "carl-delegate-hooks",
  server: async (ctx) => {
    const cwd = String(ctx?.directory ?? process.cwd());
    /** @type {Map<string, Turn>} */
    const turns = new Map();
    /** @type {Set<string>} */
    const children = new Set();
    const turn = (/** @type {string} */ id) => {
      let t = turns.get(id);
      if (!t) turns.set(id, (t = new Turn()));
      return t;
    };
    return {
      event: async ({ event }) => {
        const info = /** @type {any} */ (event)?.properties?.info;
        if ((event?.type === "session.created" || event?.type === "session.updated") && info?.parentID) {
          children.add(String(info.id));
        }
      },
      "chat.message": async (input) => {
        if (input?.sessionID && !children.has(input.sessionID)) turn(input.sessionID).reset();
      },
      // V2 (rough): the reminder at the end of every user message of a main session (stable text: cache-safe)
      "experimental.chat.messages.transform": async (_input, output) => {
        if (!on(MODE, "remind")) return;
        for (const m of output?.messages ?? []) {
          const info = /** @type {any} */ (m.info);
          if (info?.role !== "user" || children.has(String(info.sessionID))) continue;
          const texts = (m.parts ?? []).filter((/** @type {any} */ p) => p?.type === "text" && !p.synthetic);
          const last = /** @type {any} */ (texts[texts.length - 1]);
          if (last) last.text = withReminder(String(last.text ?? ""), "opencode");
        }
      },
      "tool.execute.before": async (input, output) => {
        if (!input?.sessionID || children.has(input.sessionID)) return;
        const why = turn(input.sessionID).before(MODE, "opencode", String(input.tool), output?.args ?? {}, cwd, existsSync);
        if (why) throw new Error(why);
      },
      "tool.execute.after": async (input, output) => {
        if (!input?.sessionID || children.has(input.sessionID) || !output) return;
        const add = turn(input.sessionID).after(MODE, "opencode", String(input.tool));
        if (add) output.output = String(output.output ?? "") + add;
      },
    };
  },
};
