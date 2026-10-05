// @ts-check
// The /carl panel for OpenCode (TUI plugin, installed by CARL's client/install.sh): every CARL part on this
// computer with its state (carl-panel.js), one section per part: the client config sync first (whether new
// configs are applied at once, a config that waits, a check now), then the disk cache, the model check, the
// session switcher, the subagents sidebar, the coder, the browser, web search and LSP. OpenCode's dialogs are
// lists, so the sections are a list and each one opens its own dialog (the nearest to tabs): its actions and
// sentences first, then "‹ back", then the details (addresses, versions, the installer's switches) under
// their own heading.
import { outcome, run, sections } from "./carl-panel.js";

/** @typedef {import("@opencode-ai/plugin/tui").TuiPluginApi} TuiPluginApi */
/** @typedef {import("@opencode-ai/plugin/tui").TuiPluginModule} TuiPluginModule */

const BACK = "‹ back";   // not exported: OpenCode may call every export of an entry module

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
        { title: BACK, value: "back", description: "" },
        ...s.details.map((l, i) => ({ title: l, value: `detail:${i}`, description: "", category: "Details" })),
      ],
      onSelect: async (opt) => {
        const v = String(opt.value);
        if (v === "back") return top();
        if (!v.startsWith("act:")) return;
        const a = s.actions[Number(v.slice(4))];
        if (!a) return;
        if (a.busy) api.ui.toast({ message: a.busy, variant: "info" });
        const said = outcome(a, await run(a.args), "opencode");
        api.ui.toast({ message: said.message, variant: said.ok ? "success" : "error" });
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
      description: "The CARL parts on this computer and their state: config sync, disk cache, tools",
      slash: { name: "carl" },
      onSelect: () => open(),
    }]);
    if (unreg) api.lifecycle.onDispose(unreg);
  },
};

export default plugin;
