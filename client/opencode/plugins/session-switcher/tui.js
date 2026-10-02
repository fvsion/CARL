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

const MAX = 9;                          // sessions in the cycle and the picker
const RECENT_H = 72;                    // older sessions stay in OpenCode's own /sessions list
const TITLE_W = 22;                     // columns for the title in the prompt box

function el(tag, props, children = []) {
  const node = createElement(tag);
  for (const [k, v] of Object.entries(props)) if (v !== undefined) setProp(node, k, v);
  for (const c of children) if (c !== null && c !== undefined && c !== false) insert(node, c);
  return node;
}
const box = (props, children) => el("box", props, children);
const text = (fg, value) => el("text", { fg }, [value]);

function cut(s, n) {
  s = String(s ?? "").replace(/\s+/g, " ").trim();
  return s.length > n ? s.slice(0, Math.max(n - 1, 0)) + "…" : s;
}
function ago(ms) {
  const m = Math.max(0, Math.floor((Date.now() - ms) / 60000));
  return m < 1 ? "now" : m < 60 ? `${m}m ago` : m < 1440 ? `${Math.floor(m / 60)}h ago` : `${Math.floor(m / 1440)}d ago`;
}

const plugin = {
  id: "session-switcher:tui",
  tui: async (api) => {
    const sessions = new Map();         // id -> { id, title, updated }
    const [tick, setTick] = createSignal(0);
    const bump = () => { setTick((t) => t + 1); api.renderer.requestRender(); };
    let loaded = false;

    const upsert = (s) => {
      if (!s?.id) return false;
      if (s.parentID) return false;     // a subagent: not a session you switch to
      if (s.time?.archived) { sessions.delete(s.id); return true; }
      sessions.set(s.id, { id: s.id, title: s.title || "new session",
                           updated: s.time?.updated ?? s.time?.created ?? Date.now() });
      return true;
    };
    let loading = null;
    const load = () => {
      if (loaded) return loading;
      loaded = true;
      return (loading = (async () => { try {
        const res = await api.client.session.list();
        const items = Array.isArray(res) ? res : (res?.data ?? []);
        for (const s of items) upsert(s);
        bump();
      } catch { loaded = false; } })());  // on failure: retry on the next render
    };

    const current = (props) =>
      props?.session_id ?? (api.route.current?.name === "session" ? api.route.current.params?.sessionID : undefined);
    const state = (id) => {
      if ((api.state.session.permission(id) || []).length || (api.state.session.question?.(id) || []).length) return "wait";
      const st = api.state.session.status(id)?.type;
      return st && st !== "idle" ? "busy" : "idle";
    };
    const list = (cur) => {
      const cutoff = Date.now() - RECENT_H * 3600 * 1000;
      const all = [...sessions.values()].sort((a, b) => b.updated - a.updated);
      const recent = all.filter((s) => s.updated >= cutoff || s.id === cur || state(s.id) !== "idle").slice(0, MAX);
      if (cur && !recent.some((s) => s.id === cur) && sessions.has(cur)) recent.push(sessions.get(cur));
      return recent;
    };
    const go = (id) => { if (id) api.route.navigate("session", { sessionID: id }); };
    const icon = (st, theme) => st === "busy" ? ["●", theme.info] : st === "wait" ? ["!", theme.warning] : ["○", theme.textMuted];

    const picker = async (cur) => {
      await load();
      const theme = api.theme.current;
      const items = list(cur);
      if (!items.length) { api.ui.toast({ message: "No recent sessions in this project", variant: "info" }); return; }
      api.ui.dialog.replace(() => api.ui.DialogSelect({
        title: "Switch session",
        placeholder: "Filter sessions",
        current: cur,
        options: items.map((s) => {
          const st = state(s.id);
          return { title: `${icon(st, theme)[0]} ${cut(s.title, 60)}`, value: s.id,
                   description: `${st === "busy" ? "busy" : st === "wait" ? "waiting for you" : "idle"} · ${ago(s.updated)}` };
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
    ];
    api.lifecycle.onDispose(() => off.forEach((f) => f?.()));

    // /switch and the command palette (legacy command API: present in 1.18.x)
    const unreg = api.command?.register?.(() => [{
      title: "Switch session", value: "llm-deploy.session.switch", category: "Session",
      description: "Recent sessions of this project, with their state",
      slash: { name: "switch" },
      onSelect: () => picker(current()),
    }]);
    if (unreg) api.lifecycle.onDispose(unreg);

    const render = (props) => {
      tick();                                   // re-render on every update
      load();
      const theme = api.theme.current;
      try {
        const cur = current(props);
        const items = list(cur);
        if (!items.length || (items.length === 1 && items[0].id === cur)) return null;   // nothing to switch to
        const i = items.findIndex((s) => s.id === cur);
        const pos = i >= 0 ? i : -1;
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
      } catch {
        return null;                            // never break the prompt box
      }
    };

    api.slots.register({
      order: 50,
      slots: {
        session_prompt_right: (ctx, props) => render(props),   // not on the home screen: its prompt is too narrow
      },
    });
  },
};

export default plugin;
