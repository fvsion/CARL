// @ts-check
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
import { asElement, cut, nodes, routeSessionID, rows } from "./carl-tui.js";

/** @typedef {import("@opencode-ai/plugin/tui").TuiPluginModule} TuiPluginModule */
/** @typedef {import("./carl-tui.js").Theme} Theme */
/** @typedef {import("./carl-tui.js").Color} Color */
/** @typedef {import("./carl-tui.js").Node} Node */
/** @typedef {import("@opencode-ai/sdk/v2").Session} Session */
/** @typedef {import("@opencode-ai/sdk/v2").AssistantMessage} AssistantMessage */
/** @typedef {import("@opencode-ai/sdk/v2").ToolPart} ToolPart */
/** @typedef {"busy" | "retry" | "idle"} RunStatus */
/**
 * One subagent (child session) as the panel shows it.
 * @typedef {object} Subagent
 * @property {string} id
 * @property {string} parentID
 * @property {RunStatus} status     "idle" means finished, at `done`
 * @property {number} [done]        when it finished
 * @property {string} [error]       why it failed, if it did
 * @property {number} tokens        context size of its last assistant message
 * @property {string} [model]
 * @property {string} [tool]        the tool call running right now
 * @property {string} [lastTool]    the last tool call it made
 * @property {Set<string>} [calls]  finished tool calls
 * @property {string} task
 * @property {string} agent
 * @property {number} created
 * @property {number} updated
 */

const WIDTH = 32;                       // usable columns in OpenCode's sidebar (measured at 170-col terminal)
const DONE_MAX = 5;                     // finished subagents listed under the running ones
const DONE_TTL = 5 * 60 * 1000;         // ...and only for this long after they finish
const SPIN = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

const { box, text } = nodes({ createElement, insert, setProp });

/** @param {number} ms @returns {string} e.g. 42s, 1m08s, 2h05m */
function dur(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  return s >= 3600 ? `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`
       : s >= 60 ? `${Math.floor(s / 60)}m${String(s % 60).padStart(2, "0")}s` : `${s}s`;
}
/** @param {number} n @returns {string} e.g. 950, 5.1k, 12k */
function kfmt(n) {
  return n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n || 0);
}

/**
 * Message / session errors are objects ({ name, data.message }), tool errors strings.
 * @param {unknown} err
 * @returns {string | undefined}
 */
function errText(err) {
  if (!err) return undefined;
  if (typeof err === "string") return err;
  const e = /** @type {{ name?: string, data?: { message?: string } }} */ (err);
  if (e.name === "MessageAbortedError") return "aborted";
  return e.data?.message || e.name || "error";
}

/**
 * OpenCode titles child sessions "<description> (@<agent> subagent)".
 * @param {string | undefined} title
 * @returns {{ task: string, agent: string }}
 */
function parseTitle(title) {
  const m = /^(.*?)\s*\(@([\w-]+) subagent\)\s*$/.exec(title || "");
  return m ? { task: m[1] || "subagent", agent: m[2] } : { task: title || "subagent", agent: "" };
}

/** "<tool> <main argument>", e.g. "read /src/a.ts". @param {ToolPart} part @returns {string} */
function toolSummary(part) {
  const st = part.state || {};
  const input = st.input || {};
  const arg = ("title" in st ? st.title : undefined) || input.description || input.filePath || input.path ||
              input.pattern || input.command || input.query || input.url || "";
  return arg ? `${part.tool} ${arg}` : part.tool;
}

/** Context size: everything the model saw plus what it wrote. @param {Partial<AssistantMessage["tokens"]> | undefined} t */
function contextTokens(t = {}) {
  return (t.input || 0) + (t.cache?.read || 0) + (t.cache?.write || 0) + (t.output || 0);
}

/** @param {Subagent} s @param {AssistantMessage} info */
function applyAssistant(s, info) {
  s.tokens = contextTokens(info.tokens);
  s.model = info.modelID || s.model;
  s.error = errText(info.error);
}

/** @param {Subagent} s @param {ToolPart} part */
function countFinishedCall(s, part) {
  if (part.state?.status === "completed" || part.state?.status === "error") (s.calls ??= new Set()).add(part.callID || part.id);
}

/** Busy or retrying; a finished one that runs again was resumed (task_id). @param {Subagent} s @param {RunStatus} type */
function run(s, type) {
  if (s.status === "idle") { s.done = undefined; s.error = undefined; }
  s.status = type;
}
/** Finished, at `at` (or now); an earlier finish time is kept. @param {Subagent} s @param {number} [at] */
function finish(s, at) {
  if (s.status !== "idle") { s.status = "idle"; s.done = at ?? Date.now(); }
  else s.done ??= at ?? Date.now();
}

/**
 * The v1 SDK's request shape ({ path: { id } }), for TUI clients older than
 * the v2 one (flat { sessionID }) that the installed types describe.
 * @param {string} id
 * @returns {{ sessionID: string }}
 */
