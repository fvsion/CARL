// @ts-check
// Session switcher for OpenCode (TUI plugin). OpenCode 1.18.34 no longer shows
// open sessions as tabs, so this puts a one-line switcher on the right side of
// the prompt box:   ‹ 2/3 ● fix the parser ›
//   ‹ ›     previous / next session (newest first)
//   title   opens a picker with every recent session; /switch opens it too
//   ●       busy (thinking or running tools)   ! waits for a permission or a question
//   ○       idle
// Sessions: the top-level sessions of this project (subagent sessions are
// left out), updated in the last RECENT_H hours, at most MAX of them.
// Data: api.client.session.list() once, then session.* events. Read-only.
import { createElement, insert, setProp } from "@opentui/solid";
import { createSignal } from "solid-js";
import { asElement, cut, nodes, routeSessionID, rows } from "./carl-tui.js";

/** @typedef {import("@opencode-ai/plugin/tui").TuiPluginModule} TuiPluginModule */
/** @typedef {import("./carl-tui.js").Theme} Theme */
/** @typedef {import("./carl-tui.js").Color} Color */
/** @typedef {import("./carl-tui.js").Node} Node */
/** @typedef {import("@opencode-ai/sdk/v2").Session} Session */
/** @typedef {{ id: string, title: string, updated: number }} SessionEntry */
/** @typedef {"busy" | "wait" | "idle"} SessionState */

const MAX = 9;                          // sessions in the cycle and the picker
const RECENT_H = 72;                    // older sessions stay in OpenCode's own /sessions list
const TITLE_W = 22;                     // columns for the title in the prompt box

const { box, text } = nodes({ createElement, insert, setProp });

/** @param {number} ms @param {number} now @returns {string} */
function ago(ms, now) {
  const m = Math.max(0, Math.floor((now - ms) / 60000));
  return m < 1 ? "now" : m < 60 ? `${m}m ago` : m < 1440 ? `${Math.floor(m / 60)}h ago` : `${Math.floor(m / 1440)}d ago`;
}

/** @param {SessionState} st @param {Theme} theme @returns {[string, Color]} */
const icon = (st, theme) => st === "busy" ? ["●", theme.info] : st === "wait" ? ["!", theme.warning] : ["○", theme.textMuted];
/** @param {SessionState} st @returns {string} */
const stateLabel = (st) => st === "busy" ? "busy" : st === "wait" ? "waiting for you" : "idle";

/**
 * The sessions to cycle through, newest first: recent or active ones, at most
 * MAX, plus the current one when it fell outside that window.
 * @param {Map<string, SessionEntry>} sessions
 * @param {string | undefined} cur
 * @param {number} now
 * @param {(id: string) => boolean} active
 * @returns {SessionEntry[]}
 */
function recentSessions(sessions, cur, now, active) {
  const cutoff = now - RECENT_H * 3600 * 1000;
  const all = [...sessions.values()].sort((a, b) => b.updated - a.updated);
  const recent = all.filter((s) => s.updated >= cutoff || s.id === cur || active(s.id)).slice(0, MAX);
  const current = cur === undefined ? undefined : sessions.get(cur);
  if (current && !recent.some((s) => s.id === cur)) recent.push(current);
  return recent;
}

