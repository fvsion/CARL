// @ts-check
// The /carl panel for OpenCode (TUI plugin, installed by CARL's client/install.sh): a control panel (carl-panel.js).
// One list: each row is a CARL part's label and its state at the right ("Coder subagent   on"). Enter changes it
// in place: the state reads "turning off…" while the setup runs, then the new state, and a toast says what CARL did
// (red on an error; a warning when the coder goes on with 1 slot); the title says when OpenCode must restart. Web
// search opens its values; Sync service opens a view of its state. The list is a signal, so a change keeps the
// cursor where it is.
import { createSignal } from "solid-js";
import { act, panel, viewText } from "./carl-panel.js";

/** @typedef {import("@opencode-ai/plugin/tui").TuiPluginApi} TuiPluginApi */
/** @typedef {import("@opencode-ai/plugin/tui").TuiPluginModule} TuiPluginModule */
/** @typedef {import("./carl-panel.js").Row} Row */
/** @typedef {import("./carl-panel.js").Action} Action */

/** @param {TuiPluginApi} api */
function controlPanel(api) {
  const [now, setNow] = createSignal(panel("opencode"));
  /** @type {Map<string, string>} */
  const busy = new Map();                    // row id -> its state while an action runs
  let running = false;                       // one action at a time
  const refresh = () => setNow(panel("opencode"));

  /** @param {Row} r @param {Action} a */
  const run = async (r, a) => {
    if (running) return;
    running = true;
    busy.set(r.id, a.busy);
    refresh();
    try {
      const said = await act(a, "opencode");
      api.ui.toast({ message: said.message, variant: !said.ok ? "error" : said.warn ? "warning" : "success" });
    } finally {
      busy.delete(r.id);
      running = false;
      refresh();
    }
  };

  /** @param {string} id */
  const choose = (id) => {
    const r = now().rows.find((x) => x.id === id);
    if (!r || running) return;
    if (r.kind === "switch") {
      const a = Object.values(r.actions ?? {})[0];
      if (a) void run(r, a);
    } else if (r.kind === "action" && r.action) {
      void run(r, r.action);
    } else if (r.kind === "choice") {
      api.ui.dialog.replace(() => api.ui.DialogSelect({
        title: r.label,
        options: (r.values ?? []).map((v) => ({ title: v, value: v, description: r.notes?.[v] })),
        current: r.state,
        onSelect: (opt) => {
          const a = r.actions?.[String(opt.value)];
          open();
          if (a) void run(r, a);
        },
      }));
    } else if (r.kind === "view") {
      api.ui.dialog.replace(() => api.ui.DialogAlert({ title: r.viewTitle ?? r.label, message: viewText(r.view ?? []), onConfirm: open }));
    }
  };

  const open = () => {
    refresh();
    api.ui.dialog.replace(() => api.ui.DialogSelect({
      get title() {
        const t = now().title;
        return t ? `CARL   ${t}` : "CARL";
      },
      placeholder: "Search",
      get options() {
        return now().rows.map((r) => ({ title: r.label, value: r.id, footer: busy.get(r.id) ?? r.state }));
      },
      onSelect: (opt) => choose(String(opt.value)),
    }));
  };
  return open;
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
