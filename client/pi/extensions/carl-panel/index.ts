/**
 * CARL: the /carl panel for Pi (installed by CARL's client/install.sh): every CARL part on this computer
 * with its state (carl-panel.js), one section per part: the client config sync first (whether new configs
 * are applied at once, a config that waits, a check now), then the disk cache, the coder, the subagent tool,
 * the browser and web search. Pi's dialogs are lists: the sections are a list, each one opens its own (its
 * actions and sentences, then the details under their own line, then "‹ back").
 * Without the sync service, a session start checks the server once for a new config.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { appliedSinceStart, checkOnce, outcome, restartNotice, run, sections } from "./carl-panel.js";

const BACK = "‹ back";
const DETAILS = "── Details ──";

export default function carlPanel(pi: ExtensionAPI) {
	pi.on("session_start", async () => {
		checkOnce();
	});

	// Pi reads its models when it starts: say once when a config from the dashboard was applied since
	let told = false;
	pi.on("agent_end", async (_event, ctx) => {
		if (appliedSinceStart() && !told) {
			told = true;
			ctx.ui.notify(restartNotice("pi"), "info");
		}
	});

	pi.registerCommand("carl", {
		description: "The CARL parts on this computer and their state: config sync, disk cache, tools",
		handler: async (_args, ctx) => {
			for (;;) {
				const all = sections("pi");
				const pick = await ctx.ui.select("CARL", all.map((s) => `${s.title} · ${s.summary}`));
				const s = all.find((x) => pick?.startsWith(`${x.title} · `));
				if (!s) return;
				for (;;) {
					const cur = sections("pi").find((x) => x.id === s.id) ?? s;
					const acts = cur.actions.map((a) => `▸ ${a.label}`);
					const details = cur.details.length ? [DETAILS, ...cur.details] : [];
					const choice = await ctx.ui.select(`CARL · ${cur.title}`, [...acts, ...cur.lines, ...details, BACK]);
					if (!choice || choice === BACK) break;
					const a = cur.actions[acts.indexOf(choice)];
					if (!a) continue;
					if (a.busy) ctx.ui.notify(a.busy, "info");
					const said = outcome(a, await run(a.args), "pi");
					ctx.ui.notify(said.message, said.ok ? "info" : "error");
				}
			}
		},
	});
}