/** @type {TuiPluginModule & { id: string }} */
const plugin = {
  id: "session-switcher:tui",
  tui: async (api) => {
    /** @type {Map<string, SessionEntry>} */
    const sessions = new Map();
    const [tick, setTick] = createSignal(0);
    const bump = () => { setTick((t) => t + 1); api.renderer.requestRender(); };

    /** @param {Session | undefined} s @returns {boolean} true when the list changed */
    const upsert = (s) => {
      if (!s?.id) return false;
      if (s.parentID) return false;     // a subagent: not a session you switch to
      if (s.time?.archived) { sessions.delete(s.id); return true; }
      sessions.set(s.id, { id: s.id, title: s.title || "new session",
                           updated: s.time?.updated ?? s.time?.created ?? Date.now() });
      return true;
    };

    let loaded = false;
    /** @type {Promise<void> | undefined} */
    let loading;
    /** Fetches the list once; a failed fetch is retried on the next call. */
    const load = () => {
      if (loaded) return loading;
      loaded = true;
      return (loading = (async () => {
        try {
          for (const s of rows(await api.client.session.list())) upsert(s);
          bump();
        } catch {
          loaded = false;
        }
      })());
    };

    /** @param {string} id @returns {SessionState} */
    const state = (id) => {
      if ((api.state.session.permission(id) ?? []).length || (api.state.session.question?.(id) ?? []).length) return "wait";
      const st = api.state.session.status(id)?.type;
      return st && st !== "idle" ? "busy" : "idle";
    };
    /** @param {string | undefined} cur */
    const list = (cur) => recentSessions(sessions, cur, Date.now(), (id) => state(id) !== "idle");
    /** @param {string | undefined} id */
    const go = (id) => { if (id) api.route.navigate("session", { sessionID: id }); };

    /** @param {string | undefined} cur */
    const picker = async (cur) => {
      await load();
      const theme = api.theme.current;
      const items = list(cur);
      if (!items.length) { api.ui.toast({ message: "No recent sessions in this project", variant: "info" }); return; }
      const now = Date.now();
      api.ui.dialog.replace(() => api.ui.DialogSelect({
        title: "Switch session",
        placeholder: "Filter sessions",
        current: cur,
        options: items.map((s) => {
          const st = state(s.id);
          return { title: `${icon(st, theme)[0]} ${cut(s.title, 60)}`, value: s.id,
                   description: `${stateLabel(st)} · ${ago(s.updated, now)}` };
        }),
        onSelect: (opt) => { api.ui.dialog.clear(); go(opt.value); },
      }));
    };

    load();                                     // at start-up: /switch works before anything is drawn
    const off = [
      api.event.on("session.created", (e) => { if (upsert(e.properties.info)) bump(); }),
      api.event.on("session.updated", (e) => { if (upsert(e.properties.info)) bump(); }),
      api.event.on("session.deleted", (e) => { if (sessions.delete(e.properties.info?.id)) bump(); }),
      api.event.on("session.status", () => bump()),
      api.event.on("permission.asked", () => bump()),
      api.event.on("permission.replied", () => bump()),
      // a pending question shows "!" too, so its events redraw like permissions
      api.event.on("question.asked", () => bump()),
      api.event.on("question.replied", () => bump()),
      api.event.on("question.rejected", () => bump()),
    ];
    api.lifecycle.onDispose(() => off.forEach((f) => f?.()));

    // /switch and the command palette (legacy command API: present in 1.18.x)
    const unreg = api.command?.register?.(() => [{
      title: "Switch session", value: "carl.session.switch", category: "Session",
      description: "Recent sessions of this project, with their state",
      slash: { name: "switch" },
      onSelect: () => picker(routeSessionID(api)),
    }]);
    if (unreg) api.lifecycle.onDispose(unreg);

    /**
     * ‹ pos/count icon title [+N●] ›, or null when there is nothing to switch to.
     * @param {string | undefined} cur
     * @param {Theme} theme
     * @returns {Node | null}
     */
    const switcher = (cur, theme) => {
      const items = list(cur);
      if (!items.length || (items.length === 1 && items[0].id === cur)) return null;
      const pos = items.findIndex((s) => s.id === cur);       // -1: current not listed
      const prev = items[(pos - 1 + items.length) % items.length];
      const next = items[(pos + 1) % items.length];
      const shown = pos >= 0 ? items[pos] : null;
      const busyElsewhere = items.filter((s) => s.id !== cur && state(s.id) !== "idle").length;
      const [ic, icCol] = shown ? icon(state(shown.id), theme) : ["◦", theme.textMuted];
      return box({ flexDirection: "row", flexShrink: 0 }, [
        box({ onMouseDown: () => go(prev.id) }, [text(theme.accent, "‹ ")]),
        box({ flexDirection: "row", onMouseDown: () => picker(cur) }, [
          text(theme.textMuted, `${pos >= 0 ? pos + 1 : "–"}/${items.length} `),
          text(icCol, ic + " "),
          text(theme.text, cut(shown ? shown.title : "sessions", TITLE_W)),
          busyElsewhere ? text(theme.info, ` +${busyElsewhere}●`) : null,
        ]),
        box({ onMouseDown: () => go(next.id) }, [text(theme.accent, " ›")]),
      ]);
    };

    api.slots.register({
      order: 50,
      slots: {
        // not on the home screen: its prompt is too narrow
        session_prompt_right: (_ctx, props) => {
          tick();                               // re-render on every update
          load();
          try {
            return asElement(switcher(props?.session_id ?? routeSessionID(api), api.theme.current));
          } catch {
            return null;                        // never break the prompt box
          }
        },
      },
    });
  },
};

export default plugin;
