// Subagents sidebar for OpenCode (TUI plugin), in the spirit of Claude Code's
// agent list: for the session on screen it shows every subagent (child session)
// with its agent, task, status, elapsed time, context size and current tool.
// Click a subagent to open its conversation; click the header to collapse.
//
// Data: api.client.session.children() once per session, then live events:
//   session.created / session.updated  -> new or renamed subagents
//   session.status                     -> busy / idle / retry
//   message.updated                    -> model, tokens, errors, completion
//   message.part.updated (tool parts)  -> what it's doing right now
// Read-only; no network or file access.
import { createElement, insert, setProp } from "@opentui/solid";
import { createSignal } from "solid-js";

const WIDTH = 32;                       // usable columns in OpenCode's sidebar (measured at 170-col terminal)
const SPIN = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

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
function dur(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  return s >= 3600 ? `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`
       : s >= 60 ? `${Math.floor(s / 60)}m${String(s % 60).padStart(2, "0")}s` : `${s}s`;
}
function kfmt(n) {
  return n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n || 0);
}

// OpenCode titles child sessions "<description> (@<agent> subagent)".
function parseTitle(title) {
  const m = /^(.*?)\s*\(@([\w-]+) subagent\)\s*$/.exec(title || "");
  return m ? { task: m[1] || "subagent", agent: m[2] } : { task: title || "subagent", agent: "" };
}

function toolSummary(part) {
  const st = part.state || {};
  const input = st.input || {};
  const arg = st.title || input.description || input.filePath || input.path || input.pattern ||
              input.command || input.query || input.url || "";
  return arg ? `${part.tool} ${arg}` : part.tool;
}

