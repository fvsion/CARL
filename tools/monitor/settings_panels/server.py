"""The Server panel: the pending settings beside what runs now, the fit line, Apply, the model list
and, below them, the MODEL card."""
from __future__ import annotations

from typing import List

from ..arrange import FILTERS, SORTS, label as arrange_label
from ..fmt import (B, CYN, DIM, GRN, R, RED, YEL, CardLine, Ln, Row, button_rows, buttons, cwrap, draw_card, fit,
                   heading, home_short, indent, lv, merge_columns, vlen, with_side, wwrap)
from ..model import ServerData
from ..settings import ADV_WARN, NOT_RUNNING, SET_HELP, UNMARKED, Pending, SettingsService, row_instruction, shown_value
from ..state import SP_FIT, UIState
from .model_card import ModelCard
from .model_lines import ModelLines

LIST_W = 40                                            # the Server panel's model list
COLOURS = (f"{GRN}green{R} = tuned / fast · {YEL}yellow{R} = changed / slower · "
           f"{RED}red{R} = very slow / no MTP head")


def arrange_lines(sort: int, filt: int, w: int) -> List[CardLine]:
    """Compact sort / filter controls (a narrow list): "sort: quality ▾ 2/5", click for a drop-down."""
    so, fi = arrange_label(sort, filt)
    out: List[CardLine] = []
    for kind, val, idx, n, act in (("sort", so, sort, len(SORTS), "msortpick"), ("show", fi, filt, len(FILTERS), "mfilterpick")):
        text = f"{kind}: {CYN}{val} ▾{R} {DIM}{idx % n + 1}/{n}{R}"
        out.append(Ln(fit(text, w), act=act))
    return out


def about_lines(p: Pending, key: str) -> List[CardLine]:
    """About this setting: what the selected row does (the side column wraps it)."""
    return [SET_HELP[key], *([f"{YEL}{ADV_WARN}{R}"] if p.get("adv") == "shown" else [])]


KEYS_MORE = ["* shows a value that is different from the running server. To scroll, use the mouse wheel or "
             "PgUp / PgDn.",
             "On the context row and the other number rows, type a number, then press Enter.",
             "To add a model from Hugging Face, press ] for the Models panel, then press h.",
             "After a change to the context or slots, run install.sh again on the clients. The Connect tab tells "
             "you when."]


def confirm_restart(run: bool, port: int, w: int) -> List[Row]:
    """Apply's question: restart (or start) the server with the new settings?"""
    first = ("This stops the server and starts it again with the new settings." if run else
             f"This starts llama.cpp on port {port}.")
    return draw_card("confirm", "START?" if not run else "RESTART?", "", [
        "", first,
        "Requests in progress stop. The model loads again (about 30 s to 2 min)." if run else "The model loads (about 30 s to 2 min).",
        "If the new server does not start, CARL starts the old server again." if run else "", "",
        buttons("  ", [("Yes, restart (y)" if run else "Yes, start (y)", "setyes"), ("Cancel (n)", "setno")])], w, 2)


