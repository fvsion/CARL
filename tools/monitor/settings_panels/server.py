"""The Server panel: the state and the next action, then each setting in three columns (your choice,
running now, recommended with its source), Apply, the Memory section (does it fit), what Auto fit
suggests; beside it the Quick tip, About the selected setting and the model list; below it the
MODEL card. Full detail adds the More settings rows, the flags and config keys, the memory parts and
where the settings come from."""
from __future__ import annotations

from typing import Dict, List, Tuple

from carl_core.domain.units import MIB, file_size, memory

from ..arrange import label as arrange_label
from carl_core.domain.units import tokens

from ..cards import wrapped
from ..fmt import (reveal, B, CYN, DIM, GRN, R, RED, YEL, CardLine, Ln, Row, aligned, bar, button_rows, buttons, cwrap, draw_card,
                   fit, home_short, indent, row, side_by_side, vlen)
from ..model import JSONDict, ServerData, jdict
from ..settings import (ADV_WARN, FULL_ONLY, MODEL_ROW_KEYS, NOT_RUNNING, NUMERIC, SET_HELP, FitInfo, Pending,
                        SettingRow, SettingsService, fit_sentence, fmt_val, gib_pair, plan_words, row_instruction,
                        shown_value, spec_value)
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
                         "ckpt": "--ctx-checkpoints · llama.ckpt", "ckstep": "--checkpoint-min-step · llama.ckpt_step",
                         "swa": "--swa-full · cache.swa"}
