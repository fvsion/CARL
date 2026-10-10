/**
 * CARL: the model check for Pi (installed by CARL's client/install.sh; Phase 23.5, as OpenCode's carl-model-check
 * plugin): before a request to CARL's provider, the model Pi asks for against what the server runs
 * (carl-model-check.js), and a notice once per situation:
 *   single-model mode   the server runs another model (it answers with that one)
 *   router mode         the model isn't installed (HTTP 400), or it is being loaded
 * /carl's Model Check row turns it on and off (NO_MODEL_CHECK, as for OpenCode). Never in the way of a request.
 */
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { serverModels, verdict } from "./carl-model-check.js";

const CACHE_MS = 5_000;

/** CARL's provider ids (carl.json "providers" in Pi's agent folder). */
function providers(): Set<string> {
	try {
		const dir = process.env.PI_CODING_AGENT_DIR || join(homedir(), ".pi", "agent");
		const st = JSON.parse(readFileSync(join(dir, "carl.json"), "utf8")) as { providers?: Record<string, unknown> };
		return new Set(Object.values(st.providers ?? {}).filter((v): v is string => typeof v === "string"));
	} catch {
		return new Set(["llamacpp"]);
	}
}

export default function carlModelCheck(pi: ExtensionAPI) {
	const ours = providers();
	// each situation said once per Pi process (Pi loads its extensions again at each session switch)
	const g = globalThis as { __carlModelSaid?: Set<string> };
	const said = (g.__carlModelSaid ??= new Set<string>());
	let cache = { t: 0, url: "", state: undefined as Awaited<ReturnType<typeof serverModels>> };

	pi.on("before_provider_request", async (_event, ctx) => {
		try {
			const model = ctx.model;
			if (!model || !ours.has(String(model.provider))) return undefined;
			const base = String(model.baseUrl ?? "");
			if (!base) return undefined;
			if (cache.url !== base || Date.now() - cache.t > CACHE_MS) {
				const auth = await ctx.modelRegistry.getApiKeyAndHeaders(model);
				cache = { t: Date.now(), url: base, state: await serverModels(base, auth.ok ? auth.apiKey : undefined) };
			}
			const v = cache.state && verdict(String(model.id), cache.state, "Pi");
			if (v && !said.has(v.key)) {
				said.add(v.key);
				ctx.ui.notify(v.message, v.variant);
			}
		} catch {
			/* never in the way of a request */
		}
		return undefined;
	});
}
