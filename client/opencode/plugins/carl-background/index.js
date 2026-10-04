// @ts-check
// CARL: the coder subagent runs in the background (installed by CARL's client/install.sh with background subagents
// on, the default; NO_BACKGROUND_SUBAGENTS=1 leaves it out). OpenCode's task tool waits for a subagent unless the
// model asks for the background, and local models often don't, even when told to: the main session then sits idle
// in its slot while the coder works in the other. This sets background: true on a task call for CARL's coder that
// doesn't say; the model can still ask for the foreground with background: false. Its result comes back to the
// main session as a message when the coder ends. A task that continues an earlier one (task_id) is left as asked.

const CODERS = new Set(["coder", "carl-coder"]);   // CARL's coder: "carl-coder" next to a user's own "coder"

/** @type {import("@opencode-ai/plugin").PluginModule & { id: string }} */
export default {
  id: "carl-background",
  server: async () => ({
    "tool.execute.before": async (input, output) => {
      const args = /** @type {Record<string, unknown> | undefined} */ (output?.args);
      if (input?.tool !== "task" || !args || !CODERS.has(String(args.subagent_type))) return;
      if (args.background === undefined && !args.task_id) args.background = true;
    },
  }),
};