class ServerPanel:
    """Draws the Server panel."""

    def __init__(self, svc: SettingsService, lines: ModelLines, card: ModelCard, config_file: str, home: str) -> None:
        self.svc = svc
        self.lines = lines
        self.card = card
        self.config_file = config_file
        self.home = home

    def draw(self, ui: UIState, p: Pending, d: ServerData, cols: int, height: int, port: int) -> List[Row]:
        """The pending settings p beside what runs now, the fit line and Apply; below it, full width,
        the model's card (click its title: collapsed / normal / full). Scrolls with the wheel / PgUp PgDn."""
        svc = self.svc
        run = svc.running(d)
        rws = svc.rows(p)
        ui.set_row = min(ui.set_row, len(rws) - 1)
        side = cols >= 130                                 # room for the model list beside the settings
        w = cols - 1                                       # the card, full width
        iw = w - 4 - (LIST_W + 3 if side else 0)          # its settings column (the model list takes the right side)
        key = rws[ui.set_row].key

        def main(mw: int) -> List[CardLine]:
            vw = 28 if mw >= 66 else 18                    # value column: room for "draft-mtp,ngram-mod"
            L: List[CardLine] = []
            if svc.models.error:                            # a broken catalogue / models.json: say so here
                L += [f"{RED}{x}{R}" for x in wwrap(f"model list unavailable: {svc.models.error}", mw)[:3]]
                L += cwrap(f"{DIM}Correct the file. ./carl.sh models shows the same error. CARL reads the list again "
                           f"every 10 s.{R}", mw) + [""]
            L.append(f"{DIM}{'':2}{'setting':<14}{'new':<{vw + 8}}{'running now':<18}{R}")
            for i, (k, label, _, _, _) in enumerate(rws):
                L.append(self._row(ui, p, run, i, k, label, vw))
            L.append("")
            if ui.restart:
                L += cwrap(f"{YEL}{ui.restart}{R}", mw)
            else:
                ok = svc.fit_cached(p)[0]
                L += button_rows("", [("Apply and restart (a)" if run else "Start server (a)",
                                       "setapply" if ok else "setnofit"),
                                      ("Revert (r)", "setrevert"), ("Tuned values (x)", "setdefaults")], mw)
            return L

        L = with_side(main, row_instruction(key), [("About this setting", about_lines(p, key)),
                                                   ("Status", self.status_lines(p)),
                                                   ("Colours", [COLOURS])], iw, main_w=74)
        ui.keys = [("↑↓", "select"), ("←→", "change"), *([("Enter", "pick a model")] if key == "model" else []),
                   ("a", "apply and restart" if run else "start"), ("r", "revert"), ("x", "tuned values"),
                   ("A", "auto fit"), ("[ ]", "panels")]
        ui.keys_more = list(KEYS_MORE)
        srv = "llama.cpp" if run else "no server"
        if side:                                           # the model list: the right column of the same card
            L = merge_columns(L, self.model_list(ui, p, LIST_W, len(L)), iw)
        else:
            L += ["", *self.model_list(ui, p, w - 4, 16)]
        rows = indent(draw_card("settings", "SERVER SETTINGS", f"{DIM}running: {srv} · port {port}{R}", L, w, 2))
        ui.levels.setdefault("modelinfo", 1)
        rows += indent(self.card.draw(p, key, cols - 1, ui.levels["modelinfo"]))
        if ui.confirm:
            rows = indent(confirm_restart(bool(run), port, min(w, 80))) + rows
        ui.set_scroll = max(0, min(ui.set_scroll, len(rows) - height))
        return rows[ui.set_scroll:ui.set_scroll + height]

    def status_lines(self, p: Pending) -> List[CardLine]:
        """Status: does the setup fit and is the model here, auto fit's pick, the settings file."""
        svc = self.svc
        L: List[CardLine] = [lv("fit", svc.fit_cached(p)[1])]
        af = svc.auto_fit(p)
        if af and af.pick and af.plan:
            where = "" if af.pick.downloaded else f" · {YEL}not downloaded{R}"
            text = f"{CYN}{af.pick.name}{R}, {af.plan.label()}{where} · {DIM}the reason and Use this: Auto fit panel (A){R}"
        else:
            text = f"{RED}{af.because() if af else svc.models.fit_error or 'unavailable'}{R} {DIM}(Auto fit panel: A){R}"
        L.append(Ln(lv("auto fit", text), act=f"sp:{SP_FIT}"))
        L.append(lv("file", home_short(self.config_file, self.home) + f"{DIM} · ./carl.sh config show lists all keys{R}"))
        return L

    def model_list(self, ui: UIState, p: Pending, w: int, height: int) -> List[CardLine]:
        """The model selector in the settings card (w columns, height lines): click a model (or m,
        then ↑↓ Enter) to pick it; sort / filter drop-downs. Only names and status: the MODEL card
        below has the rest."""
        items = ["auto"] + [m["name"] for m in self.lines.visible(ui.msort, ui.mfilter)]
        cur = str(p.get("model", "auto"))
        if not ui.slist:                                    # the cursor follows the chosen model
            ui.srow = items.index(cur) if cur in items else 0
        ui.srow = max(0, min(ui.srow, len(items) - 1))
        ms = {m["name"]: m for m in self.svc.models.get()}
        af = self.svc.auto_fit(p)
        pick = af.name if af else None
        L: List[CardLine] = [heading("Choose a model", w),
                             *cwrap(f"{DIM}● downloaded ○ not · ★ auto fit's pick · {RED}red{R}{DIM} = too big{R}", w),
                             *arrange_lines(ui.msort, ui.mfilter, w)]
        vis = max(height - len(L) - 1, 3)
        top = min(max(ui.srow - vis // 2, 0), max(len(items) - vis, 0))
        for i in range(top, min(top + vis, len(items))):
            name = items[i]
            st = ms[name]["status"] if name in ms else ""
            dot = (f"{GRN}●{R}" if st == "downloaded" else f"{YEL}◐{R}" if st == "partial"
                   else f"{DIM}○{R}" if name != "auto" else f"{CYN}★{R}")
            chosen = name == cur
            col = (B + CYN) if chosen else RED if name in ms and self.lines.too_big(ms[name]) else ""
            if name == "auto":
                text = f"{dot} {col}auto{R} {DIM}→ {self.svc.resolved_model(dict(p, model='auto'))}{R}"
            else:
                text = f"{dot} {col}{name}{R}" + (f" {CYN}★{R}" if name == pick else "")
            if ui.slist and i == ui.srow:
                text = f"\x1b[7m{fit(text, w - 2)}{R}"
            L.append(Ln(("› " if chosen else "  ") + text, act=f"smodel:{name}"))
        more = len(items) - vis
        L.append(f"{DIM}{top + 1}-{min(top + vis, len(items))} of {len(items)} · " if more > 0 else DIM)
        L[-1] = str(L[-1]) + ("↑ ↓ then Enter · m or Esc: done" if ui.slist else "click one, or press m") + R
        return L

    def _row(self, ui: UIState, p: Pending, run: Pending, i: int, key: str, label: str, vw: int) -> Ln:
        """One setting: [<] new value [>]  * running value. The value is coloured (value_color)."""
        sel = i == ui.set_row
        val = shown_value(key, p[key])
        if key == "model" and p[key] == "auto":
            val = f"auto: {self.svc.resolved_model(p)}"
        if sel and ui.edit is not None:
            val = ui.edit + "▏"
        rv: object = run.get(key, "N/A")
        rv = "" if key in NOT_RUNNING else rv
        rvs = "auto" if key == "cache" and rv == 0 else shown_value(key, rv)
        mark = (f"{YEL}*{R}" if run and str(p[key]) != str(run.get(key))
                and key not in UNMARKED else " ")
        col = self.svc.value_color(key, p) or CYN
        pre = f"{CYN}{B}›{R} " if sel else "  "
        text = f"{pre}{(B if sel else '')}{label:<14}{R}"
        spans = [(0, 2 + 14, f"setrow:{i}")]
        c = vlen(text)
        middle = fit(val, vw) if vlen(val) > vw else f"{val:^{vw}}"
        for b, act in (("[<]", f"setdec:{i}"), (middle, f"setrow:{i}" if key != "model" else "setpick"), ("[>]", f"setinc:{i}")):
            spans.append((c, c + vlen(b), act))
            text += f"{col}{b}{R}"
            c += vlen(b)
        text += f" {mark}  {DIM}{fit(rvs, 30) if vlen(rvs) > 30 else rvs}{R}"
        return Ln(text, spans=spans)
