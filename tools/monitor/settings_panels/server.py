"""The Server panel: the state and the next action, then each setting in three columns (your choice,
running now, recommended with its source), Apply, the Memory section (does it fit), what Auto fit
suggests; beside it the Quick tip, About the selected setting and the model list; below it the
MODEL card. Full detail adds the More settings rows, the flags and config keys, the memory parts and
where the settings come from."""
from __future__ import annotations

from typing import Dict, List, Tuple

from carl_core.domain.units import MIB, file_size, memory

from ..arrange import label as arrange_label
from ..fmt import (B, CYN, DIM, GRN, R, RED, YEL, CardLine, Ln, Row, bar, button_rows, buttons, cwrap, draw_card, fit,
                   heading, home_short, indent, merge_columns, side_lines, vlen)
from ..model import JSONDict, ServerData, jdict
from ..settings import (ADV_WARN, FULL_ONLY, MODEL_ROW_KEYS, MORE_SETTINGS, NOT_RUNNING, NUMERIC, SET_HELP, FitInfo, Pending,
                        SettingRow, SettingsService, fit_sentence, fmt_val, gib_pair, plan_words, row_instruction,
                        shown_value, spec_value, swa_sentence)
from ..state import SP_FIT, UIState
from ..words import net_name, plural, spec_name
from .model_card import ModelCard
from .model_lines import ModelLines

SIDE_W = (38, 60)                                       # the side column: the Quick tip, About, the model list
MAIN_MIN = 90                                           # the settings column needs this much to sit beside it
LABEL_W = 17
SOURCE = {"auto-tune": "Auto-tune", "catalogue": "catalogue", "header": "the file", "default": "CARL's default",
          "config": "your setting"}
FLAGS: Dict[str, str] = {"model": "-m · llama.model", "ctx": "-c · ctx", "slots": "--parallel · slots",
                         "spec": "--spec-type · spec, spec_n", "kv": "-ctk -ctv · kv", "cache": "--cache-ram · llama.cache_ram",
                         "net": "--host · llama.net", "temp": "--temp · temp", "presence": "--presence-penalty · presence",
                         "top_k": "--top-k · top_k", "top_p": "--top-p · top_p", "min_p": "--min-p · min_p",
                         "repeat": "--repeat-penalty · repeat", "ub": "-ub · llama.ub",
                         "ckpt": "--ctx-checkpoints · llama.ckpt", "ckstep": "--checkpoint-min-step · llama.ckpt_step"}
GIB_NOTE = "GiB is memory: 1 GiB = 1.07 GB."


def confirm_restart(run: bool, port: int, changes: List[str], w: int) -> List[Row]:
    """Apply's question: restart (or start) the server with these settings? It lists the changes."""
    if run:
        first = (["You changed:", *[f"  {c}" for c in changes]] if changes else
                 ["Nothing changed. Restart the server anyway?"])
        L: List[CardLine] = ["", *first, "",
                             "Requests in progress stop. The model loads again (30 s to 2 min).",
                             "If the new server does not start, CARL starts the old server again.", "",
                             buttons("  ", [("Restart (y)", "setyes"), ("Cancel (n)", "setno")])]
    else:
        L = ["", f"This starts the server on port {port}. The model loads (30 s to 2 min).", "",
             buttons("  ", [("Start (y)", "setyes"), ("Cancel (n)", "setno")])]
    return draw_card("confirm", "RESTART THE SERVER?" if run else "START THE SERVER?", "", L, w, 1)


