/**
 * CARL: the /carl panel for Pi (installed by CARL's client/install.sh): every CARL piece on this computer
 * with its state (carl-panel.js), one section per piece: the client config sync first (its auto-apply
 * switch, a config that waits, a check now), then the prompt cache, the coder, the subagent tool, the
 * browser and web search. Pi's dialogs are lists: the sections are a list, each one opens its own.
 * Without the sync service, a session start checks the server once for a pushed config.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { appliedSinceStart, checkOnce, run, sections } from "./carl-panel.js";

const BACK = "‹ back to every CARL piece";

export default function carlPanel(pi: ExtensionAPI) {
	pi.on("session_start", async () => {
		checkOnce();
	});

	// Pi reads its models when it starts: say once when a pushed config was applied since
	let told = false;
	pi.on("agent_end", async (_event, ctx) => {
		const v = appliedSinceStart();
		if (v && !told) {
			told = true;
			ctx.ui.notify(`CARL: the server's client config ${v} was applied (models, windows): restart Pi to use it.`, "info");
		}
	});

	pi.registerCommand("carl", {
		description: "Every CARL piece on this computer: the config sync, the prompt cache, the tools",
		handler: async (_args, ctx) => {
			for (;;) {
				const all = sections("pi");
				const pick = await ctx.ui.select("CARL", all.map((s) => `${s.title} · ${s.summary}`));
				const s = all.find((x) => pick?.startsWith(`${x.title} · `));
				if (!s) return;
				for (;;) {
					const cur = sections("pi").find((x) => x.id === s.id) ?? s;
					const acts = cur.actions.map((a) => `▸ ${a.label}`);
					const choice = await ctx.ui.select(`CARL · ${cur.title}`, [...acts, ...cur.lines, BACK]);
					if (!choice || choice === BACK) break;
					const a = cur.actions[acts.indexOf(choice)];
					if (!a) continue;
					const code = await run(a.args);
					ctx.ui.notify(code === 0 ? "CARL: done" : `CARL: ${a.label.toLowerCase()} failed`, code === 0 ? "info" : "error");
				}
			}
		},
	});
}
