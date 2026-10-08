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

import { existsSync } from "node:fs";
import { GateSetting, RULE_BEGIN, RULE_END, Turn, withReminder, withoutRule } from "./carl-delegation.js";

export { RULE_BEGIN, RULE_END, withoutRule };

/** @type {import("@opencode-ai/plugin").PluginModule & { id: string }} */
export default {
  id: "carl-delegation",
  server: async (ctx, options) => {
    const opts = /** @type {Record<string, unknown>} */ (options ?? {});
    const reminder = opts.reminder !== false;
    const gateSetting = new GateSetting({ cacheApi: typeof opts.cacheApi === "string" ? opts.cacheApi : "" });
    const coder = typeof opts.coder === "string" && opts.coder ? opts.coder : "coder";
    const cwd = String(/** @type {any} */ (ctx)?.directory ?? process.cwd());
    /** @type {Map<string, boolean>} session id -> is a subagent session (has a parent) */
    const sub = new Map();
    /** @type {Map<string, Turn>} */
    const turns = new Map();
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
    return {
      event: async ({ event }) => {
        const info = /** @type {any} */ (event)?.properties?.info;
        if ((event?.type === "session.created" || event?.type === "session.updated") && info?.id) {
          sub.set(String(info.id), Boolean(info.parentID));
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
      "chat.message": async (input) => {
        if (input?.sessionID) turns.get(input.sessionID)?.reset();
      },
      "tool.execute.before": async (input, output) => {
        const gate = await gateSetting.get();                     // the dashboard's setting (Connect > Setup)
        if (!gate || !input?.sessionID || (await isSub(input.sessionID))) return;
        const why = turn(input.sessionID).before(gate, "opencode", String(input.tool), output?.args ?? {}, cwd, existsSync,
                                                 coder);
        if (why) throw new Error(why);
      },
    };
  },
};