SIDE_AT = 150                                           # the side column (About, the model list) from this width
SECTIONS = ["srv", "srvmem", "srvfit", "modelinfo", "srvabout", "srvlist"]


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
        if key == "swa":
            return str(rv) == (("full" if f.full else "window") if p[key] == "auto" else str(p[key]))
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
        if key == "swa" and v == "auto" and f.full is not None:
            return f"auto ({'full cache' if f.full else 'window cache'})"
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
    def section(self, ui: UIState, name: str, title: str, summary: str, lines: List[CardLine], w: int,
                marks: int) -> List[Row]:
        """One section of the panel at its own level (its title shows it; Tab selects it, L changes it)."""
        lvl = ui.levels.get(name, 1)
        return draw_card(name, title, summary, wrapped(aligned(lines, w - 4), w - 4), w, lvl, marks, ui.section == name)

    def draw(self, ui: UIState, p: Pending, d: ServerData, cols: int, height: int, port: int,
             limit_src: str = "") -> List[Row]:
        """The sections: SERVER (the settings), MEMORY, AUTO FIT, the MODEL card; beside them from SIDE_AT columns
        (else under them) ABOUT the selected setting and the model list. The page scrolls (wheel, PgUp PgDn)."""
        svc = self.svc
        full = ui.levels.get("srv", 1) == 2
        svc.full = full
        run = svc.running(d)
        rws = svc.rows(p)
        ui.set_row = min(ui.set_row, len(rws) - 1)
        key = rws[ui.set_row].key
        f = svc.fit_cached(p)
        w = cols - 1
        side_w = 60 if w >= SIDE_AT else 0
        main_w = w - side_w - 1 if side_w else w
        changed = [r for r in rws if r.key not in NOT_RUNNING and run and not self.same(r.key, p, run, f)]

        L = self.intro(p, d, run, f, changed, port, main_w - 4)
        L += self.table(ui, p, run, f, rws, main_w - 4, full and main_w - 4 >= 140)
        if full:
            L += ["", row("your choices", f"{home_short(self.config_file, self.home)}"),
                  row("recommended", self.rec_source(p))]
        state = (f"{GRN}running{R}" if run else f"{RED}stopped{R}") + ("   router mode" if d.router is not None else "")
        if changed:
            state += f"   {YEL}{plural(len(changed), 'change')}{R}"
        main = self.section(ui, "srv", "SERVER", state, L, main_w, 3)
        mem, mem_sum = self.memory_lines(ui, p, f, limit_src)
        main += self.section(ui, "srvmem", "MEMORY", mem_sum, mem, main_w, 3)
        fit_l, fit_sum = self.auto_fit_lines(p)
        main += self.section(ui, "srvfit", "AUTO FIT", fit_sum, fit_l, main_w, 2)
        mlvl = ui.levels.get("modelinfo", 1)
        card = self.card.draw(p, key, main_w, mlvl, mlvl == 2, ui.section == "modelinfo")
        label = next(r.label for r in rws if r.key == key)
        about = self.section(ui, "srvabout", f"ABOUT: {label.upper()}", "",
                             self.about_lines(p, key, (side_w or w) - 4), side_w or w, 2)
        if side_w:
            lst = self.section(ui, "srvlist", "MODELS", "", self.model_list(ui, p, side_w - 4,
                                                                             max(len(main) + len(card) - len(about) - 4, 12)),
                               side_w, 2)
            rows = side_by_side(main + card, about + lst, main_w, pad_left=False)
        else:
            lst = self.section(ui, "srvlist", "MODELS", "", self.model_list(ui, p, w - 4, 16), w, 2)
            rows = indent(main + about + card + lst)
        ui.sections = list(SECTIONS)
        if ui.confirm:
            ch = [f"{r.label}: {self.running_plain(r.key, p, run, f)} → {self.choice_plain(r.key, p, run, f)}"
                  for r in changed]
            rows = indent(confirm_restart(bool(run), port, ch, min(w, 84))) + rows
        self.set_keys(ui, key)
        reveal(ui, rows, "set_scroll", height)
        ui.set_scroll = max(0, min(ui.set_scroll, len(rows) - height))
        ui.more = len(rows) > ui.set_scroll + height
        return rows[ui.set_scroll:ui.set_scroll + height]

    def about_lines(self, p: Pending, key: str, w: int) -> List[CardLine]:
        """The selected setting: what to do, what it is, its flag and config key; for the sliding window what
        each choice needs."""
        L: List[CardLine] = [*cwrap(f"{CYN}{row_instruction(key)}{R}", w), "", *cwrap(SET_HELP[key], w)]
        if key in FULL_ONLY:
            L += ["", *cwrap(f"{YEL}{ADV_WARN}{R}", w)]
        if key == "swa":
            nf, nw = self.svc.swa_needs(p)
            if nf or nw:
                L += ["", row("full cache", memory(nf)), row("window cache", memory(nw))]
        flag_, _, ck = FLAGS.get(key, "").partition(" · ")
        if flag_:
            L += ["", row("flag", flag_), row("config key", ck)]
        return L

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
            ui.keys = [("↑↓", "setting"), ("← →", "change"), ("a", "apply"), *([("Enter", enter)] if enter else []),
                       ("r", "undo"), ("x", "recommended"), ("Tab", "section"), ("L", "level"),
                       ("A", "Auto fit"), ("m", "model list"), ("[ ]", "panels"), ("PgUp PgDn", "scroll")]
        ui.keys_more = ["The Running now column shows a value only when it is different from your choice.",
                        "On the context row and the other number rows, type a number, then press Enter (96k = 96K "
                        "tokens).",
                        "To add a model from Hugging Face: the Models panel (]), then h.",
                        "After a change to the context or the slots, update OpenCode and Pi: the Connect tab, u."]

    def intro(self, p: Pending, d: ServerData, run: Pending, f: FitInfo, changed: List[SettingRow], port: int,
              w: int) -> List[CardLine]:
        """The state in one line (an unreadable model list first)."""
        svc = self.svc
        L: List[CardLine] = []
        if svc.models.error:                            # a broken catalogue / models.json: say so here
            L += cwrap(f"{RED}The model list is not available: {svc.models.error}{R}", w)
            L += cwrap(f"{DIM}Correct the file. ./carl.sh models shows the same error. CARL reads the list again "
                       f"every 10 s.{R}", w) + [""]
        if d.router is not None:
            first = f"Router mode on port {port}: every model it offers uses these settings."
        elif run:
            first = (f"{run.get('model')} runs on port {port}, {net_name(run.get('net', 'local'))}. "
                     + ("Your settings match it." if not changed else
                        f"{YEL}You changed {plural(len(changed), 'setting')}: a applies "
                        f"{'it' if len(changed) == 1 else 'them'} (the server restarts).{R}"))
        else:
            first = "The server is not running. a starts it with these settings."
        return L + cwrap(first, w) + [""]

    def table(self, ui: UIState, p: Pending, run: Pending, f: FitInfo, rws: List[SettingRow],
              w: int, flag_col: bool = False) -> List[CardLine]:
        """The settings: label, your choice, running now, recommended (with its source); full detail: the
        flag and config key of each (a column when there is room, else under the row)."""
        svc = self.svc
        name = svc.resolved_model(p)
        rec, src = svc.recommended(name)
        cw = max(min(34, (w - 2 - LABEL_W) * 34 // 100), 24)
        rw = max(min(18, (w - 2 - LABEL_W - cw) // 3), 12)
        recw = w - 2 - LABEL_W - cw - rw - (30 if flag_col else 0)
        head = f"  {'Setting':<{LABEL_W}}{'Your choice':<{cw}}{'Running now':<{rw}}{'Recommended':<{recw}}"
        L: List[CardLine] = [f"{DIM}{head}{'Flag' if flag_col else ''}{R}"]
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
            L.append(Ln(text + fit(rec_text, recw - 1) + (f" {DIM}{FLAGS.get(r.key, '').split(' · ')[0]}{R}"
                                                         if flag_col else ""), spans=spans))
            note = svc.value_note(r.key, p)
            if note:
                notes.append(note)
        if not self.svc.full:
            L.append(f"  {DIM}{'More settings':<{LABEL_W}}{R}" + fit(f"{DIM}Presence, sampling, batch size and "
                                                                       f"checkpoints: the full level (L).{R}",
                                                                       w - 2 - LABEL_W))
        L += [x for n in notes for x in cwrap(f"{RED}⚠{R} {n}", w, "  ")]
        L.append("")
        if ui.restart:
            L += cwrap(f"{YEL}The server is restarting now. The line at the bottom shows each step.{R}", w)
        else:
            go = "Apply and restart (a)" if run else "Start the server (a)"
            L += button_rows("", [(go, "setapply" if f.fits else "setnofit"), ("Undo my changes (r)", "setrevert"),
                                  ("Use the recommended settings (x)", "setdefaults")], w)
        return L

    def memory_lines(self, ui: UIState, p: Pending, f: FitInfo, limit_src: str) -> Tuple[List[CardLine], str]:
        """Does it fit (a row and a bar), the sliding-window cache, a start that failed; full: the parts. And the
        section's summary."""
        full = ui.levels.get("srvmem", 1) == 2
        L: List[CardLine] = []
        ok = f.known and f.downloaded and not f.error
        if not ok:
            L += [fit_sentence(f)]
            summary = f"{RED}✗{R}"
        else:
            need, limit = gib_pair(f.need, f.limit)
            L.append(row("fits", f"{GRN}✓ yes{R}" if f.fits else f"{RED}✗ no{R}"))
            L.append(row("needs", f"{bar(f.need / f.limit if f.limit else 0, 30)}  {need.replace(' GiB', '')} of {limit}"))
            if not f.fits:
                L.append(row("at most", f"{tokens(f.largest)} per slot with {plural(f.slots, 'slot')}" if f.largest
                             else "The weights alone do not fit."))
            summary = (f"{GRN}✓{R} {need.replace(' GiB', '')} of {limit}" if f.fits else f"{RED}✗ {need} of {limit}{R}")
        if ok and f.dropped:
            L.append(row("speculation", f"{YEL}n-gram only{R}: MTP does not fit with these slots and this context. "
                                        f"The server starts without it."))
        if f.full is not None:
            L.append(row("sliding window", "Full cache: saved sessions can be restored." if f.full else
                         f"{YEL}Window cache{R}: saved sessions cannot be restored."))
        if full and ok:
            L += ["", row("weights", f"{memory(f.weights):>9}"), row("drafter", f"{memory(f.drafter) if f.drafter else '0':>9}"),
                  row("context memory", f"{memory(f.context):>9}"), row("recurrent state", f"{memory(f.state):>9}"),
                  row("buffers", f"{memory(f.buffers):>9}"),
                  row("GPU limit", f"{memory(f.limit):>9}" + (f"   {DIM}{limit_src}{R}" if limit_src and "Metal" not in
                                                                limit_src else ""))]
        if ui.start_error:
            L += ["", f"{RED}{B}The last start failed. The launcher said:{R}"]
            L += [f"{RED}{e}{R}" for e in ui.start_error[:8]]
        return L, summary

    def auto_fit_lines(self, p: Pending) -> Tuple[List[CardLine], str]:
        """What Auto fit suggests for this Mac, as rows; the summary is its pick."""
        svc = self.svc
        af = svc.auto_fit(p)
        if af is None:
            return [f"{YEL}Auto fit cannot work now: {svc.models.fit_error or 'no model list'}.{R}"], ""
        if not af.pick or not af.plan:
            return [f"{RED}No model fits this Mac with this goal.{R} The Auto fit panel (A) tells why."], ""
        name = af.pick.name
        m = svc.models.by_name(name)
        mine = name == svc.resolved_model(p)
        L: List[CardLine] = [Ln(row("suggests", f"{CYN}{name}{R} ★" + (f"   {DIM}your choice{R}" if mine else "")),
                                act=f"sp:{SP_FIT}"),
                             row("with", plan_words(af.plan.slots, af.plan.ctx))]
        if not af.pick.downloaded:
            L.append(row("download", f"{file_size(m['bytes']) if m else 'Its size is not known'}. Download it in the "
                                     f"Auto fit panel (A)."))
            if p.get("model") == "auto":
                L.append(row("until then", f"Auto starts {svc.resolved_model(p)}."))
        L.append(row("why", f"{af.order_text()}. The Auto fit panel (A) tells more."))
        return L, name

    def rec_source(self, p: Pending) -> str:
        m = self.svc.models.by_name(self.svc.resolved_model(p))
        t = jdict(jdict(m.get("local")).get("tune")) if m else {}
        if t:
            return f"Auto-tune on this Mac ({t.get('date', '?')}, {t.get('machine', '?')}), else the catalogue."
        return "The catalogue. Auto-tune did not measure this model on this Mac."

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
        L: List[CardLine] = [f"{GRN}●{R} downloaded   {DIM}○{R} not downloaded",
                             f"{CYN}★{R} Auto fit's choice   {RED}red{R} = does not fit",
                             Ln(fit(f"Sort: {CYN}{so}{R} (s)", w), act="msortpick"),
                             Ln(fit(f"Show: {CYN}{fi}{R} (f)", w), act="mfilterpick")]
        vis = max(height - len(L) - 1, 3)
        top = min(max(ui.srow - vis // 2, 0), max(len(items) - vis, 0))
        for i in range(top, min(top + vis, len(items))):
            name = items[i]
            st = ms[name]["status"] if name in ms else ""
            dot = (f"{GRN}●{R}" if st == "downloaded" else f"{YEL}◐{R}" if st == "partial" else f"{DIM}○{R}")
            chosen = name == cur
            col = (B + CYN) if chosen else RED if name in ms and self.lines.too_big(ms[name]) else ""
            if name == "auto":
                text = f"  {col}auto{R} {DIM}(now: {self.svc.resolved_model(dict(p, model='auto'))}){R}"
            else:
                text = f"{dot} {col}{name}{R}" + (f" {CYN}★{R}" if name == pick else "")
            if ui.slist and i == ui.srow:
                text = f"\x1b[7m{fit(text, w - 2)}{R}"
            L.append(Ln(("› " if chosen else "  ") + text, act=f"smodel:{name}"))
        more = len(items) - (top + vis)
        L.append(f"{DIM}" + (f"… and {more} more. Press m, then ↑↓." if more > 0 else "Press m to choose with the keys.") + R)
        return L

