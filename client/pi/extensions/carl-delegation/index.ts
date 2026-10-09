/**
 * CARL: the hand-off to the coder in Pi (installed by CARL's client setup with the coder). The rules are in
 * carl-delegation.js and carl-brief.js (shared with the OpenCode plugin). Phase 23 (1.8.0):
 *   - the reminder (carl.json "delegation".reminder, on unless false): every user message ends with one line about
 *     the coder (the same text each time, so the prompt cache stays whole);
 *   - the gate: the dashboard's setting delegation.gate (GateSetting; carl.json "cache_api" on another computer).
 * Phase 23.4.3:
 *   - the brief check (carl.json "delegation".brief, on unless false): a subagent call that gives the coder a task
 *     (single, or a coder item of tasks or chain) whose TOML brief fails the check is blocked before the coder
 *     starts, with what to fix (the existing_tests paths are checked in the project folder, the call's cwd; a JSON
 *     brief of the same keys is read too; "delegation".brief_format "json" names JSON in the refusal of a task that
 *     has no brief: agent-bench's brief_json, now obsolete);
 *   - the coder's gates: CARL's subagent tool runs a subagent as its own Pi process with CARL_AGENT set to the
 *     agent's name. In the coder's process, its prompt ("Task: " and the brief) gives the brief, and tool_call
 *     blocks writes against its work_mode, or of a known_file to read only.
 * Only these: in a subagent's process there is no reminder and no new-file gate (the subagent tool also keeps
 * APPEND_SYSTEM.md, with the delegation rule, from subagents).
 */
import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { checkBrief, parseBrief } from "./carl-brief.js";
import { CoderGate, GateSetting, Turn, briefCheck, isCoderName, withReminder } from "./carl-delegation.js";

/** CARL's settings for the hand-off (carl.json in Pi's agent folder). */
function settings(): { reminder: boolean; brief: boolean; briefFormat: "toml" | "json"; cacheApi: string; coder: string } {
	try {
		const dir = process.env.PI_CODING_AGENT_DIR || join(homedir(), ".pi", "agent");
		const st = JSON.parse(readFileSync(join(dir, "carl.json"), "utf8")) as Record<string, unknown>;
		const d = (st.delegation ?? {}) as Record<string, unknown>;
		return {
			reminder: d.reminder !== false,
			brief: d.brief !== false,
			briefFormat: d.brief_format === "json" ? "json" : "toml",
			cacheApi: typeof st.cache_api === "string" ? st.cache_api : "",
			coder: typeof st.coder_agent === "string" && st.coder_agent ? st.coder_agent : "coder",
		};
	} catch {
		return { reminder: true, brief: true, briefFormat: "toml", cacheApi: "", coder: "coder" };
	}
}

/** The coder's own process: the gates from its brief. */
function coderGates(pi: ExtensionAPI) {
	let gate: CoderGate | undefined;
	pi.on("before_agent_start", async (event, ctx) => {
		const { brief } = parseBrief(String(event.prompt ?? ""));
		if (brief && !checkBrief(brief).length) gate = new CoderGate(brief, String(ctx.cwd ?? process.cwd()));
		return undefined;
	});
	pi.on("tool_call", async (event) => {
		const why = gate?.before(String(event.toolName), (event.input ?? {}) as Record<string, unknown>) ?? "";
		return why ? { block: true, reason: why } : undefined;
	});
}

export default function carlDelegation(pi: ExtensionAPI) {
	const set = settings();
	const agent = process.env.CARL_AGENT;
	if (agent) {
		if (agent === set.coder || isCoderName(agent)) coderGates(pi);
		return; // a subagent: no reminder, no new-file gate
	}
	const turn = new Turn();
	const gateSetting = new GateSetting({ cacheApi: set.cacheApi });
	pi.on("input", async (event) => {
		if (!set.reminder || !event.text.trim() || event.text.trimStart().startsWith("/")) return { action: "continue" as const };
		return { action: "transform" as const, text: withReminder(event.text, "pi", set.coder), images: event.images };
	});
	pi.on("before_agent_start", async () => {
		turn.reset();
		return undefined;
	});
	pi.on("tool_call", async (event, ctx) => {
		const tool = String(event.toolName);
		const input = (event.input ?? {}) as Record<string, unknown>;
		if (set.brief) {
			const why = briefCheck(tool, input, { format: set.briefFormat, root: String(ctx.cwd ?? process.cwd()) }); // a coder task with an incomplete brief
			if (why) return { block: true, reason: why };
		}
		const gate = await gateSetting.get(); // the dashboard's setting (Connect > Setup)
		if (!gate) return undefined;
		const why = turn.before(gate, "pi", tool, input, String(ctx.cwd ?? process.cwd()), existsSync, set.coder);
		return why ? { block: true, reason: why } : undefined;
	});
}
