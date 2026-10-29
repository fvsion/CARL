// @ts-check
// Server half of the plugin: nothing to do server-side; the panel lives in
// tui.js (OpenCode loads it through exports["./tui"]).
/** @type {import("@opencode-ai/plugin").PluginModule & { id: string, setup: () => void }} */
export default { id: "subagents-sidebar", setup() {}, server: async () => ({}) };
