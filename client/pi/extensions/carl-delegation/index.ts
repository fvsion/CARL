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
 * W1.6 (the coder brief): before the check, the lenient reader (repairCall) cuts leaked tool-call markup from each coder
 * task and fixes the brief's mechanical slips, in the call's input (Pi passes the changed input on, so the subagent
 * gets the fixed brief); the tool_result event adds the [CARL] note of what changed to the call's result.
 */
import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { checkBrief, fixedText, parseBrief } from "./carl-brief.js";
import { CoderGate, FREE_FORM_TEXT, GateSetting, RequestCheck, Turn, freeFormFor, isCoderName, repairCall, requestFrom, withReminder } from "./carl-delegation.js";

/** Pi's agent folder (carl.json is there). */
const agentDir = (): string => process.env.PI_CODING_AGENT_DIR || join(homedir(), ".pi", "agent");

/** CARL's settings for the hand-off (carl.json in Pi's agent folder). */
function settings(): { reminder: boolean; brief: boolean; briefFormat: "toml" | "json"; cacheApi: string; carlId: string; coder: string } {
	try {
		const st = JSON.parse(readFileSync(join(agentDir(), "carl.json"), "utf8")) as Record<string, unknown>;
		const d = (st.delegation ?? {}) as Record<string, unknown>;
		return {
			reminder: d.reminder !== false,
			brief: d.brief !== false,
			briefFormat: d.brief_format === "json" ? "json" : "toml",
			cacheApi: typeof st.cache_api === "string" ? st.cache_api : "",
			carlId: typeof st.carl_id === "string" ? st.carl_id : "",
			coder: typeof st.coder_agent === "string" && st.coder_agent ? st.coder_agent : "coder",
		};
	} catch {
		return { reminder: true, brief: true, briefFormat: "toml", cacheApi: "", carlId: "", coder: "coder" };
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
	const request = new RequestCheck(); // the Request Check (the 23.4.3 addendum): the user's messages since the last brief taken
	const gateSetting = new GateSetting({ cacheApi: set.cacheApi, carlId: set.carlId });
	const fixedNotes = new Map<string, string>(); // a coder call (its toolCallId) -> CARL's note of what it changed (W1.6)
	pi.on("input", async (event) => {
		if (set.brief) request.user(event.text.trimStart().replace(/^\/\S+\s*/, "")); // "/code TASK": the task's words
		if (!set.reminder || !event.text.trim() || event.text.trimStart().startsWith("/")) return { action: "continue" as const };
		return { action: "transform" as const, text: withReminder(event.text, "pi", set.coder), images: event.images };
	});
	// the free-form part of the brief (the 23.4.3 addendum): taught and taken only when the session's model qualifies
	const freeForm = (ctx: { model?: { provider?: string; id?: string } }): boolean =>
		freeFormFor(join(agentDir(), "carl.json"), `${ctx.model?.provider ?? ""}/${ctx.model?.id ?? ""}`);
	pi.on("before_agent_start", async (event, ctx) => {
		turn.reset();
		if (!freeForm(ctx as { model?: { provider?: string; id?: string } })) return undefined;
		return { systemPrompt: `${String(event.systemPrompt ?? "")}\n\n${FREE_FORM_TEXT}` };
	});
	pi.on("tool_call", async (event, ctx) => {
		const tool = String(event.toolName);
		const input = (event.input ?? {}) as Record<string, unknown>;
		if (set.brief) {
			// the lenient reader first (W1.6): the fixed brief goes into the input, in place
			const fixed = repairCall(tool, input, { root: String(ctx.cwd ?? process.cwd()) });
			// a coder task with an incomplete brief (and the turn's memory: no switch to tests-only after a refusal)
			const why = turn.brief(tool, input, { format: set.briefFormat, root: String(ctx.cwd ?? process.cwd()),
				freeForm: freeForm(ctx as { model?: { provider?: string; id?: string } }) });
			if (why) return { block: true, reason: why };
			// the request's literals in the brief (carl.json "request_check": on, reminder, off; read at each task)
			const missing = request.check(tool, input, requestFrom(join(agentDir(), "carl.json")));
			if (missing) return { block: true, reason: missing };
			const id = String((event as { toolCallId?: string }).toolCallId ?? "");
			if (fixed.length && id) fixedNotes.set(id, fixedText(fixed));
		}
		const gate = await gateSetting.get(); // the dashboard's setting (Connect > Setup)
		if (!gate) return undefined;
		const why = turn.before(gate, "pi", tool, input, String(ctx.cwd ?? process.cwd()), existsSync, set.coder);
		return why ? { block: true, reason: why } : undefined;
	});
	// what the lenient reader changed in a coder call: after its result (W1.6)
	pi.on("tool_result", async (event) => {
		const e = event as { toolCallId?: string; content?: unknown[] };
		const note = fixedNotes.get(String(e.toolCallId ?? ""));
		if (!note) return undefined;
		fixedNotes.delete(String(e.toolCallId));
		return { content: [...(Array.isArray(e.content) ? e.content : []), { type: "text" as const, text: note }] };
	});
}