function v1Params(id) {
  return /** @type {{ sessionID: string }} */ (/** @type {unknown} */ ({ path: { id } }));
}

/**
 * Running ones oldest first; finished ones newest first, and the few that stay
 * listed (recently finished, or the one on screen).
 * @param {Subagent[]} mine
 * @param {string} sid   the session on screen
 * @param {number} now
 */
function partition(mine, sid, now) {
  const running = mine.filter((s) => s.status !== "idle").sort((a, b) => (a.created || 0) - (b.created || 0));
  const done = mine.filter((s) => s.status === "idle").sort((a, b) => (b.done || 0) - (a.done || 0));
  const recent = done.filter((s, i) => s.id === sid || (i < DONE_MAX && now - (s.done || 0) < DONE_TTL));
  return { running, done, recent };
}

/**
 * "▼ Subagents · 2 running · 3✓ 1✗"
 * @param {boolean} collapsed @param {number} running @param {Subagent[]} done
 */
function headline(collapsed, running, done) {
  const failed = done.filter((s) => s.error).length;
  const ok = done.length - failed;
  const tally = [ok ? `${ok}✓` : "", failed ? `${failed}✗` : ""].filter(Boolean).join(" ");
  return [`${collapsed ? "▶" : "▼"} Subagents`, running ? `${running} running` : "", tally].filter(Boolean).join(" · ");
}

/** @param {Subagent} s @returns {string} */
function runningDetail(s) {
  return s.status === "retry" ? "↳ retrying…" : s.tool ? `↳ ${s.tool}` : s.lastTool ? `↳ ${s.lastTool} ✓` : "↳ starting…";
}