class ServerPanel:
    """Draws the Server panel."""

    def __init__(self, svc: SettingsService, lines: ModelLines, card: ModelCard, config_file: str, home: str) -> None:
        self.svc = svc
        self.lines = lines
        self.card = card
        self.config_file = config_file
        self.home = home

    # ------------------------------------------------------------ values
    def same(self, key: str, p: Pending, run: Pending, f: FitInfo) -> bool:
        """Is the pending value what the running server uses?"""
        rv = run.get(key)
        if rv is None:
            return False
        if key == "model":
            return str(rv) == self.svc.resolved_model(p)
        if key == "slots":
            return str(rv) == (str(f.slots) if p[key] == "auto" else str(p[key]))
        if key == "cache":
            return p[key] == "auto" or str(p[key]) == str(rv)
        if key == "spec":
            return spec_value(p) == f"{run.get('spec')}|{run.get('specn')}"
        if key == "net":
            return str(rv) == str(p[key])
        return str(fmt_val(key, rv)) == str(p[key])

    def choice_text(self, key: str, p: Pending, run: Pending, f: FitInfo) -> str:
        """Your choice, in words: 'auto' always with what it chose."""
        v = p[key]
        if key == "model":
            return f"auto ({self.svc.resolved_model(p)})" if v == "auto" else str(v)
        if key == "slots" and v == "auto":
            return f"auto ({f.slots})" if f.slots else "auto (2 when two fit)"
        if key == "cache" and v == "auto":
            rv = run.get("cache")
            mib = int(str(rv)) if str(rv).isdigit() and int(str(rv)) else self.svc.auto_cache_mib(p)
            return f"auto ({memory(mib * MIB)})" if mib else "auto (CARL sizes it at the start)"
        if key == "spec":
            return spec_name(p.get("spec"), p.get("specn"))
        return shown_value(key, v, long=True)

    def running_text(self, key: str, p: Pending, run: Pending, f: FitInfo) -> str:
        if not run or key in NOT_RUNNING:
            return f"{DIM}–{R}"
        if self.same(key, p, run, f):
            return f"{DIM}the same{R}"
        rv = run.get(key, "–")
        if key == "spec":
            text = spec_name(run.get("spec"), run.get("specn"))
        elif key == "cache":
            text = shown_value("cache", rv) if str(rv) != "0" else "–"
        else:
            text = shown_value(key, rv)
        return f"{YEL}{text}{R}"

    def recommended_text(self, key: str, p: Pending, rec: JSONDict, src: Dict[str, str]) -> str:
        if key == "model":
            return f"{DIM}see Auto fit below{R}"
        pk = MODEL_ROW_KEYS.get(key)
        if pk is None or pk not in rec:
            return ""
        source = SOURCE.get(src.get(pk, ""), src.get(pk, ""))
        if key == "spec":
            text = spec_name(rec.get("spec"), rec.get("spec_n"))
            mine = spec_value(p) == f"{rec.get('spec')}|{rec.get('spec_n')}"
        else:
            text = shown_value(key, fmt_val(key, rec[pk]))
            mine = str(fmt_val(key, rec[pk])) == str(p[key])
        if mine and len(text) > 10:
            text = "the same"
        return f"{DIM if mine else ''}{text} ({source}){R}"

    # ------------------------------------------------------------ the panel
    def draw(self, ui: UIState, p: Pending, d: ServerData, cols: int, height: int, port: int,
             limit_src: str = "") -> List[Row]:
        """The panel and, under it, the MODEL card (click its title: open or collapsed). Scrolls with
        the wheel / PgUp PgDn."""
        svc = self.svc
        svc.full = ui.full
        run = svc.running(d)
        rws = svc.rows(p)
        ui.set_row = min(ui.set_row, len(rws) - 1)
        key = rws[ui.set_row].key
        f = svc.fit_cached(p)
        w = cols - 1
        inner = w - 4
        side_w = min(max(inner - MAIN_MIN - 3, 0), SIDE_W[1])
        side = side_w >= SIDE_W[0]
        main_w = inner - side_w - 3 if side else inner
        changed = [r for r in rws if r.key not in NOT_RUNNING and run and not self.same(r.key, p, run, f)]

        L = self.intro(p, d, run, f, changed, port, main_w)
        L += self.table(ui, p, run, f, rws, main_w)
        L += self.memory_lines(ui, f, limit_src, main_w)
        L += self.auto_fit_lines(p, main_w)
        if ui.full:
            L += ["", heading("Where the settings come from", main_w),
                  *cwrap(f"Your choices: {home_short(self.config_file, self.home)} (./carl.sh config show). Running "
                         f"now: the server's flags. Recommended: {self.rec_source(p)}", main_w)]
        about = [SET_HELP[key], *([f"{YEL}{ADV_WARN}{R}"] if key in FULL_ONLY else [])]
        label = next(r.label for r in rws if r.key == key)
        if side:
            S = side_lines(row_instruction(key), [(f"About: {label}", about)], side_w)
            S += ["", *self.model_list(ui, p, side_w, max(len(L) - len(S) - 1, 12))]
            L = merge_columns(L, S, main_w)
        else:
            L += ["", *side_lines(row_instruction(key), [], inner), "", *self.model_list(ui, p, inner, 16),
                  "", *side_lines("", [(f"About: {label}", about)], inner)]
        state = (f"{GRN}running{R}" if run else f"{RED}stopped{R}") + (f" {DIM}· router mode{R}" if d.router is not None
                                                                       else "")
        rows = indent(draw_card("settings", "SERVER", state, L, w, 1))
        ui.levels.setdefault("modelinfo", 1)
        rows += indent(self.card.draw(p, key, cols - 1, ui.levels["modelinfo"], ui.full))
        if ui.confirm:
            ch = [f"{r.label}: {self.running_plain(r.key, p, run, f)} → {self.choice_plain(r.key, p, run, f)}"
                  for r in changed]
            rows = indent(confirm_restart(bool(run), port, ch, min(w, 84))) + rows
        self.set_keys(ui, key)
        ui.set_scroll = max(0, min(ui.set_scroll, len(rows) - height))
        return rows[ui.set_scroll:ui.set_scroll + height]

    def running_plain(self, key: str, p: Pending, run: Pending, f: FitInfo) -> str:
        if key == "spec":
            return spec_name(run.get("spec"), run.get("specn"))
        return shown_value(key, run.get(key, "–"))

    def choice_plain(self, key: str, p: Pending, run: Pending, f: FitInfo) -> str:
        return self.choice_text(key, p, run, f)

    def set_keys(self, ui: UIState, key: str) -> None:
        """The footer's keys and the ? card's lines for this panel (its mode: the list, a number typed)."""
        if ui.confirm:
            ui.keys = [("y Enter", "yes"), ("n Esc", "cancel")]
        elif ui.edit is not None:
            ui.keys = [("0-9 . k", "type the value"), ("Enter", "keep it"), ("Esc", "drop it"), ("Backspace", "delete")]
        elif ui.slist:
            ui.keys = [("↑↓", "model"), ("Enter", "choose it"), ("m Esc", "back to the settings"), ("s f", "sort / show")]
        else:
            enter = "choose a model" if key == "model" else "type a value" if key in NUMERIC else ""
            ui.keys = [("↑↓", "setting"), ("← →", "change"), *([("Enter", enter)] if enter else []),
                       ("a", "apply"), ("r", "undo"), ("x", "recommended"), ("A", "Auto fit"), ("m", "model list"),
                       ("s f", "sort / show models"), ("[ ]", "panels"), ("PgUp PgDn", "scroll")]
        ui.keys_more = ["The Running now column shows a value only when it is different from your choice.",
                        "On the context row and the other number rows, type a number, then press Enter (96k = 96K "
                        "tokens).",
                        "To add a model from Hugging Face: the Models panel (]), then h.",
                        "After a change to the context or the slots, update OpenCode and Pi: the Connect tab, u."]

    def intro(self, p: Pending, d: ServerData, run: Pending, f: FitInfo, changed: List[SettingRow], port: int,
              w: int) -> List[CardLine]:
        """The state and the next action, in sentences."""
        svc = self.svc
        L: List[CardLine] = []
        if svc.models.error:                            # a broken catalogue / models.json: say so here
            L += cwrap(f"{RED}The model list is not available: {svc.models.error}{R}", w)
            L += cwrap(f"{DIM}Correct the file. ./carl.sh models shows the same error. CARL reads the list again "
                       f"every 10 s.{R}", w) + [""]
        if d.router is not None:
            first = (f"The server runs in router mode on port {port}. Each model that it offers uses these "
                     f"settings. Model chooses only the first model that loads.")
        elif run:
            first = (f"The server runs {run.get('model')} on port {port} for {net_name(run.get('net', 'local'))}. "
                     + ("Your settings match what runs." if not changed else
                        f"You changed {plural(len(changed), 'setting')}: press a to apply "
                        f"{'it' if len(changed) == 1 else 'them'} (the server restarts)."))
        else:
            first = "The server is not running. Press a to start it with these settings."
        L += cwrap(first, w)
        if not changed:
            L += cwrap("To change a setting: select it (↑↓), change it with ← →, then Apply (a).", w)
        return L + [""]

    def table(self, ui: UIState, p: Pending, run: Pending, f: FitInfo, rws: List[SettingRow],
              w: int) -> List[CardLine]:
        """The settings: label, your choice, running now, recommended (with its source); full detail: the
        flag and config key of each (a column when there is room, else under the row)."""
        svc = self.svc
        name = svc.resolved_model(p)
        rec, src = svc.recommended(name)
        cw = max(min(34, (w - 2 - LABEL_W) * 34 // 100), 24)
        rw = max(min(18, (w - 2 - LABEL_W - cw) // 3), 12)
        flag_col = ui.full and w >= 2 + LABEL_W + cw + rw + 28 + 30
        recw = w - 2 - LABEL_W - cw - rw - (30 if flag_col else 0)
        head = f"  {'Setting':<{LABEL_W}}{'Your choice':<{cw}}{'Running now':<{rw}}{'Recommended':<{recw}}"
        L: List[CardLine] = [f"{DIM}{head}{'Flag · key' if flag_col else ''}{R}"]
        notes: List[str] = []
        for i, r in enumerate(rws):
            sel = i == ui.set_row
            val = self.choice_text(r.key, p, run, f)
            if sel and ui.edit is not None:
                val = f"{ui.edit}▏"
            col = svc.value_color(r.key, p) if r.key != "model" else ""
            pre = f"{CYN}{B}›{R} " if sel else "  "
            text = f"{pre}{B if sel else ''}{r.label:<{LABEL_W}}{R}"
            spans: List[Tuple[int, int, str]] = [(0, 2 + LABEL_W, f"setrow:{i}")]
            c0 = 2 + LABEL_W
            if r.key == "model":
                cell = f"{val} ▾"
                spans.append((c0, c0 + cw, "setpick"))
            elif sel and r.choices:
                cell = f"‹ {val} ›"
                spans += [(c0, c0 + 2, f"setdec:{i}"), (c0 + 2, c0 + 2 + vlen(val), f"setrow:{i}"),
                          (c0 + 3 + vlen(val), c0 + 5 + vlen(val), f"setinc:{i}")]
            else:
                cell = val
                spans.append((c0, c0 + cw, f"setrow:{i}"))
            text += fit(f"{col}{cell}{R}", cw - 1) + " " + fit(self.running_text(r.key, p, run, f), rw - 1) + " "
            rec_text = self.recommended_text(r.key, p, rec, src)
            if ui.full and not flag_col:
                L.append(Ln(text + fit(rec_text, recw), spans=spans))
                L.append(f"    {DIM}{FLAGS.get(r.key, '')}{R}")
            else:
                L.append(Ln(text + fit(rec_text, recw - 1) + (f" {DIM}{FLAGS.get(r.key, '')}{R}" if flag_col else ""),
                            spans=spans))
            note = svc.value_note(r.key, p)
            if note:
                notes.append(note)
        if not ui.full:
            L.append(f"  {DIM}{'More settings':<{LABEL_W}}{R}" + fit(f"{DIM}{MORE_SETTINGS}: detail full (D){R}",
                                                                       w - 2 - LABEL_W))
        L += [x for n in notes for x in cwrap(f"{RED}⚠{R} {n}", w, "  ")]
        L.append("")
        if ui.restart:
            L += cwrap(f"{YEL}The server restarts now. The line at the bottom shows each step.{R}", w)
        else:
            go = "Apply and restart (a)" if run else "Start the server (a)"
            L += button_rows("", [(go, "setapply" if f.fits else "setnofit"), ("Undo my changes (r)", "setrevert"),
                                  ("Use the recommended settings (x)", "setdefaults")], w)
        return L

    def memory_lines(self, ui: UIState, f: FitInfo, limit_src: str, w: int) -> List[CardLine]:
        """Does it fit: a sentence, a bar; the sliding-window cache; a start that failed; full: the parts."""
        L: List[CardLine] = ["", heading("Memory", w), *cwrap(fit_sentence(f), w)]
        if f.known and f.downloaded and not f.error and f.limit:
            need, limit = gib_pair(f.need, f.limit)
            bw = max(min(w - 24, 60), 10)
            L.append(f"  {bar(f.need / f.limit, bw)}  {need.replace(' GiB', '')} of {limit}")
        swa = swa_sentence(f)
        if swa:
            L += cwrap(swa, w)
        if ui.full and f.known and f.downloaded and not f.error:
            parts = (f"weights {memory(f.weights)} · drafter {memory(f.drafter) if f.drafter else '0'} · context memory "
                     f"{memory(f.context)} · recurrent state {memory(f.state)} · buffers {memory(f.buffers)}")
            L += cwrap(f"  {parts}", w, "  ")
            L += cwrap(f"  The GPU memory limit comes from macOS{f' ({limit_src})' if limit_src else ''}. Auto fit also "
                       f"keeps memory free for macOS and apps: the Auto fit panel tells how much.", w, "  ")
        elif not ui.full:
            L.append(f"{DIM}{GIB_NOTE}{R}")
        if ui.start_error:
            L += ["", f"{RED}{B}The last start failed. The launcher said:{R}"]
            L += [x for e in ui.start_error[:8] for x in cwrap(f"{RED}{e}{R}", w, "  ")]
        return L

    def auto_fit_lines(self, p: Pending, w: int) -> List[CardLine]:
        """What Auto fit suggests for this Mac, in sentences, and where to see why."""
        svc = self.svc
        L: List[CardLine] = ["", heading("Auto fit suggests", w)]
        af = svc.auto_fit(p)
        if af is None:
            return L + cwrap(f"{YEL}Auto fit cannot work now: {svc.models.fit_error or 'no model list'}.{R}", w)
        if not af.pick or not af.plan:
            return L + cwrap(f"{RED}No model fits this Mac with this goal.{R} The Auto fit panel (A) tells why.", w)
        name = af.pick.name
        resolved = svc.resolved_model(p)
        m = svc.models.by_name(name)
        if name == resolved:
            text = f"{CYN}{name}{R} with {plan_words(af.plan.slots, af.plan.ctx)}: the model that you chose."
        else:
            text = f"{CYN}{name}{R} with {plan_words(af.plan.slots, af.plan.ctx)}."
        if not af.pick.downloaded:
            text += f" It is not downloaded ({file_size(m['bytes']) if m else 'a download'})."
        L.append(Ln(cwrap(text, w)[0], act=f"sp:{SP_FIT}"))
        L += [Ln(x, act=f"sp:{SP_FIT}") for x in cwrap(text, w)[1:]]
        if not af.pick.downloaded and p.get("model") == "auto":
            L += cwrap(f"Until then, auto starts {resolved}.", w)
        L += cwrap("To see why" + (" and to download it" if not af.pick.downloaded else "") + ": the Auto fit panel (A).",
                   w)
        return L

    def rec_source(self, p: Pending) -> str:
        m = self.svc.models.by_name(self.svc.resolved_model(p))
        t = jdict(jdict(m.get("local")).get("tune")) if m else {}
        if t:
            return f"Auto-tune on this Mac ({t.get('date', '?')}, {t.get('machine', '?')}), else the catalogue."
        return "the catalogue (Auto-tune did not measure this model on this Mac)."

    def model_list(self, ui: UIState, p: Pending, w: int, height: int) -> List[CardLine]:
        """The model selector (w columns, height lines): click a model (or m, then ↑↓ Enter); the sort and
        show drop-downs. Only names and status: the MODEL card below has the rest."""
        items = ["auto"] + [m["name"] for m in self.lines.visible(ui.msort, ui.mfilter)]
        cur = str(p.get("model", "auto"))
        if not ui.slist:                                    # the cursor follows the chosen model
            ui.srow = items.index(cur) if cur in items else 0
        ui.srow = max(0, min(ui.srow, len(items) - 1))
        ms = {m["name"]: m for m in self.svc.models.get()}
        af = self.svc.auto_fit(p)
        pick = af.name if af else None
        so, fi = arrange_label(ui.msort, ui.mfilter)
        L: List[CardLine] = [heading("Choose a model", w),
                             f"{GRN}●{R} downloaded   {DIM}○{R} not downloaded",
                             f"{CYN}★{R} Auto fit's choice   {RED}red{R} = does not fit",
                             Ln(fit(f"sort: {CYN}{so}{R} (s)", w), act="msortpick"),
                             Ln(fit(f"show: {CYN}{fi}{R} (f)", w), act="mfilterpick")]
        vis = max(height - len(L) - 1, 3)
        top = min(max(ui.srow - vis // 2, 0), max(len(items) - vis, 0))
        for i in range(top, min(top + vis, len(items))):
            name = items[i]
            st = ms[name]["status"] if name in ms else ""
            dot = (f"{GRN}●{R}" if st == "downloaded" else f"{YEL}◐{R}" if st == "partial" else f"{DIM}○{R}")
            chosen = name == cur
            col = (B + CYN) if chosen else RED if name in ms and self.lines.too_big(ms[name]) else ""
            if name == "auto":
                text = f"  {col}auto{R} {DIM}(now {self.svc.resolved_model(dict(p, model='auto'))}){R}"
            else:
                text = f"{dot} {col}{name}{R}" + (f" {CYN}★{R}" if name == pick else "")
            if ui.slist and i == ui.srow:
                text = f"\x1b[7m{fit(text, w - 2)}{R}"
            L.append(Ln(("› " if chosen else "  ") + text, act=f"smodel:{name}"))
        more = len(items) - (top + vis)
        L.append(f"{DIM}" + (f"… {more} more (scroll: m, then ↑↓)" if more > 0 else "m: choose with the keys") + R)
        return L

