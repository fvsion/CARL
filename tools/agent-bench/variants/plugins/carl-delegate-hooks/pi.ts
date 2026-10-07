/**
 * CARL (Phase 23 variants V4 / V5): the delegation hooks in Pi (delegate-hooks.js has the rules). Only the main
 * session: CARL's subagent tool runs a subagent as its own Pi process with CARL_AGENT set, where this does nothing.
 */
import { existsSync } from "node:fs";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Turn, on, withReminder } from "./delegate-hooks.js";

const MODE: string = "__MODE__";

export default function carlDelegateHooks(pi: ExtensionAPI) {
	if (process.env.CARL_AGENT) return;
	const turn = new Turn();
	// V2 (rough): the reminder at the end of every user message (stable text: cache-safe)
	pi.on("input", async (event) => {
		if (!on(MODE, "remind") || !event.text.trim() || event.text.trimStart().startsWith("/")) return { action: "continue" as const };
		return { action: "transform" as const, text: withReminder(event.text, "pi"), images: event.images };
	});
	pi.on("before_agent_start", async () => {
		turn.reset();
		return undefined;
	});
	pi.on("tool_call", async (event, ctx) => {
		const why = turn.before(MODE, "pi", String(event.toolName), (event.input ?? {}) as Record<string, unknown>,
			String(ctx.cwd ?? process.cwd()), existsSync);
		return why ? { block: true, reason: why } : undefined;
	});
	pi.on("tool_result", async (event) => {
		const add = turn.after(MODE, "pi", String((event as { toolName?: string }).toolName ?? ""));
		if (!add) return undefined;
		return { content: [...event.content, { type: "text" as const, text: add }], structuredContent: event.structuredContent };
	});
}
