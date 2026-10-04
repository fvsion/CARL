// @ts-check
// Server half of the plugin: nothing to do server-side; the panel lives in tui.js (OpenCode loads it
// through exports["./tui"]).
/** @type {import("@opencode-ai/plugin").PluginModule & { id: string }} */
export default { id: "carl-panel", server: async () => ({}) };