const plugin = {
  id: "subagents-sidebar:tui",
  tui: async (api) => {
    const subs = new Map();            // childID -> info
    const loaded = new Set();          // parent IDs fetched once
    const [tick, setTick] = createSignal(0);
    const [collapsed, setCollapsed] = createSignal(false);
    const [expanded, setExpanded] = createSignal(null);
    const bump = () => { setTick((t) => t + 1); api.renderer.requestRender(); };

    const upsert = (session) => {
      if (!session?.parentID) return false;
      const cur = subs.get(session.id) || { id: session.id, status: "busy", tokens: 0, output: 0, cost: 0 };
      const { task, agent } = parseTitle(session.title);
      Object.assign(cur, { parentID: session.parentID, task, agent: agent || cur.agent || "",
                           created: session.time?.created ?? cur.created ?? Date.now(),
                           updated: session.time?.updated ?? Date.now() });
      subs.set(session.id, cur);
      return true;
    };

    // One subagent's history (after a restart or reopening a session): tool
    // calls, last context size, model, error, completion time.
    const history = async (s) => {
      try {
        let res = await api.client.session.messages({ sessionID: s.id });
        if (res?.error) res = await api.client.session.messages({ path: { id: s.id } });
        const msgs = Array.isArray(res) ? res : (res?.data ?? []);
        for (const m of msgs) {
          const info = m.info || m;
          if (info.role === "assistant") {
            const t = info.tokens || {};
            s.tokens = (t.input || 0) + (t.cache?.read || 0) + (t.cache?.write || 0) + (t.output || 0);
            s.model = info.modelID || s.model;
            s.error = info.error ? (info.error.data?.message || info.error.name || "error") : undefined;
            if (info.time?.completed) s.done = info.time.completed;
          }
          for (const p of m.parts || []) {
            if (p.type !== "tool") continue;
            s.lastTool = toolSummary(p);
            if (p.state?.status === "completed" || p.state?.status === "error") (s.calls ??= new Set()).add(p.callID || p.id);
          }
        }
        bump();
      } catch { /* the live events still fill it in */ }
    };

    const load = async (parentID) => {
      if (!parentID || loaded.has(parentID)) return;
      loaded.add(parentID);
      try {
        // The TUI client is the v2 SDK (flat params); fall back to the v1 shape.
        let res = await api.client.session.children({ sessionID: parentID });
        if (res?.error) res = await api.client.session.children({ path: { id: parentID } });
        const items = Array.isArray(res) ? res : (res?.data ?? []);
        for (const s of items) {
          if (upsert(s)) {
            const st = api.state.session.status(s.id);
            subs.get(s.id).status = st?.type ?? "idle";
            history(subs.get(s.id));
          }
        }
        bump();
      } catch {
        loaded.delete(parentID);       // retry on the next render
      }
    };

    const off = [
      api.event.on("session.created", (e) => { if (upsert(e.properties.info)) bump(); }),
      api.event.on("session.updated", (e) => { if (upsert(e.properties.info)) bump(); }),
      api.event.on("session.status", (e) => {
        const s = subs.get(e.properties.sessionID);
        if (!s) return;
        s.status = e.properties.status?.type ?? s.status;
        if (s.status === "idle") s.done = s.done ?? Date.now();
        else s.done = undefined;
        bump();
      }),
      api.event.on("message.updated", (e) => {
        const m = e.properties.info;
        const s = m && subs.get(m.sessionID);
        if (!s || m.role !== "assistant") return;
        const t = m.tokens || {};
        s.tokens = (t.input || 0) + (t.cache?.read || 0) + (t.cache?.write || 0) + (t.output || 0);
        s.model = m.modelID || s.model;
        s.error = m.error ? (m.error.data?.message || m.error.name || "error") : undefined;
        bump();
      }),
      api.event.on("message.part.updated", (e) => {
        const p = e.properties.part;
        const s = p && subs.get(p.sessionID);
        if (!s || p.type !== "tool") return;
        const st = p.state?.status;
        if (st === "running" || st === "pending") s.tool = toolSummary(p);
        else {
          s.lastTool = toolSummary(p);
          if (s.tool && s.tool.startsWith(p.tool)) s.tool = undefined;
          if (st === "completed" || st === "error") (s.calls ??= new Set()).add(p.callID || p.id);
        }
        bump();
      }),
    ];
    // spinner + elapsed time while anything runs
    const timer = setInterval(() => {
      for (const s of subs.values()) if (s.status !== "idle") return bump();
    }, 250);
    api.lifecycle.onDispose(() => { clearInterval(timer); off.forEach((f) => f?.()); });

    const currentSession = (props) =>
      props?.session_id ?? (api.route.current?.name === "session" ? api.route.current.params?.sessionID : undefined);

    api.slots.register({
      order: 50,                                    // near the top of the sidebar
      slots: {
        sidebar_content: (ctx, props) => {
          tick();                                   // re-render on every update
          const theme = api.theme.current;
          try {
            const sid = currentSession(props) ?? currentSession(ctx);
            if (!sid) return null;
            // In a subagent's own session, show its siblings under the same parent.
            const parent = subs.get(sid)?.parentID ?? sid;
            load(parent);
            const list = [...subs.values()].filter((s) => s.parentID === parent)
              .sort((a, b) => (a.created || 0) - (b.created || 0));
            if (!list.length) return null;
            const running = list.filter((s) => s.status !== "idle").length;
            const failed = list.filter((s) => s.status === "idle" && s.error).length;
            const done = list.length - running - failed;
            const head = [`${collapsed() ? "▶" : "▼"} Subagents`,
                          running ? `${running} running` : "", done ? `${done} done` : "", failed ? `${failed} failed` : ""]
              .filter(Boolean).join(" · ");
            const rows = [box({ width: "100%", onMouseDown: () => { setCollapsed(!collapsed()); bump(); } },
                              [text(theme.accent, cut(head, WIDTH))])];
            if (!collapsed()) {
              const frame = SPIN[Math.floor(Date.now() / 100) % SPIN.length];
              for (const s of list) {
                const busy = s.status !== "idle";
                const icon = busy ? frame : s.error ? "✗" : "✓";
                const col = busy ? (s.status === "retry" ? theme.warning : theme.info)
                          : s.error ? theme.error : theme.success;
                const right = " " + dur((busy ? Date.now() : (s.done || s.updated || Date.now())) - (s.created || Date.now()));
                const name = s.agent || "agent";
                const isSel = s.id === sid;
                const first = `${icon} ${name}`;
                const body = [box({ width: "100%", flexDirection: "row" }, [
                  text(col, first),
                  text(isSel ? theme.text : theme.textMuted, " " + cut(s.task, WIDTH - first.length - right.length - 1)),
                  box({ flexGrow: 1 }, []),
                  text(theme.textMuted, right),
                ])];
                const calls = s.calls?.size || 0;
                const detail = busy ? (s.status === "retry" ? "↳ retrying…" : s.tool ? `↳ ${s.tool}`
                                       : s.lastTool ? `↳ ${s.lastTool} ✓` : "↳ starting…")
                             : s.error ? `↳ ${s.error}` : `↳ ${calls} tool${calls === 1 ? "" : "s"} · ${kfmt(s.tokens)} ctx`;
                body.push(text(busy ? theme.text : (s.error ? theme.error : theme.textMuted), "  " + cut(detail, WIDTH - 2)));
                if (expanded() === s.id) {
                  body.push(text(theme.textMuted, "  " + cut(`${s.model || "?"} · ${kfmt(s.tokens)} ctx`, WIDTH - 2)));
                  body.push(text(theme.accent, "  " + cut("open: click again", WIDTH - 2)));
                }
                rows.push(box({
                  width: "100%", flexDirection: "column",
                  onMouseDown: () => {
                    if (expanded() === s.id) api.route.navigate("session", { sessionID: s.id });
                    else setExpanded(s.id);
                    bump();
                  },
                }, body));
              }
            }
            return box({ width: "100%", flexDirection: "column", paddingBottom: 1 }, rows);
          } catch (err) {
            return text(theme.error, cut(`subagents-sidebar: ${err}`, WIDTH));
          }
        },
      },
    });
  },
};

export default plugin;
