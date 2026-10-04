/**
 * CARL: the prompt cache for Pi (installed by CARL's client/install.sh). Each session's conversation is
 * saved on the CARL server's disk after its turn and restored before its next request when the server no
 * longer holds it (after a restart, a model switch, or many other sessions); each agent's prompt is read
 * once and saved. The work is in carl-cache.js (shared with the OpenCode plugin).
 *
 * Only requests to CARL's providers (carl.json "providers" in Pi's agent folder) are touched. Subagents (CARL's
 * subagent tool runs them with --no-session and CARL_AGENT) get their prompt's file, not a session's.
 */
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { CarlCache, debugLog, errorText, obj, splitPi } from "./carl-cache.js";

/** CARL's state for Pi (carl.json in Pi's agent folder): our provider ids and the dashboard's cache API. */
function carlState(): { providers: Set<string>; cacheApi?: string } {
	try {
		const dir = process.env.PI_CODING_AGENT_DIR || join(homedir(), ".pi", "agent");
		const st = obj(JSON.parse(readFileSync(join(dir, "carl.json"), "utf8")) as unknown);
		return {
			providers: new Set(Object.values(obj(st.providers)).filter((v): v is string => typeof v === "string")),
			cacheApi: typeof st.cache_api === "string" ? st.cache_api : undefined,
		};
	} catch {
		return { providers: new Set(["llamacpp"]) };
	}
}

export default function carlCache(pi: ExtensionAPI) {
	const state = carlState();
	const ours = state.providers;
	const caches = new Map<string, CarlCache>();
	const agent = process.env.CARL_AGENT ? `pi-${process.env.CARL_AGENT}` : "pi";
	let release = () => {};
	let last: CarlCache | undefined;

	pi.on("before_provider_request", async (event, ctx) => {
		const model = ctx.model;
		if (!model || !ours.has(String(model.provider)) || !event.payload || typeof event.payload !== "object" || Array.isArray(event.payload)) return undefined;
		try {
			const base = String(model.baseUrl ?? "");
			let cache = caches.get(base);
			if (!cache) {
				const auth = await ctx.modelRegistry.getApiKeyAndHeaders(model);
				cache = new CarlCache({ baseURL: base, apiKey: auth.ok ? auth.apiKey : undefined, split: splitPi, cacheApi: state.cacheApi });
				caches.set(base, cache);
			}
			last = cache;
			const sm = ctx.sessionManager;
			const r = await cache.before(obj(event.payload), {
				session: sm.getSessionId(), agent, sub: !sm.getSessionFile(),
			});
			release = r.release;
			return r.payload;
		} catch (e) {
			debugLog(`request: ${errorText(e)} (it goes as it is)`); // never in the way of a request
			return undefined;
		}
	});

	pi.on("after_provider_response", async () => {
		release();
		release = () => {};
	});

	// a reply that ends the turn (not a tool call): saved before Pi goes on (and before `pi -p` exits)
	pi.on("message_end", async (event, ctx) => {
		const msg = event.message;
		const sm = ctx.sessionManager;
		if (!last || msg.role !== "assistant" || !(msg.stopReason === "stop" || msg.stopReason === "length")) return undefined;
		// a subagent (no session file) saves nothing, but its turn ends too
		await last
			.after({ session: sm.getSessionId(), agent, sub: !sm.getSessionFile() })
			.catch((e: unknown) => debugLog(`save after the reply: ${errorText(e)}`));
		return undefined;
	});

	// the turn ends also when it is stopped (Esc) or fails: the dashboard waits for no turn
	pi.on("agent_end", async (_event, ctx) => {
		await last?.turnsDone(ctx.sessionManager.getSessionId()).catch((e: unknown) => debugLog(`turn end: ${errorText(e)}`));
	});
}
