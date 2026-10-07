/**
 * CARL: the hand-off to the coder in Pi (installed by CARL's client setup with the coder). The rules are in
 * carl-delegation.js (shared with the OpenCode plugin). Phase 23 (1.8.0):
 *   - the reminder (carl.json "delegation".reminder, on unless false): every user message ends with one line about
 *     the coder (the same text each time, so the prompt cache stays whole);
 *   - the gate (carl.json "delegation".gate: the number of the new file it stops at; 0: off).
 * Only the main session: CARL's subagent tool runs a subagent as its own Pi process with CARL_AGENT set, where this
 * does nothing (the subagent tool also keeps APPEND_SYSTEM.md, with the delegation rule, from subagents).
 */
import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Turn, gateNumber, withReminder } from "./carl-delegation.js";

/** CARL's settings for the hand-off (carl.json in Pi's agent folder). */
function settings(): { reminder: boolean; gate: number; coder: string } {
	try {
		const dir = process.env.PI_CODING_AGENT_DIR || join(homedir(), ".pi", "agent");
		const st = JSON.parse(readFileSync(join(dir, "carl.json"), "utf8")) as Record<string, unknown>;
		const d = (st.delegation ?? {}) as Record<string, unknown>;
		return {
			reminder: d.reminder !== false,
			gate: gateNumber(d.gate),
			coder: typeof st.coder_agent === "string" && st.coder_agent ? st.coder_agent : "coder",
		};
	} catch {
		return { reminder: true, gate: 0, coder: "coder" };
	}
}

export default function carlDelegation(pi: ExtensionAPI) {
	if (process.env.CARL_AGENT) return; // a subagent: no reminder, no gate
	const set = settings();
	const turn = new Turn();
	pi.on("input", async (event) => {
		if (!set.reminder || !event.text.trim() || event.text.trimStart().startsWith("/")) return { action: "continue" as const };
		return { action: "transform" as const, text: withReminder(event.text, "pi", set.coder), images: event.images };
	});
	pi.on("before_agent_start", async () => {
		turn.reset();
		return undefined;
	});
	pi.on("tool_call", async (event, ctx) => {
		if (!set.gate) return undefined;
		const why = turn.before(set.gate, "pi", String(event.toolName), (event.input ?? {}) as Record<string, unknown>,
			String(ctx.cwd ?? process.cwd()), existsSync, set.coder);
		return why ? { block: true, reason: why } : undefined;
	});
}
