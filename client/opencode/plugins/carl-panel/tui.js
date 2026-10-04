// The /carl panel for OpenCode (TUI plugin, installed by CARL's client/install.sh): every CARL piece on this
// computer with its state (carl-panel.js), one section per piece: the client config sync first (its
// auto-apply switch, a config that waits, a check now), then the prompt cache, the model check, the session
// switcher, the subagents sidebar, the coder, the browser, web search and LSP. OpenCode's dialogs are lists,
// so the sections are a list and each one opens its own dialog (the nearest to tabs).
import { run, sections } from "./carl-panel.js";

/** @typedef {import("@opencode-ai/plugin/tui").TuiPluginApi} TuiPluginApi */
/** @typedef {import("@opencode-ai/plugin/tui").TuiPluginModule} TuiPluginModule */

/** @param {TuiPluginApi} api */
function panel(api) {
  const top = () => {
    const all = sections("opencode");
    api.ui.dialog.replace(() => api.ui.DialogSelect({
      title: "CARL",
      placeholder: "Filter",
      options: all.map((s) => ({ title: s.title, value: s.id, description: s.summary })),
      onSelect: (opt) => section(String(opt.value)),
    }));
  };
  /** @param {string} id */
  const section = (id) => {
    const s = sections("opencode").find((x) => x.id === id);
    if (!s) return top();
    api.ui.dialog.replace(() => api.ui.DialogSelect({
      title: `CARL · ${s.title}`,
      options: [
        ...s.actions.map((a, i) => ({ title: `▸ ${a.label}`, value: `act:${i}`, description: "" })),
        ...s.lines.map((l, i) => ({ title: l, value: `line:${i}`, description: "" })),
        { title: "‹ back to every CARL piece", value: "back", description: "" },
      ],
      onSelect: async (opt) => {
        const v = String(opt.value);
        if (v === "back") return top();
        if (!v.startsWith("act:")) return;
        const a = s.actions[Number(v.slice(4))];
        api.ui.toast({ message: `CARL: ${a.label.toLowerCase()}…`, variant: "info" });
        const code = await run(a.args);
        api.ui.toast({ message: code === 0 ? `CARL: done` : `CARL: ${a.label.toLowerCase()} failed (see /carl)`,
                       variant: code === 0 ? "success" : "error" });
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
      description: "Every CARL piece on this computer: the config sync, the prompt cache, the plugins, the tools",
      slash: { name: "carl" },
      onSelect: () => open(),
    }]);
    if (unreg) api.lifecycle.onDispose(unreg);
  },
};

export default plugin;
