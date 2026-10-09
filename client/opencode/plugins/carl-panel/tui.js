// @ts-check
// The /carl panel for OpenCode (TUI plugin, installed by CARL's client/install.sh): a control panel (carl-panel.js).
// One list: each row is a CARL part's label and its state at the right ("Coder subagent   on ›": the "›" says that it
// opens a list). Enter changes it in place: the state reads "turning off…" while the setup runs, then the new state,
// and a toast says what CARL did (red on an error; a warning when the coder goes on with 1 slot); the title says when
// OpenCode must restart. Web search opens its values; Coder subagent opens its own list (Coder, Background coder,
// Delegation reminder, Coder thinking, Coder model), and Coder thinking its values (● the current one) for the model
// of the session /carl is opened in; a choice with one value opens nothing; Sync service opens a view of its state.
// The lists are signals, so a change keeps the cursor where it is.
//
// Esc goes back one list (user, 2026-10-09: "I would like to be able to go back too"); Esc on the top list closes
// /carl. OpenCode's dialog stack (1.18) holds one dialog: replace() closes the one shown (its onClose runs) and shows
// the new one; Esc runs the shown dialog's onClose, then empties the stack. So a list that /carl opened from another
// one shows the parent list again from its onClose, after OpenCode's Esc is done (a timer), and only when the key was
// Esc (read from the renderer's key input: ctrl+c and a click outside close /carl, as they close OpenCode's own
// dialogs) and nothing else was opened since.
import { createSignal } from "solid-js";
import { act, panel, rowsAt, shownState, viewText } from "./carl-panel.js";

/** @typedef {import("@opencode-ai/plugin/tui").TuiPluginApi} TuiPluginApi */
/** @typedef {import("@opencode-ai/plugin/tui").TuiPluginModule} TuiPluginModule */
/** @typedef {import("./carl-panel.js").Row} Row */
/** @typedef {import("./carl-panel.js").Action} Action */
/** @typedef {import("./carl-panel.js").Session} Session */

/** How long after an Esc key a closed list counts as closed by it (ms). */
const ESC_WINDOW = 500;

/**
 * The model of the session /carl is opened in ("provider/model"): the newest message of the main session (a
 * subagent's session: its parent's) with a model; undefined on the home screen or before the first message.
 * @param {TuiPluginApi} api @returns {string | undefined}
 */
function sessionModel(api) {
  const r = api.route?.current;
  if (!r || r.name !== "session") return undefined;
  const id = String(/** @type {{ sessionID?: unknown }} */ (r.params ?? {}).sessionID ?? "");
  const chain = [];                          // the session, then its parents (the main session last)
  for (let s = id; s && chain.length < 8 && !chain.includes(s);) {
    chain.push(s);
    s = String(api.state?.session?.get?.(s)?.parentID ?? "");
  }
  for (const s of chain.reverse()) {         // the main session first; the session itself when its parent is not loaded
    const msgs = api.state?.session?.messages?.(s) ?? [];
    for (let i = msgs.length - 1; i >= 0; i--) {
      const m = /** @type {{ role?: string, model?: { providerID?: string, modelID?: string }, providerID?: string,
                             modelID?: string }} */ (msgs[i]);
      if (m.role === "user" && m.model?.providerID && m.model.modelID) return `${m.model.providerID}/${m.model.modelID}`;
      if (m.role === "assistant" && m.providerID && m.modelID) return `${m.providerID}/${m.modelID}`;
    }
  }
  return undefined;
}

