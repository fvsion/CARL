/**
 * CARL: the /carl panel for Pi (installed by CARL's client/install.sh): a control panel (carl-panel.js) in Pi's own
 * settings list (as /settings). Each row is a CARL part's label and its state; Enter or Space changes it in place:
 * the state reads "turning off…" while the setup runs, then the new state, and a notice says what CARL did; the
 * title says when Pi must restart. Web search opens its values (with where the queries go); Sync service opens a
 * view of its state.
 * Without the sync service, a session start checks the server once for a new config.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { getSelectListTheme, getSettingsListTheme } from "@earendil-works/pi-coding-agent";
import { Container, Key, matchesKey, type SettingItem, SelectList, SettingsList, Spacer, Text } from "@earendil-works/pi-tui";
import { act, appliedSinceStart, checkOnce, panel, restartNotice, viewText } from "./carl-panel.js";
import type { Action, Row } from "./carl-panel.js";

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
			await ctx.ui.custom<void>((tui, theme, _kb, done) => {
				const busy = new Map<string, string>(); // row id -> its state while an action runs
				let running = false;
				let rows: Row[] = [];
				const title = new Text("", 1, 0);
				const box = new Container();
				let list: SettingsList | undefined;

				/** The rows again (a change can add or remove rows), the cursor on `keep`. */
				const heading = (text: string, note = "") =>
					title.setText(theme.fg("accent", theme.bold(text)) + (note ? `   ${theme.fg("accent", note)}` : ""));
				const build = (keep?: string) => {
					const now = panel("pi");
					rows = now.rows;
					heading("CARL", now.title);
					const items: SettingItem[] = rows.map((r) => item(r));
					list = new SettingsList(items, Math.min(items.length, MAX_ROWS), getSettingsListTheme(), changed, () => done());
					if (keep) list.selectItem(keep);
					box.clear();
					box.addChild(title);
					box.addChild(new Spacer(1));
					box.addChild(list);
					tui.requestRender();
				};

				const item = (r: Row): SettingItem => {
					const shown = busy.get(r.id) ?? r.state;
					if (r.kind === "switch") return { id: r.id, label: r.label, currentValue: shown, values: ["on", "off"] };
					// an action has one value: Enter "changes" it to itself, which runs the action (changed())
					if (r.kind === "action") return { id: r.id, label: r.label, currentValue: shown, values: [shown] };
					if (r.kind === "choice") {
						return { id: r.id, label: r.label, currentValue: shown, submenu: (cur, close) => {
							const values = r.values ?? [];
							const pick = new SelectList(values.map((v) => ({ value: v, label: v, description: r.notes?.[v] })),
								values.length, getSelectListTheme(), { minPrimaryColumnWidth: 10, maxPrimaryColumnWidth: 10 });
							pick.setSelectedIndex(Math.max(0, values.indexOf(cur)));
							heading(r.label);
							const back = (v?: string) => {
								heading("CARL", panel("pi").title);
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
							heading("CARL", panel("pi").title);
							close();
						} });
					} };
				};

				/** The list changed a row's value: run the row's action for it. */
				const changed = (id: string, value: string) => {
					const r = rows.find((x) => x.id === id);
					const a: Action | undefined = r?.kind === "action" ? r.action : r?.actions?.[value];
					if (!r || !a || running) return build(id);
					running = true;
					busy.set(id, a.busy);
					list?.updateValue(id, a.busy);
					tui.requestRender();
					void act(a, "pi").then((said) => {
						ctx.ui.notify(said.message, said.ok ? "info" : "error");
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
