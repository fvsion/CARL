/**
 * CARL: the /carl panel for Pi (installed by CARL's client/install.sh): a control panel (carl-panel.js). The
 * list has one row for each CARL part: its label and its state ("Coder subagent   on"): the config sync, new
 * configs at once, the coder, the background coder, the delegation reminder, the browser, web search and the
 * disk cache. Pi's dialogs are lists: a row opens its own (its actions, what the part does, then the details
 * under their own line, then "‹ back"). A switch runs `carl-sync.py set`; a notice says what changed and what
 * must restart.
 * Without the sync service, a session start checks the server once for a new config.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { act, appliedSinceStart, checkOnce, restartNotice, sections } from "./carl-panel.js";

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
		description: "Turn the CARL parts on this computer on or off: the coder, the tools, the config sync",
		handler: async (_args, ctx) => {
			for (;;) {
				const all = sections("pi");
				const rows = all.map((s) => s.row);
				const pick = await ctx.ui.select("CARL", rows);
				const s = pick === undefined ? undefined : all[rows.indexOf(pick)];
				if (!s) return;
				for (;;) {
					const cur = sections("pi").find((x) => x.id === s.id) ?? s;
					const acts = cur.actions.map((a) => `▸ ${a.label}`);
					const details = cur.details.length ? [DETAILS, ...cur.details] : [];
					const choice = await ctx.ui.select(`CARL › ${cur.title}`, [...acts, ...cur.lines, ...details, BACK]);
					if (!choice || choice === BACK) break;
					const a = cur.actions[acts.indexOf(choice)];
					if (!a) continue;
					if (a.busy) ctx.ui.notify(a.busy, "info");
					const said = await act(a, "pi");
					ctx.ui.notify(said.message, said.ok ? "info" : "error");
				}
			}
		},
	});
}