/** @param {TuiPluginApi} api */
function controlPanel(api) {
  /** @type {Session} */
  let session = {};
  const [now, setNow] = createSignal(panel("opencode"));
  /** @type {Map<string, string>} */
  const busy = new Map();                    // row id -> its state while an action runs
  let running = false;                       // one action at a time
  const refresh = () => setNow(panel("opencode", session));

  // the time of the last key press when it was Esc, else 0 (the renderer's key input; the dialog stack's own Esc runs
  // in the same key press). One Esc goes back one list at most.
  let lastEsc = 0;
  const keys = /** @type {any} */ (api.renderer)?.keyInput;
  if (keys && typeof keys.prependListener === "function") {
    const onKey = (/** @type {{ name?: string }} */ k) => {
      lastEsc = k?.name === "escape" ? Date.now() : 0;
    };
    keys.prependListener("keypress", onKey);
    api.lifecycle?.onDispose?.(() => keys.off?.("keypress", onKey));
  }

  let shown = 0;                             // the dialog /carl shows now: an older one's onClose does nothing
  /**
   * Show a dialog; `back` shows the list it was opened from when Esc closes it (none: the top list, Esc closes /carl).
   * @param {() => any} render @param {(() => void) | undefined} back
   */
  const show = (render, back) => {
    const n = ++shown;
    api.ui.dialog.replace(render, () => {
      if (n !== shown || !back) return;      // /carl showed its next dialog, or the top list closed
      setTimeout(() => {
        // Esc, and nothing opened since (a dialog of OpenCode's own, or /carl again)
        if (n !== shown || (api.ui.dialog.depth ?? 0) > 0 || !lastEsc || Date.now() - lastEsc > ESC_WINDOW) return;
        lastEsc = 0;
        back();
      }, 0);
    });
  };

  /** @param {Row} r @param {Action} a */
  const run = async (r, a) => {
    if (running) return;
    running = true;
    busy.set(r.id, a.busy);
    refresh();
    try {
      const said = await act(a, "opencode", session);
      api.ui.toast({ message: said.message, variant: !said.ok ? "error" : said.warn ? "warning" : "success" });
    } finally {
      busy.delete(r.id);
      running = false;
      refresh();
    }
  };

  /** The title of the list at `path`, with the restart note. @param {string[]} path */
  const titleOf = (path) => {
    let name = "CARL";
    let rows = now().rows;
    for (const id of path) {
      const r = rows.find((x) => x.id === id);
      name = r?.listTitle ?? r?.label ?? name;
      rows = r?.rows ?? [];
    }
    const t = now().title;
    return t ? `${name}   ${t}` : name;
  };

  /** Enter on a row of the list at `path`. @param {string} id @param {string[]} path */
  const choose = (id, path) => {
    const r = rowsAt(now().rows, path)?.find((x) => x.id === id);
    if (!r || running) return;
    if (r.kind === "switch") {
      const a = Object.values(r.actions ?? {})[0];
      if (a) void run(r, a);
    } else if (r.kind === "action" && r.action) {
      void run(r, r.action);
    } else if (r.kind === "list") {
      open([...path, r.id]);
    } else if (r.kind === "choice" && (r.values ?? []).length > 1) {
      show(() => api.ui.DialogSelect({
        title: r.choiceTitle ?? r.label,
        options: (r.values ?? []).map((v) => ({ title: r.titles?.[v] ?? v, value: v, description: r.notes?.[v] })),
        current: r.value ?? r.state,
        onSelect: (opt) => {
          const a = r.actions?.[String(opt.value)];
          open(path);
          if (a) void run(r, a);
        },
      }), () => open(path));
    } else if (r.kind === "view") {
      show(() => api.ui.DialogAlert({ title: r.viewTitle ?? r.label, message: viewText(r.view ?? []),
                                      onConfirm: () => open(path) }), () => open(path));
    }
  };

  /** The list at `path` ([]: the top list; a list that is gone now: the top list). @param {string[]} [path] */
  const open = (path = []) => {
    refresh();
    const at = rowsAt(now().rows, path) ? path : [];
    show(() => api.ui.DialogSelect({
      get title() {
        return titleOf(at);
      },
      ...(at.length ? {} : { placeholder: "Search" }),
      get options() {
        return (rowsAt(now().rows, at) ?? []).map((r) => ({ title: r.label, value: r.id, footer: shownState(r, busy.get(r.id)) }));
      },
      onSelect: (opt) => choose(String(opt.value), at),
    }), at.length ? () => open(at.slice(0, -1)) : undefined);
  };
  return () => {
    session = { model: sessionModel(api) };  // the session /carl is opened in: its model, for Coder thinking
    open();
  };
}

/** @type {TuiPluginModule & { id: string }} */
const plugin = {
  id: "carl-panel:tui",
  tui: async (api) => {
    const open = controlPanel(api);
    const unreg = api.command?.register?.(() => [{
      title: "CARL", value: "carl.panel", category: "CARL",
      description: "Turn the CARL parts on this computer on or off",
      slash: { name: "carl" },
      onSelect: () => open(),
    }]);
    if (unreg) api.lifecycle.onDispose(unreg);
  },
};

export default plugin;
