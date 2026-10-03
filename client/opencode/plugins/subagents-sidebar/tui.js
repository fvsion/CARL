// Subagents sidebar for OpenCode (TUI plugin), in the spirit of Claude Code's
// agent list: for the session on screen it shows its subagents (child sessions).
//   running  on top, oldest first: agent, task, elapsed time, current tool
//   done     below, newest first, one line each: ✓ / ✗, agent, task, duration.
//            At most DONE_MAX, and only for DONE_TTL after they finish; older
//            ones fold into a "+N more" line (click it to list them all).
// Click a subagent to expand it, click again to open its conversation; click
// the header to collapse.
//
// Data: api.client.session.children() once per parent session, then live events:
//   session.created / session.updated  -> new or renamed subagents
//   session.status / session.idle      -> busy / retry / idle (idle = finished)
//   session.error                      -> failed or aborted
//   session.deleted                    -> dropped from the list
//   message.updated                    -> model, tokens, errors, completion
//   message.part.updated (child tools) -> what it's doing right now
//   message.part.updated (parent task) -> finished: the `task` tool's
//       metadata.sessionId names the child, and the call ends when it does
// Read-only; no network or file access.
import { createElement, insert, setProp } from "@opentui/solid";
import { createSignal } from "solid-js";

const WIDTH = 32;                       // usable columns in OpenCode's sidebar (measured at 170-col terminal)
const DONE_MAX = 5;                     // finished subagents listed under the running ones
const DONE_TTL = 5 * 60 * 1000;         // ...and only for this long after they finish
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
// Message / session errors are objects ({ name, data.message }), tool errors strings.
function errText(err) {
  if (!err) return undefined;
  if (typeof err === "string") return err;
  if (err.name === "MessageAbortedError") return "aborted";
  return err.data?.message || err.name || "error";
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
    // view state; reset (except collapsed) when another parent session is shown
    const view = { parent: undefined, collapsed: false, expanded: null, all: false };
    const bump = () => { setTick((t) => t + 1); api.renderer.requestRender(); };

    // status: "busy" | "retry" | "idle"; idle means finished, at s.done.
    const run = (s, type) => {
      if (s.status === "idle") { s.done = undefined; s.error = undefined; }   // resumed (task_id)
      s.status = type;
    };
    const finish = (s, at) => {
      if (s.status !== "idle") { s.status = "idle"; s.done = at ?? Date.now(); }
      else s.done ??= at ?? Date.now();
    };

    const upsert = (session, created) => {
      if (!session?.parentID) return false;
      let cur = subs.get(session.id);
      if (!cur) {
        // First sight: a just-created one is starting; anything else (loaded
        // later, or updated after the fact) takes OpenCode's own status.
        const st = api.state.session.status(session.id)?.type;
        cur = { id: session.id, status: st ?? (created ? "busy" : "idle"), tokens: 0 };
        if (cur.status === "idle") cur.done = session.time?.updated ?? Date.now();
        subs.set(session.id, cur);
      }
      const { task, agent } = parseTitle(session.title);
      Object.assign(cur, { parentID: session.parentID, task, agent: agent || session.agent || cur.agent || "",
                           created: session.time?.created ?? cur.created ?? Date.now(),
                           updated: session.time?.updated ?? Date.now() });
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
            s.error = errText(info.error);
            if (info.time?.completed && s.status === "idle") s.done = info.time.completed;
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
        for (const s of items) if (upsert(s, false)) history(subs.get(s.id));
        bump();
      } catch {
        loaded.delete(parentID);       // retry on the next render
      }
    };

    // The parent's `task` call for a subagent: it returns when the subagent's
    // run ends (unless it was sent to the background, which returns at once).
    const taskPart = (p) => {
      const st = p.state || {};
      const s = subs.get(st.metadata?.sessionId);
      if (!s || st.metadata?.background) return false;
      if (st.status === "completed") finish(s, st.time?.end);
      else if (st.status === "error") { finish(s, st.time?.end); s.error = errText(st.error) || "failed"; }
      else return false;
      return true;
    };

    const off = [
      api.event.on("session.created", (e) => { if (upsert(e.properties.info, true)) bump(); }),
      api.event.on("session.updated", (e) => { if (upsert(e.properties.info, false)) bump(); }),
      api.event.on("session.deleted", (e) => { if (subs.delete(e.properties.info?.id ?? e.properties.sessionID)) bump(); }),
      api.event.on("session.status", (e) => {
        const s = subs.get(e.properties.sessionID);
        const type = e.properties.status?.type;
        if (!s || !type) return;
        if (type === "idle") finish(s);
        else run(s, type);
        bump();
      }),
      api.event.on("session.idle", (e) => {
        const s = subs.get(e.properties.sessionID);
        if (s) { finish(s); bump(); }
      }),
      api.event.on("session.error", (e) => {
        const s = subs.get(e.properties.sessionID);
        if (s) { s.error = errText(e.properties.error) || "error"; bump(); }
      }),
      api.event.on("message.updated", (e) => {
        const m = e.properties.info;
        const s = m && subs.get(m.sessionID);
        if (!s || m.role !== "assistant") return;
        const t = m.tokens || {};
        s.tokens = (t.input || 0) + (t.cache?.read || 0) + (t.cache?.write || 0) + (t.output || 0);
        s.model = m.modelID || s.model;
        s.error = errText(m.error);
        bump();
      }),
      api.event.on("message.part.updated", (e) => {
        const p = e.properties.part;
        if (!p || p.type !== "tool") return;
        const ended = p.tool === "task" && taskPart(p);
        const s = subs.get(p.sessionID);
        if (!s) { if (ended) bump(); return; }
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
    // spinner + elapsed time while anything runs; otherwise a slow tick (5 s)
    // so that finished ones drop off the list once DONE_TTL has passed
    let slow = 0;
    const timer = setInterval(() => {
      let recent = false;
      for (const s of subs.values()) {
        if (s.status !== "idle") return bump();
        if (Date.now() - (s.done || 0) < DONE_TTL + 5000) recent = true;
      }
      if (recent && ++slow % 20 === 0) bump();
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
            const parent = subs.get(sid)?.parentID ?? api.state.session.get(sid)?.parentID ?? sid;
            if (parent !== view.parent) Object.assign(view, { parent, expanded: null, all: false });
            load(parent);
            const mine = [...subs.values()].filter((s) => s.parentID === parent);
            if (!mine.length) return null;
            const now = Date.now();
            const running = mine.filter((s) => s.status !== "idle").sort((a, b) => (a.created || 0) - (b.created || 0));
            const done = mine.filter((s) => s.status === "idle").sort((a, b) => (b.done || 0) - (a.done || 0));
            // recently finished ones (and the one on screen) stay; the rest fold away
            const recent = done.filter((s, i) => s.id === sid || (i < DONE_MAX && now - (s.done || 0) < DONE_TTL));
            const more = done.length - recent.length;
            const failed = done.filter((s) => s.error).length;
            const tally = [done.length - failed ? `${done.length - failed}✓` : "", failed ? `${failed}✗` : ""].filter(Boolean).join(" ");
            const head = [`${view.collapsed ? "▶" : "▼"} Subagents`, running.length ? `${running.length} running` : "", tally]
              .filter(Boolean).join(" · ");
            const rows = [box({ width: "100%", onMouseDown: () => { view.collapsed = !view.collapsed; bump(); } },
                              [text(theme.accent, cut(head, WIDTH))])];
            if (view.collapsed) return box({ width: "100%", flexDirection: "column", paddingBottom: 1 }, rows);

            // first line: icon, agent, task, time on the right
            const line = (s, icon, col, ms) => {
              const right = " " + dur(ms);
              const first = `${icon} ${s.agent || "agent"}`;
              return box({ width: "100%", flexDirection: "row" }, [
                text(col, first),
                text(s.id === sid ? theme.text : theme.textMuted, " " + cut(s.task, WIDTH - first.length - right.length - 1)),
                box({ flexGrow: 1 }, []),
                text(theme.textMuted, right),
              ]);
            };
            const item = (s, body) => {
              if (view.expanded === s.id) {
                body.push(text(theme.textMuted, "  " + cut(`${s.model || "?"} · ${kfmt(s.tokens)} ctx`, WIDTH - 2)));
                body.push(text(theme.accent, "  " + cut("open: click again", WIDTH - 2)));
              }
              rows.push(box({
                width: "100%", flexDirection: "column",
                onMouseDown: () => {
                  if (view.expanded === s.id) api.route.navigate("session", { sessionID: s.id });
                  else view.expanded = s.id;
                  bump();
                },
              }, body));
            };

            const frame = SPIN[Math.floor(now / 100) % SPIN.length];
            for (const s of running) {
              const col = s.status === "retry" ? theme.warning : theme.info;
              const detail = s.status === "retry" ? "↳ retrying…" : s.tool ? `↳ ${s.tool}`
                           : s.lastTool ? `↳ ${s.lastTool} ✓` : "↳ starting…";
              item(s, [line(s, frame, col, now - (s.created || now)), text(theme.text, "  " + cut(detail, WIDTH - 2))]);
            }
            // done: one line each; the detail line only when expanded
            for (const s of view.all ? done : recent) {
              const body = [line(s, s.error ? "✗" : "✓", s.error ? theme.error : theme.success,
                                 (s.done || s.updated || now) - (s.created || now))];
              if (view.expanded === s.id) {
                const calls = s.calls?.size || 0;
                body.push(s.error ? text(theme.error, "  " + cut(`↳ ${s.error}`, WIDTH - 2))
                                  : text(theme.textMuted, "  " + cut(`↳ ${calls} tool${calls === 1 ? "" : "s"}`, WIDTH - 2)));
              }
              item(s, body);
            }
            if (more) rows.push(box({ width: "100%", onMouseDown: () => { view.all = !view.all; bump(); } },
                                    [text(theme.textMuted, "  " + (view.all ? "− fewer" : `+${more} more`))]));
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
