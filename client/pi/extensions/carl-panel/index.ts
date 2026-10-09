/**
 * CARL: the /carl panel for Pi (installed by CARL's client/install.sh): a control panel (carl-panel.js) in Pi's own
 * settings list (as /settings). Each row is a CARL part's label and its state ("Coder subagent  on ›": the "›" says
 * that it opens a list); Enter or Space changes it in place: the state reads "turning off…" while the setup runs, then
 * the new state, and a notice says what CARL did (a warning when the coder goes on with 1 slot); the title says when Pi
 * must restart. Web search opens its values (with where the queries go); Coder subagent opens its own list in the same
 * place (Esc goes back), and Coder thinking its values for the model of this session (ctx.model); a choice with one
 * value opens nothing; Sync service opens a view of its state.
 * Without the sync service, a session start checks the server once for a new config.
 * Phase 23.4.5: Coder model's values are "same as main" and the models this Pi can use, asked from Pi itself: the
 * model registry's available models (ctx.modelRegistry.getAvailable()) with the thinking levels pi-ai knows for each
 * (getSupportedThinkingLevels); carl-panel.js piModels reads only their ids, base URLs and costs, never a key.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { getSelectListTheme, getSettingsListTheme } from "@earendil-works/pi-coding-agent";
import { Container, Key, matchesKey, type SettingItem, SelectList, SettingsList, Spacer, Text } from "@earendil-works/pi-tui";
import { getSupportedThinkingLevels } from "@earendil-works/pi-ai";
import { act, appliedSinceStart, checkOnce, panel, piModels, restartNotice, rowsAt, shownState, viewText } from "./carl-panel.js";
import type { Action, Row, Session } from "./carl-panel.js";

/** The most rows the list shows before it scrolls. */
const MAX_ROWS = 15;

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
		description: "Turn the CARL parts on this computer on or off",
		handler: async (_args, ctx) => {
			if (ctx.mode !== "tui") {
				ctx.ui.notify("/carl needs Pi's interactive mode.", "error");
				return;
			}
			// the model of this session: Coder thinking is for the model the coder runs on ("same as main"); Pi's
			// models, for Coder model
			let available: unknown[] = [];
			try {
				available = ctx.modelRegistry.getAvailable();
			} catch {
				available = [];
			}
			const session: Session = {
				model: ctx.model ? `${ctx.model.provider}/${ctx.model.id}` : undefined,
				external: piModels(available, (m) => getSupportedThinkingLevels(m)),
			};
			await ctx.ui.custom<void>((tui, theme, _kb, done) => {
				const busy = new Map<string, string>(); // row id -> its state while an action runs
				let running = false;
				let rows: Row[] = [];
				let path: string[] = []; // the list rows opened from the top (Coder subagent, Coder thinking)
				const title = new Text("", 1, 0);
				const box = new Container();
				let list: SettingsList | undefined;

				const heading = (text: string, note = "") =>
					title.setText(theme.fg("accent", theme.bold(text)) + (note ? `   ${theme.fg("accent", note)}` : ""));
				/** The name of the list shown: CARL, or the list row's title. */
				const listName = () => {
					let name = "CARL";
					let at = panel("pi", session).rows;
					for (const id of path) {
						const r = at.find((x) => x.id === id);
						name = r?.listTitle ?? r?.label ?? name;
						at = r?.rows ?? [];
					}
					return name;
				};
				/** The rows again (a change can add or remove rows), the cursor on `keep`. */
				const build = (keep?: string) => {
					const now = panel("pi", session);
					if (!rowsAt(now.rows, path)) path = []; // a list that is gone now (the coder went off)
					rows = rowsAt(now.rows, path) ?? [];
					heading(listName(), now.title);
					const items: SettingItem[] = rows.map((r) => item(r));
					// Esc: back to the list above, or out of /carl
					const back = () => {
						if (!path.length) return done();
						const from = path.pop();
						build(from);
					};
					list = new SettingsList(items, Math.min(items.length, MAX_ROWS), getSettingsListTheme(), changed, back);
					if (keep) list.selectItem(keep);
					box.clear();
					box.addChild(title);
					box.addChild(new Spacer(1));
					box.addChild(list);
					tui.requestRender();
				};

				const item = (r: Row): SettingItem => {
					const shown = shownState(r, busy.get(r.id));
					if (r.kind === "switch") return { id: r.id, label: r.label, currentValue: shown, values: ["on", "off"] };
					// an action, a list and a choice with one value have one value: Enter "changes" it to itself, which runs
					// the action or opens the list (changed()), or does nothing
					const values = r.values ?? [];
					if (r.kind === "action" || r.kind === "list" || (r.kind === "choice" && values.length < 2)) {
						return { id: r.id, label: r.label, currentValue: shown, values: [shown] };
					}
					if (r.kind === "choice") {
						return { id: r.id, label: r.label, currentValue: shown, submenu: (_shown, close) => {
							const cur = r.value ?? r.state;
							const words = values.map((v) => r.titles?.[v] ?? v);
							const width = Math.max(10, ...words.map((w) => w.length));
							const pick = new SelectList(values.map((v, i) => ({ value: v, label: words[i], description: r.notes?.[v] })),
								values.length, getSelectListTheme(), { minPrimaryColumnWidth: width, maxPrimaryColumnWidth: width });
							pick.setSelectedIndex(Math.max(0, values.indexOf(cur)));
							heading(r.choiceTitle ?? r.label);
							const back = (v?: string) => {
								heading(listName(), panel("pi", session).title);
								close(v);
							};
							pick.onSelect = (v) => back(v.value === cur ? undefined : v.value);
							pick.onCancel = () => back();
							return pick;
						} };
					}
					return { id: r.id, label: r.label, currentValue: shown, submenu: (_cur, close) => {
						const view = new Container();
						heading(r.viewTitle ?? r.label);
						view.addChild(new Text(viewText(r.view ?? []), 2, 0));
						view.addChild(new Spacer(1));
						view.addChild(new Text(theme.fg("dim", "Esc to go back"), 2, 0));
						return Object.assign(view, { handleInput: (data: string) => {
							if (!matchesKey(data, Key.escape)) return;
							heading(listName(), panel("pi", session).title);
							close();
						} });
					} };
				};

				/** The list changed a row's value: open a list, or run the row's action for it. */
				const changed = (id: string, value: string) => {
					const r = rows.find((x) => x.id === id);
					if (r?.kind === "list" && !running) {
						path.push(id);
						return build();
					}
					const a: Action | undefined = r?.kind === "action" ? r.action : r?.actions?.[value];
					if (!r || !a || running) return build(id);
					running = true;
					busy.set(id, a.busy);
					list?.updateValue(id, a.busy);
					tui.requestRender();
					void act(a, "pi", session).then((said) => {
						ctx.ui.notify(said.message, !said.ok ? "error" : said.warn ? "warning" : "info");
					}).finally(() => {
						busy.delete(id);
						running = false;
						build(id);
					});
				};

				build();
				return {
					render: (width: number) => box.render(width),
					invalidate: () => box.invalidate(),
					handleInput: (data: string) => {
						if (running && !matchesKey(data, Key.escape)) return; // one action at a time
						list?.handleInput(data);
						tui.requestRender();
					},
				};
			});
		},
	});
}