/** @type {TuiPluginModule & { id: string }} */
const plugin = {
  id: "subagents-sidebar:tui",
  tui: async (api) => {
    /** @type {Map<string, Subagent>} */
    const subs = new Map();
    /** @type {Set<string>} parent sessions whose children were fetched */
    const loaded = new Set();
    const [tick, setTick] = createSignal(0);
    // view state; reset (except collapsed) when another parent session is shown
    /** @type {{ parent: string | undefined, collapsed: boolean, expanded: string | null, all: boolean }} */
    const view = { parent: undefined, collapsed: false, expanded: null, all: false };
    const bump = () => { setTick((t) => t + 1); api.renderer.requestRender(); };

    /**
     * @param {Session | undefined} session
     * @param {boolean} created  true for session.created: a new one is starting
     * @returns {boolean} true for a subagent (the list changed)
     */
    const upsert = (session, created) => {
      if (!session?.parentID) return false;
      let cur = subs.get(session.id);
      if (!cur) {
        // First sight: a just-created one is starting; anything else (loaded
        // later, or updated after the fact) takes OpenCode's own status.
        const status = api.state.session.status(session.id)?.type ?? (created ? "busy" : "idle");
        cur = { id: session.id, parentID: session.parentID, status, tokens: 0, task: "", agent: "",
                created: Date.now(), updated: Date.now() };
        if (status === "idle") cur.done = session.time?.updated ?? Date.now();
        subs.set(session.id, cur);
      }
      const { task, agent } = parseTitle(session.title);
      Object.assign(cur, { parentID: session.parentID, task, agent: agent || session.agent || cur.agent || "",
                           created: session.time?.created ?? cur.created,
                           updated: session.time?.updated ?? Date.now() });
      return true;
    };

    // One subagent's history (after a restart or reopening a session): tool
    // calls, last context size, model, error, completion time.
    /** @param {Subagent} s */
    const history = async (s) => {
      try {
        let res = await api.client.session.messages({ sessionID: s.id });
        if (res?.error) res = await api.client.session.messages(v1Params(s.id));
        for (const m of rows(res)) {
          const info = m.info;
          if (info?.role === "assistant") {
            applyAssistant(s, info);
            if (info.time?.completed && s.status === "idle") s.done = info.time.completed;
          }
          for (const p of m.parts || []) {
            if (p.type !== "tool") continue;
            s.lastTool = toolSummary(p);
            countFinishedCall(s, p);
          }
        }
        bump();
      } catch { /* the live events still fill it in */ }
    };

    /** @param {string} parentID */
    const load = async (parentID) => {
      if (loaded.has(parentID)) return;
      loaded.add(parentID);
      try {
        let res = await api.client.session.children({ sessionID: parentID });
        if (res?.error) res = await api.client.session.children(v1Params(parentID));
        for (const session of rows(res)) {
          if (!upsert(session, false)) continue;
          const s = subs.get(session.id);
          if (s) history(s);
        }
        bump();
      } catch {
        loaded.delete(parentID);       // retry on the next render
      }
    };

    /**
     * The parent's `task` call for a subagent: it returns when the subagent's
     * run ends (unless it was sent to the background, which returns at once).
     * @param {ToolPart} p
     * @returns {boolean} true when it finished a subagent
     */
    const taskPart = (p) => {
      const st = p.state;
      const meta = st && "metadata" in st ? st.metadata : undefined;
      const id = meta?.sessionId;
      const s = typeof id === "string" ? subs.get(id) : undefined;
      if (!s || meta?.background) return false;
      if (st.status === "completed") finish(s, st.time?.end);
      else if (st.status === "error") { finish(s, st.time?.end); s.error = errText(st.error) || "failed"; }
      else return false;
      return true;
    };

    /** @param {ToolPart} p */
    const toolPart = (p) => {
      const ended = p.tool === "task" && taskPart(p);
      const s = subs.get(p.sessionID);
      if (!s) { if (ended) bump(); return; }
      const st = p.state?.status;
      if (st === "running" || st === "pending") s.tool = toolSummary(p);
      else {
        s.lastTool = toolSummary(p);
        if (s.tool && s.tool.startsWith(p.tool)) s.tool = undefined;
        countFinishedCall(s, p);
      }
      bump();
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
        const s = e.properties.sessionID ? subs.get(e.properties.sessionID) : undefined;
        if (s) { s.error = errText(e.properties.error) || "error"; bump(); }
      }),
      api.event.on("message.updated", (e) => {
        const m = e.properties.info;
        const s = m && subs.get(m.sessionID);
        if (!s || m.role !== "assistant") return;
        applyAssistant(s, m);
        bump();
      }),
      api.event.on("message.part.updated", (e) => {
        const p = e.properties.part;
        if (p?.type === "tool") toolPart(p);
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

    /**
     * The panel for the subagents of `parent`, or null when it has none.
     * @param {string} sid     the session on screen
     * @param {string} parent  the session whose subagents are listed
     * @param {Theme} theme
     * @returns {Node | null}
     */
    const panel = (sid, parent, theme) => {
      const mine = [...subs.values()].filter((s) => s.parentID === parent);
      if (!mine.length) return null;
      const now = Date.now();
      const { running, done, recent } = partition(mine, sid, now);
      const more = done.length - recent.length;
      /** @type {Node[]} */
      const rowsOut = [box({ width: "100%", onMouseDown: () => { view.collapsed = !view.collapsed; bump(); } },
                          [text(theme.accent, cut(headline(view.collapsed, running.length, done), WIDTH))])];
      const column = () => box({ width: "100%", flexDirection: "column", paddingBottom: 1 }, rowsOut);
      if (view.collapsed) return column();

      /** first line: icon, agent, task, time on the right @param {Subagent} s @param {string} icon @param {Color} col @param {number} ms */
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
      /** @param {Color} col @param {string} value */
      const detail = (col, value) => text(col, "  " + cut(value, WIDTH - 2));
      /** a clickable entry: the first click expands it, the second opens it @param {Subagent} s @param {Node[]} body */
      const item = (s, body) => {
        if (view.expanded === s.id) {
          body.push(detail(theme.textMuted, `${s.model || "?"} · ${kfmt(s.tokens)} ctx`));
          body.push(detail(theme.accent, "open: click again"));
        }
        rowsOut.push(box({
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
        item(s, [line(s, frame, col, now - (s.created || now)), text(theme.text, "  " + cut(runningDetail(s), WIDTH - 2))]);
      }
      // done: one line each; the detail line only when expanded
      for (const s of view.all ? done : recent) {
        const body = [line(s, s.error ? "✗" : "✓", s.error ? theme.error : theme.success,
                           (s.done || s.updated || now) - (s.created || now))];
        if (view.expanded === s.id) {
          const calls = s.calls?.size || 0;
          body.push(s.error ? detail(theme.error, `↳ ${s.error}`)
                            : detail(theme.textMuted, `↳ ${calls} tool${calls === 1 ? "" : "s"}`));
        }
        item(s, body);
      }
      if (more) rowsOut.push(box({ width: "100%", onMouseDown: () => { view.all = !view.all; bump(); } },
                                 [text(theme.textMuted, "  " + (view.all ? "− fewer" : `+${more} more`))]));
      return column();
    };

    api.slots.register({
      order: 50,                                    // near the top of the sidebar
      slots: {
        sidebar_content: (_ctx, props) => {
          tick();                                   // re-render on every update
          const theme = api.theme.current;
          try {
            const sid = props?.session_id ?? routeSessionID(api);
            if (!sid) return null;
            // In a subagent's own session, show its siblings under the same parent.
            const parent = subs.get(sid)?.parentID ?? api.state.session.get(sid)?.parentID ?? sid;
            if (parent !== view.parent) Object.assign(view, { parent, expanded: null, all: false });
            load(parent);
            return asElement(panel(sid, parent, theme));
          } catch (err) {
            return asElement(text(theme.error, cut(`subagents-sidebar: ${err}`, WIDTH)));
          }
        },
      },
    });
  },
};

export default plugin;
