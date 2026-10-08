// @ts-check
// The /carl panel for OpenCode (TUI plugin, installed by CARL's client/install.sh): a control panel
// (carl-panel.js). The list has one row for each CARL part: its label and its state ("Coder subagent   on"):
// the config sync, new configs at once, the coder, the background coder, the delegation reminder, the browser,
// web search, LSP, the subagents side panel, the session switcher, the disk cache and the model check.
// OpenCode's dialogs are lists, so a row opens its own dialog: its actions first ("▸ Turn it off"), then what
// the part does, then "‹ back", then the details (the setup switch, addresses, versions) under their own
// heading. A switch runs `carl-sync.py set`; a toast says what changed and what must restart.
import { act, sections } from "./carl-panel.js";

/** @typedef {import("@opencode-ai/plugin/tui").TuiPluginApi} TuiPluginApi */
/** @typedef {import("@opencode-ai/plugin/tui").TuiPluginModule} TuiPluginModule */

const BACK = "‹ back";   // not exported: OpenCode may call every export of an entry module

/** @param {TuiPluginApi} api */
function panel(api) {
  let running = false;                       // one action at a time: a second select waits for the first
  const top = () => {
    const all = sections("opencode");
    api.ui.dialog.replace(() => api.ui.DialogSelect({
      title: "CARL",
      placeholder: "Filter",
      options: all.map((s) => ({ title: s.row, value: s.id, description: "" })),
      onSelect: (opt) => section(String(opt.value)),
    }));
  };
  /** @param {string} id */
  const section = (id) => {
    const s = sections("opencode").find((x) => x.id === id);
    if (!s) return top();
    api.ui.dialog.replace(() => api.ui.DialogSelect({
      title: `CARL › ${s.title}`,
      options: [
        ...s.actions.map((a, i) => ({ title: `▸ ${a.label}`, value: `act:${i}`, description: "" })),
        ...s.lines.map((l, i) => ({ title: l, value: `line:${i}`, description: "" })),
        { title: BACK, value: "back", description: "" },
        ...s.details.map((l, i) => ({ title: l, value: `detail:${i}`, description: "", category: "Details" })),
      ],
      onSelect: async (opt) => {
        const v = String(opt.value);
        if (v === "back") return top();
        if (!v.startsWith("act:") || running) return;
        const a = s.actions[Number(v.slice(4))];
        if (!a) return;
        running = true;
        try {
          if (a.busy) api.ui.toast({ message: a.busy, variant: "info" });
          const said = await act(a, "opencode");
          api.ui.toast({ message: said.message, variant: said.ok ? "success" : "error" });
        } finally {
          running = false;
        }
        section(id);
      },
    }));
  };
  return top;
}

/** @type {TuiPluginModule & { id: string }} */
const plugin = {
  id: "carl-panel:tui",
  tui: async (api) => {
    const open = panel(api);
    const unreg = api.command?.register?.(() => [{
      title: "CARL", value: "carl.panel", category: "CARL",
      description: "Turn the CARL parts on this computer on or off: the coder, the tools, the config sync",
      slash: { name: "carl" },
      onSelect: () => open(),
    }]);
    if (unreg) api.lifecycle.onDispose(unreg);
  },
};

export default plugin;
