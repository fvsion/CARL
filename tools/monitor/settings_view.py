"""The Settings tab: the Server panel (settings + what the model is and why it is tuned so),
the Models panel (catalogue, models folder, downloads, Hugging Face), the Auto-tune panel,
the model picker and confirmations. Draws from the UI state; reads models and config.json
through SettingsService (the ModelStore port)."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import List, Mapping, Optional, Sequence, Tuple

from .fmt import (B, CYN, DIM, GRN, R, RED, YEL, CardLine, Ln, Row, bar, button_rows, buttons, ctx_label, cwrap,
                  draw_card, dur, fit, heading, home_short, indent, lv, merge_columns, Section, size, vlen, with_side, wwrap)
from carl_core.domain.autofit import GOAL_TEXT, GOALS, SCOPE_TEXT, SCOPES, AutoFit, as_goal, as_scope, gib
from carl_core.domain.cards import CHOICE_TEXT
from carl_core.domain.tuning import DEPTH_TEXT, DEPTHS, as_depth

from .diskcache import PROMPT, CacheConfig, CacheFile, describe, gb as gb_text, shared_saving, used
from .model import ModelInfo, RouterModel, ServerData, flag, jdict
from .settings import (ADV_WARN, LLAMA_ADV, MODEL_ROW_KEYS, NOT_RUNNING, SET_HELP, UNMARKED, Pending,
                       SettingsService, fmt_val, row_instruction, shown_value)
from .arrange import FILTERS, MIN_FIT, SORTS, arrange, label as arrange_label, speed_of
from .state import SP_FIT, SUBPANELS, TUNE_ALL, Confirm, Download, PickItem, Picker, TuneRun, UIState

DISK_CHOICES = (2, 5, 10, 20, 50)                      # the Caching panel's disk limits (GB)
AUTO_CHOICES = (30, 120, 300, 600)                    # save = auto: seconds of unsaved reading (default 120)
SAVE_TEXT = (("auto", "auto"), ("turn", "every turn"), ("switch", "on a switch"), ("stop", "before a stop"))
SAVE_HELP = (
    ("auto", "auto: when the part not saved yet would take AUTO to read again (this model's measured read speed; "
             "the Auto after row), and before a session leaves the server (its slot is needed, a router switch, a "
             "stop). Few writes; a crash loses at most AUTO of reading per session."),
    ("turn", "every turn: after each reply. Nothing is ever lost; the most disk writes (a long session: up to ~1 GB per "
             "turn on the 35B)."),
    ("switch", "on a switch: before a session leaves the server (its slot is needed, a router switch, a dashboard stop "
               "or restart). A crash or a stop outside the dashboard (Ctrl-C) loses what wasn't saved."),
    ("stop", "before a stop: only before the dashboard stops or restarts the server, or a router switch. Sessions moved "
             "to the RAM cache meanwhile are lost at the stop."),
)
SWA_TEXT = (("auto", "auto"), ("full", "full cache"), ("window", "window only"))
SWA_HELP = (
    ("auto", "auto: models with sliding-window layers (Gemma) keep every layer at full length when that fits this Mac "
             "(saved states can be restored), else only the window (less memory, no restores). Applies at the next start."),
    ("full", "full cache: restores work; more memory (Gemma 4 E4B at 2 × 96K: +2.3 GB). Applies at the next start."),
    ("window", "window only: the least memory; this model's saved states can't be restored (llama.cpp re-reads "
               "them). Applies at the next start."),
)
LIST_W = 40                                            # the Server panel's model list
COLOURS = (f"{GRN}green{R} = tuned / fast · {YEL}yellow{R} = changed / slower · "
           f"{RED}red{R} = very slow / no MTP head")
MODEL_HEADER = f"{DIM}{'':2}{'model':<26} {'size':>8}  {'status':<10} {'fits':>5} {'speed':>7} role and good for{R}"
PICKER_FOOT = ("Press ↑ ↓ to select a model and Enter to choose it, Esc to cancel. ★ = auto fit's pick · fits = the "
               "largest window per slot that fits this Mac (q4_0, 1 slot; red = too big). To download or add any GGUF "
               "from Hugging Face: press ] for the Models panel, then h.")
WHY_KEY = {"specn": "spec", "presence": "temp", "top_k": "temp", "top_p": "temp", "min_p": "temp", "repeat": "temp"}


@dataclass
class ModelsDir:
    """Where models are kept and the free disk there."""
    path: str
    free: int


def subpanel_bar(sp: int) -> Row:
    """The Server / Models / Auto-tune switch, the selected panel in reverse video."""
    text, spans, col = "", [], 0
    for i, name in enumerate(SUBPANELS):
        lab = f" {name} "
        text += (f"\x1b[1;7m{lab}{R}" if i == sp else f"{DIM}{lab}{R}") + " "
        spans.append((col, col + len(lab), f"sp:{i}"))
        col += len(lab) + 1
    text += f"{DIM}  press [ or ] to switch panels{R}"
    return " " + text, [(1 + a, 1 + b, act) for a, b, act in spans]


def selectable(text: str, sel: bool, w: int, act: str) -> Ln:
    """A list line: › and reverse video when selected."""
    return Ln((f"{CYN}{B}›{R} " if sel else "  ") + (f"\x1b[7m{fit(text, w - 8)}{R}" if sel else text), act=act)


GOOD_FOR_COLOUR = {"agent coding": GRN, "hard code": CYN, "chat & writing": B, "uncensored": RED}


def good_for_chip(tag: str) -> str:
    """A "good for" tag, coloured by kind."""
    return f"{GOOD_FOR_COLOUR.get(tag, '')}[{tag}]{R}"


def label_wrap(label: str, text: str, w: int, colour: str = "") -> List[CardLine]:
    """A bold label on the first line, the text wrapped under it."""
    pad = max(len(label) + 1, 11)                         # the same text column as lv(..., 11)
    lines = wwrap(text, w - pad)
    out: List[CardLine] = [f"{B}{label:<{pad}}{R}{colour}{lines[0]}{R}"]
    return out + [f"{' ' * pad}{colour}{x}{R}" for x in lines[1:]]


def speed_line(m: ModelInfo, tune: object) -> str:
    """This Mac's measured speed after Auto-tune, else the catalogue's figure and the Mac it came from."""
    t = jdict(tune)
    st = jdict(t.get("settings"))
    best = jdict(jdict(jdict(t.get("results")).get("speculation")).get(f"{st.get('spec')}:{st.get('spec_n')}"))
    if best:
        return (f"{GRN}{best.get('prose')} tok/s prose · {best.get('code')} code · {best.get('edit')} re-emitting{R} "
                f"{DIM}(measured here, {t.get('date')}){R}")
    measured = m.get("measured") or []
    ref = jdict(measured[0]) if measured else {}
    if ref.get("decode"):
        return f"{ref['decode']} {DIM}(on an {ref.get('machine', '?')}; run Auto-tune for this Mac){R}"
    return f"{DIM}not measured yet: run Auto-tune{R}"


def parallel_text(par: object) -> str:
    """Auto-tune's parallel step: total decode speed with n requests at once, and each one's."""
    rows = [r for r in (par if isinstance(par, list) else []) if isinstance(r, list) and len(r) >= 2]
    return "decoding in total: " + " · ".join(f"{int(r[0])} at once {float(r[1]):.0f} tok/s ({float(r[1]) / r[0]:.0f} each)"
                                              for r in rows if r[0])


def arrange_lines(sort: int, filt: int, w: int) -> List[CardLine]:
    """Compact sort / filter controls (a narrow list): "sort: quality ▾ 2/5", click for a drop-down."""
    so, fi = arrange_label(sort, filt)
    out: List[CardLine] = []
    for kind, val, idx, n, act in (("sort", so, sort, len(SORTS), "msortpick"), ("show", fi, filt, len(FILTERS), "mfilterpick")):
        text = f"{kind}: {CYN}{val} ▾{R} {DIM}{idx % n + 1}/{n}{R}"
        out.append(Ln(fit(text, w), act=act))
    return out


def arrange_chips(sort: int, filt: int, w: int) -> List[CardLine]:
    """Every sort and filter option as a clickable chip, the current one highlighted (a wide list)."""
    out: List[CardLine] = []
    for kind, opts, cur, act in (("Sort", SORTS, sort % len(SORTS), "msortset"), ("Show", FILTERS, filt % len(FILTERS), "mfilterset")):
        text, col = f"{B}{kind:<5}{R}", 5
        spans: List[Tuple[int, int, str]] = []
        for i, o in enumerate(opts):
            chip = f" {o} "
            if col + len(chip) + 1 > w:                     # wrap onto another line
                out.append(Ln(text, spans=spans))
                text, spans, col = " " * 5, [], 5
            text += (f"\x1b[7m{chip}{R}" if i == cur else f"{DIM}{chip}{R}") + " "
            spans.append((col, col + len(chip), f"{act}:{i}"))
            col += len(chip) + 1
        out.append(Ln(text, spans=spans))
    return out


class SettingsView:
    """Draws the Settings panels."""

    def __init__(self, svc: SettingsService, config_file: str, home: str) -> None:
        self.svc = svc
        self.config_file = config_file
        self.home = home

    # ------------------------------------------------------------ panel 1: server settings + model info
    def server(self, ui: UIState, p: Pending, d: ServerData, cols: int, height: int, port: int) -> List[Row]:
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
                L += cwrap(f"{DIM}Fix the file (./carl.sh models shows the same error); the list is read again every "
                           f"10 s.{R}", mw) + [""]
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

        L = with_side(main, row_instruction(key), [("About this setting", self.about_lines(p, key)),
                                                   ("Status", self.status_lines(ui, p)),
                                                   ("Colours", [COLOURS])], iw, main_w=74)
        ui.keys = [("↑↓", "select"), ("←→", "change"), *([("Enter", "pick a model")] if key == "model" else []),
                   ("a", "apply and restart" if run else "start"), ("r", "revert"), ("x", "tuned values"),
                   ("A", "auto fit"), ("[ ]", "panels")]
        ui.keys_more = self.keys_more(bool(run))
        srv = "llama.cpp" if run else "no server"
        if side:                                           # the model list: the right column of the same card
            L = merge_columns(L, self.model_list(ui, p, LIST_W, len(L)), iw)
        else:
            L += ["", *self.model_list(ui, p, w - 4, 16)]
        rows = indent(draw_card("settings", "SERVER SETTINGS", f"{DIM}running: {srv} · port {port}{R}", L, w, 2))
        ui.levels.setdefault("modelinfo", 1)
        rows += indent(self.model_info(p, key, cols - 1, ui.levels["modelinfo"]))
        if ui.confirm:
            rows = indent(self._confirm_restart(bool(run), port, min(w, 80))) + rows
        ui.set_scroll = max(0, min(ui.set_scroll, len(rows) - height))
        return rows[ui.set_scroll:ui.set_scroll + height]

    @staticmethod
    def about_lines(p: Pending, key: str) -> List[CardLine]:
        """About this setting: what the selected row does (the side column wraps it)."""
        return [SET_HELP[key], *([f"{YEL}{ADV_WARN}{R}"] if p.get("adv") == "shown" else [])]

    def status_lines(self, ui: UIState, p: Pending) -> List[CardLine]:
        """Status: does the setup fit and is the model here, auto fit's pick, the settings file."""
        svc = self.svc
        L: List[CardLine] = [lv("fit", svc.fit_cached(p)[1])]
        af = svc.auto_fit(p)
        if af and af.pick and af.plan:
            where = "" if af.pick.downloaded else f" · {YEL}not downloaded{R}"
            text = f"{CYN}{af.pick.name}{R}, {af.plan.label()}{where} · {DIM}why and Use this: the Auto fit panel (A){R}"
        else:
            text = f"{RED}{af.because() if af else svc.models.fit_error or 'unavailable'}{R} {DIM}(Auto fit panel: A){R}"
        L.append(Ln(lv("auto fit", text), act=f"sp:{SP_FIT}"))
        L.append(lv("file", home_short(self.config_file, self.home) + f"{DIM} · ./carl.sh config show lists every key{R}"))
        return L

    @staticmethod
    def keys_more(running: bool) -> List[str]:
        """What the ? card adds for the Server panel."""
        return ["* marks a value that differs from the running server. Scroll with the mouse wheel or PgUp / PgDn.",
                "Type a number and press Enter on the context and the other number rows.",
                "More models: press ] for the Models panel, then h to add one from Hugging Face.",
                "After changing the context or slots, run install.sh again on the clients (the Connect tab says when)."]

    def model_list(self, ui: UIState, p: Pending, w: int, height: int) -> List[CardLine]:
        """The model selector in the settings card (w columns, height lines): click a model (or m,
        then ↑↓ Enter) to pick it; sort / filter drop-downs. Only names and status: the MODEL card
        below has the rest."""
        items = ["auto"] + [m["name"] for m in self.visible(ui.msort, ui.mfilter)]
        cur = str(p.get("model", "auto"))
        if not ui.slist:                                    # the cursor follows the chosen model
            ui.srow = items.index(cur) if cur in items else 0
        ui.srow = max(0, min(ui.srow, len(items) - 1))
        ms = {m["name"]: m for m in self.svc.models.get()}
        af = self.svc.auto_fit(p)
        pick = af.name if af else None
        tw = w
        L: List[CardLine] = [heading("Choose a model", tw),
                             *cwrap(f"{DIM}● downloaded ○ not · ★ auto fit's pick · {RED}red{R}{DIM} = too big{R}", tw),
                             *arrange_lines(ui.msort, ui.mfilter, tw)]
        vis = max(height - len(L) - 1, 3)
        top = min(max(ui.srow - vis // 2, 0), max(len(items) - vis, 0))
        for i in range(top, min(top + vis, len(items))):
            name = items[i]
            st = ms[name]["status"] if name in ms else ""
            dot = (f"{GRN}●{R}" if st == "downloaded" else f"{YEL}◐{R}" if st == "partial"
                   else f"{DIM}○{R}" if name != "auto" else f"{CYN}★{R}")
            chosen = name == cur
            col = (B + CYN) if chosen else RED if name in ms and self.too_big(ms[name]) else ""
            if name == "auto":
                text = f"{dot} {col}auto{R} {DIM}→ {self.svc.resolved_model(dict(p, model='auto'))}{R}"
            else:
                text = f"{dot} {col}{name}{R}" + (f" {CYN}★{R}" if name == pick else "")
            if ui.slist and i == ui.srow:
                text = f"\x1b[7m{fit(text, tw - 2)}{R}"
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

    def _confirm_restart(self, run: bool, port: int, w: int) -> List[Row]:
        first = ("This stops the server and starts it again with the new settings." if run else
                 f"This starts llama.cpp on port {port}.")
        return draw_card("confirm", "START?" if not run else "RESTART?", "", [
            "", first,
            "Requests in progress stop. The model loads again (about 30 s to 2 min)." if run else "The model loads (about 30 s to 2 min).",
            "If the new server does not start, the old one starts again." if run else "", "",
            buttons("  ", [("Yes, restart (y)" if run else "Yes, start (y)", "setyes"), ("Cancel (n)", "setno")])], w, 2)

    def model_info(self, p: Pending, key: str, w: int, lvl: int = 1) -> List[Row]:
        """The selected model's card: what it is for and why, then how it is tuned.
        Collapsed: name, role and tags in the title. Normal: good for, why use it, trade-offs,
        hardware, speed, recommended values next to yours, context zones, why for the selected
        row. Full: plus the description, what uncensored means, the alternatives, the quality
        rank, why for every tuned value, Auto-tune's measurements, source and file."""
        svc = self.svc
        name = svc.resolved_model(p)
        m = svc.models.by_name(name)
        if not m:
            return draw_card("modelinfo", "MODEL", "", [f"{RED}unknown model {name}{R}"], w, lvl)
        rec, src = svc.recommended(name)
        tune = jdict(m.get("local")).get("tune")
        tags = [m["arch"].upper() if m.get("arch") else None, m.get("quant"),
                f"{RED}abliterated{R}" if m.get("abliterated") else None,
                ("custom · your card" if jdict(m.get("local")).get("card") else "custom") if m.get("custom") else None,
                f"{GRN}tuned here {tune['date']}{R}" if tune else f"{DIM}catalogue tune{R}" if not m.get("custom") else f"{YEL}not tuned{R}"]
        role = str(m.get("role") or m.get("summary") or "")
        title_info = (f"{B}{m.get('label', name)}{R}  {CYN}{role}{R}  " + f"{DIM} · {R}".join(t for t in tags if t))
        tw = w - 4
        L: List[CardLine] = []
        af = svc.auto_fit(p)
        if p.get("model") == "auto" or (af and af.name == name):
            L += self.auto_fit_lines(p, af, svc.resolved_model(dict(p, model="auto")), tw, lvl)
        good = [str(t) for t in (m.get("good_for") or [])]
        if good:
            L.append(f"{B}{'Good for':<11}{R}" + "  ".join(good_for_chip(t) for t in good))
        for field, label, colour in (("why_use", "Why use it", ""), ("trade_offs", "Trade-offs", YEL)):
            text = str(m.get(field) or "")
            if text:
                L += label_wrap(label, text, tw, colour)
        if m.get("hardware"):
            L += label_wrap("Hardware", str(m["hardware"]), tw)
        if m.get("thinking"):
            L.append(f"{B}{'Thinking':<11}{R}{CHOICE_TEXT.get(str(m['thinking']), str(m['thinking']))}")
        L += cwrap(f"{B}{'Speed':<11}{R}{speed_line(m, tune)}", tw, " " * 11)
        par = jdict(jdict(tune).get("results")).get("parallel") or []
        if par:
            L += cwrap(f"{B}{'Parallel':<11}{R}{parallel_text(par)} {DIM}(measured here; more slots: Server panel){R}",
                       tw, " " * 11)
        if lvl == 2:
            if m.get("uncensored"):
                L += [""] + label_wrap("Uncensored", str(m["uncensored"]), tw, RED)
            alts = [jdict(a) for a in (m.get("pick_instead") or [])]
            if alts:
                L += ["", f"{B}Pick instead{R}"]
                L += [x for a in alts for x in cwrap(f"  {CYN}{a.get('model')}{R} {DIM}when{R} {a.get('when')}", tw, "    ")]
            if m.get("rank") and m.get("custom"):
                L += cwrap(f"{B}{'Quality':<11}{R}rank {m['rank']} {DIM}(your card, not measured: 1 = best, the catalogue "
                           f"ranks 1-5; auto fit {'may pick it' if m.get('auto_fit') else 'leaves it out'}){R}",
                           tw, " " * 11)
            elif m.get("rank"):
                L += cwrap(f"{B}{'Quality':<11}{R}rank {m['rank']} {DIM}(1 = best: parameters and density first, then "
                           f"quantization; speed is the reverse){R}", tw, " " * 11)
            desc = wwrap(m.get("description", ""), tw)
            if desc:
                L += ["", *[f"{DIM}{x}{R}" for x in desc]]
        elif not good and m.get("summary"):                 # custom models: no card text, the summary
            L += [f"{CYN}{x}{R}" for x in wwrap(m["summary"], tw)]
        if m.get("custom") and not jdict(m.get("local")).get("card"):
            L += cwrap(f"{DIM}No card yet: press ] for the Models panel, select it and press e to say what it is good "
                       f"for (or ./carl.sh card {name}).{R}", tw)
        L += ["", *cwrap(f"{B}Recommended for this model{R} {DIM}(Auto-tune > catalogue; yellow = yours differs){R}", tw)]
        cells = []
        for pk, lab in (("kv", "KV cache"), ("ctx", "context"), ("slots", "slots"), ("spec", "speculation"),
                        ("spec_n", "draft tokens"), ("temp", "temperature")):
            v = ctx_label(rec[pk]) if pk == "ctx" else rec[pk]
            rk = next((rk for rk, k in MODEL_ROW_KEYS.items() if k == pk), pk)
            mine = str(fmt_val(rk, rec[pk])) == str(p.get(rk))
            cells.append(f"{lab:<13}{(GRN if mine else YEL)}{v}{R} {DIM}{src.get(pk, '')}{'' if mine else ' · you: ' + str(p.get(rk))}{R}")
        half = (tw - 2) // 2
        if half >= 52:                                      # two columns when the card is wide
            for i in range(0, len(cells), 2):
                L.append("  " + fit(cells[i], half) + (cells[i + 1] if i + 1 < len(cells) else ""))
        else:
            L += [x for c in cells for x in cwrap("  " + c, tw, " " * 15)]
        zg, zs, _ = svc.store.ctx_zones(m)
        L += cwrap(f"  {'zones':<13}{GRN}≤{ctx_label(zg)} fast{R} · {YEL}≤{ctx_label(zs)} slow{R} · {RED}>{ctx_label(zs)} very "
                   f"slow{R} {DIM}(cold re-read of a full window; "
                   f"{'measured here' if tune and tune.get('ctx_zones') else 'catalogue'}){R}", tw, " " * 15)
        labels = {r.key: r.label for r in svc.schema.llama + LLAMA_ADV}
        why_all = jdict(m.get("why"))
        sel_wk = WHY_KEY.get(key, key)
        order = [sel_wk] + [k for k in ("spec", "ctx", "kv", "slots", "temp") if k != sel_wk] if lvl == 2 else [sel_wk]
        for wk in order:
            why = self._why(m, tune, wk, why_all)
            if why:
                L += ["", f"{B}Why{R} {DIM}({labels.get(wk if wk != sel_wk else key, wk)}){R}"]
                L += wwrap(why, tw)[: (12 if lvl == 2 else 6)]
        if lvl == 1:
            L += ["", *cwrap(f"{DIM}Click the MODEL title for the full card: alternatives, what uncensored means, every "
                             f"reason, measurements.{R}", tw)]
        if lvl == 2:
            L += self._tune_table(tune)
            hf = jdict(m.get("hf"))
            L.append("")
            if hf.get("repo"):
                L += cwrap(lv("source", f"huggingface.co/{hf['repo']} · {hf.get('file')}"
                              + (f" @ {str(hf.get('revision'))[:8]}" if hf.get("revision") else ""), 8), tw, " " * 8)
            L += cwrap(lv("file", home_short(str(m.get("path", "")), self.home), 8), tw, " " * 8)
        return draw_card("modelinfo", "MODEL", title_info, L, w, lvl)

    def auto_fit_lines(self, p: Pending, af: Optional[AutoFit], start: str, tw: int, lvl: int) -> List[CardLine]:
        """Why auto fit picked its model (goal, scope, the plan), what a start uses meanwhile when the
        pick isn't downloaded, and the better-ranked models it passed over (all on the full card)."""
        pad = " " * 11
        if af is None:
            return label_wrap("Auto fit", f"unavailable: {self.svc.models.fit_error or 'no model list'}", tw, YEL) + [""]
        head = (f"picked {af.pick.name}, {af.plan.label()}: {af.because()}" if af.pick and af.plan
                else f"nothing fits: {af.because()}")
        L = label_wrap("Auto fit", head, tw, CYN if af.pick else RED)
        L += [f"{pad}{x}" for x in cwrap(f"{DIM}goal: {GOAL_TEXT[as_goal(p.get('goal'))]} · from: "
                                          f"{SCOPE_TEXT[as_scope(p.get('scope'))]} (change them in the Auto fit panel: "
                                          f"A) · stock models only{R}", tw - 11)]
        if af.pick and not af.pick.downloaded:
            meanwhile = (f"model auto starts {start} (the best downloaded model that fits) until then"
                         if p.get("model") == "auto" else "it must be downloaded before a start")
            L += [f"{pad}{YEL}{x}{R}" for x in wwrap(f"{af.pick.name} is not downloaded: {meanwhile}. The Auto fit "
                                                      f"panel (A) downloads it (or ] for the Models panel, then d).",
                                                      tw - 11)]
        shown = af.rejected if lvl == 2 else af.rejected[:4]
        for i, r in enumerate(shown):
            lines = wwrap(r.line(), tw - 14)
            L.append(f"{B}{'Passed over' if i == 0 else '':<11}{R} {DIM}· {lines[0]}{R}")
            L += [f"{pad}   {DIM}{x}{R}" for x in lines[1:]]
        if len(shown) < len(af.rejected):
            L.append(f"{pad}{DIM}+{len(af.rejected) - len(shown)} more: click the MODEL title for the full card{R}")
        return L + [""]

    @staticmethod
    def _why(m: ModelInfo, tune: object, wk: str, why_all: Mapping[str, object]) -> str:
        """The reason for one tuned value; for speculation, this Mac's measurements when tuned."""
        t = jdict(tune)
        if t and wk == "spec":
            res = jdict(jdict(t.get("results")).get("speculation"))
            if res:
                return "Measured here (" + str(t.get("date")) + "): " + " · ".join(
                    f"{mode.replace('draft-mtp', 'MTP').replace('ngram-mod', 'n-gram').replace(',', '+')} "
                    f"{jdict(r).get('prose')}/{jdict(r).get('code')}/{jdict(r).get('edit')}"
                    for mode, r in res.items()) + " tok/s (prose/code/re-emit)."
        return str(why_all.get(wk) or "")

    @staticmethod
    def _tune_table(tune: object) -> List[CardLine]:
        """Auto-tune's speculation table and prompt reading, the chosen mode highlighted."""
        t = jdict(tune)
        res = jdict(jdict(t.get("results")).get("speculation"))
        if not res:
            return []
        st = jdict(t.get("settings"))
        best = f"{st.get('spec')}:{st.get('spec_n')}"
        L: List[CardLine] = ["", f"{B}Auto-tune on this Mac{R} {DIM}{t.get('date')} · {t.get('machine', '?')}{R}",
                             f"  {DIM}{'speculation':<24}{'prose':>7}{'code':>7}{'re-emit':>9}{'score':>8}{R}"]
        for mode, r in res.items():
            rr = jdict(r)
            hi = GRN + B if mode == best else ""
            L.append(f"  {hi}{mode:<24}{rr.get('prose', ''):>7}{rr.get('code', ''):>7}{rr.get('edit', ''):>9}{rr.get('score', ''):>8}{R}")
        return L

    # ------------------------------------------------------------ model lines and the picker
    def too_big(self, m: ModelInfo) -> bool:
        """Known not to fit this Mac: less than a 32K window (1 slot, q4_0)."""
        mx = self.svc.max_ctx(m)
        return mx is not None and mx < MIN_FIT

    def model_line(self, m: ModelInfo, pick: Optional[str] = None) -> str:
        """One model in a list: name (★ = auto fit's pick, red = too big for this Mac), size,
        status, the largest window that fits, tuned, summary."""
        st = {"downloaded": f"{GRN}downloaded{R}", "partial": f"{YEL}partial{R}", "missing": f"{DIM}not here{R}"}[m["status"]]
        sp = speed_of(m)
        speed = (f"{GRN if sp[1] else DIM}{sp[0]:>3.0f} t/s{R}" if sp else f"{DIM}{'?':>7}{R}")
        mx = self.svc.max_ctx(m)
        fitc = (f"{(GRN if mx >= 65536 else YEL if mx >= 32768 else RED)}{ctx_label(mx) if mx else 'no fit':>5}{R}"
                if mx is not None else f"{DIM}{'?':>5}{R}")
        star = f" {CYN}★{R}" if m["name"] == pick else ""
        name = f"{RED if self.too_big(m) else ''}{m['name']}{R}{star}"
        name += " " * max(0, 26 - vlen(name))
        head = f"{name} {size(m['bytes']):>8}  {st:<10}{' ' * max(0, 10 - vlen(st))} {fitc} {speed} "
        tags = " ".join(good_for_chip(str(t)) for t in (m.get("good_for") or []))
        return head + f"{CYN}{m.get('role') or ''}{R} {tags} " + ("" if m.get("role") else f"{DIM}{m.get('summary', '')}{R}")

    def picker(self, pk: Picker, cols: int, height: int) -> List[Row]:
        """A drop-down list around its selection, with the selected model's description."""
        w = cols - 2
        n = len(pk.items)
        vis = max(height - 9, 3)
        top = min(max(pk.sel - vis // 2, 0), max(n - vis, 0))
        L: List[CardLine] = [pk.header or MODEL_HEADER]
        for i in range(top, min(top + vis, n)):
            item = pk.items[i][1]
            L.append(selectable(item if isinstance(item, str) else self.model_line(item, pk.mark), i == pk.sel, w,
                                f"pick:{i}"))
        sel_item = pk.items[pk.sel][1]
        if not isinstance(sel_item, str):
            L += ["", *[f"{DIM}{x}{R}" for x in wwrap(sel_item.get("why_use") or sel_item.get("description")
                                                        or sel_item.get("summary", ""), w - 6)[:3]]]
            if sel_item.get("trade_offs"):
                L += [f"{YEL}{x}{R}" for x in wwrap(str(sel_item["trade_offs"]), w - 6)[:2]]
        elif pk.sel == 0 and pk.note:
            L += ["", *cwrap(f"{DIM}{pk.note}{R}", w - 6)]
        L += ["", buttons("", [("Choose (Enter)", "pickok"), ("Cancel (Esc)", "pickno")]),
              *cwrap(f"{DIM}{pk.foot or PICKER_FOOT}{R}", w - 4)]
        return indent(draw_card("picker", pk.title, f"{DIM}{n} {pk.noun}{R}", L, w, 2))

    def visible(self, sort: int, filt: int) -> List[ModelInfo]:
        """The models in the current sort order, filtered (the Models panel and the drop-down)."""
        return arrange(self.svc.models.get(), sort, filt, self.svc.max_ctx)

    def arrange_picker(self, kind: str, sort: int, filt: int, reopen: Optional[str] = None) -> Picker:
        """A drop-down of every sort (or filter) option, the current one selected."""
        opts, cur = (SORTS, sort) if kind == "sort" else (FILTERS, filt)
        items: List[PickItem] = [(str(i), f"{i + 1:>2}. {o}") for i, o in enumerate(opts)]
        return Picker(title="SORT MODELS BY" if kind == "sort" else "SHOW MODELS", items=items,
                      on_pick="picksort" if kind == "sort" else "pickfilter", sel=cur % len(opts),
                      noun="options", header=f"{DIM}  press ↑ ↓ to select, Enter to choose{R}",
                      foot="Press ↑ ↓ to select an option, Enter to choose it, Esc to cancel.", reopen=reopen)

    def model_picker(self, cur: str, sort: int = 0, filt: int = 0, p: Optional[Pending] = None) -> Picker:
        """A drop-down of the models ("auto" on top) in the chosen order and filter; s / f change them.
        p: the pending settings (auto fit's goal and scope)."""
        ms = self.visible(sort, filt)
        q: Pending = dict(p or {}, model="auto")
        af = self.svc.auto_fit(q)
        start = self.svc.resolved_model(q)
        pick = af.name if af else None
        items: List[PickItem] = [("auto", f"{CYN}★{R} {'auto':<23} {DIM}→ {start}{R}")]
        note = (f"auto = auto fit's pick for this Mac: {af.summary()}." if af and af.pick else
                f"auto fit: {af.because() if af else self.svc.models.fit_error or 'unavailable'}.")
        if pick and pick != start:
            note += f" {pick} is not downloaded: a start uses {start} until then."
        items += [(m["name"], m) for m in ms]
        so, fi = arrange_label(sort, filt)
        return Picker(title="CHOOSE A MODEL", items=items, on_pick="pickmodel",
                      sel=next((i for i, it in enumerate(items) if it[0] == cur), 0),
                      noun=f"models · sort: {so} · show: {fi}", mark=pick, note=note,
                      foot=PICKER_FOOT + " Press s / S for the next / previous sort and f / F for the next / previous "
                           "filter (use case, stock, dense / MoE, downloaded, fits).")

    def confirm(self, c: Confirm, cols: int) -> List[Row]:
        """A yes / no question."""
        w = min(cols - 2, 96)
        L: List[CardLine] = ["", *[x for line in c.lines for x in cwrap(line, w - 4)], "",
                             buttons("  ", [("Yes (y)", c.yes), ("Cancel (n)", "c2no")]),
                             f"{DIM}  Press y for yes, n or Esc to cancel.{R}"]
        return indent(draw_card("confirm2", c.title, "", L, w, 2))

    # ------------------------------------------------------------ panel 2: models + downloads
    def models(self, ui: UIState, cols: int, height: int, mdir: ModelsDir) -> List[Row]:
        """Every model, the selected one's details, the download in progress and the actions."""
        ms = self.visible(ui.msort, ui.mfilter)
        ui.mrow = max(0, min(ui.mrow, len(ms) - 1))
        w = cols - 2
        so, fi = arrange_label(ui.msort, ui.mfilter)
        af = self.svc.auto_fit(ui.pending or {})
        pick = af.name if af else None
        m = ms[ui.mrow] if ms else None

        def main(mw: int) -> List[CardLine]:
            L: List[CardLine] = [*arrange_chips(ui.msort, ui.mfilter, mw), f"{DIM}{len(ms)} of {len(self.svc.models.get())} models{R}",
                                 MODEL_HEADER]
            if not ms:
                L.append(f"{DIM}  no model matches \"{fi}\": pick another filter above{R}")
            vis = max(height - 14, 4)
            top = min(max(ui.mrow - vis // 2, 0), max(len(ms) - vis, 0))
            for i in range(top, min(top + vis, len(ms))):
                L.append(selectable(self.model_line(ms[i], pick), i == ui.mrow, mw, f"mrow:{i}"))
            if len(ms) > vis:
                L.append(f"{DIM}  {top + 1}-{min(top + vis, len(ms))} of {len(ms)} (↑↓){R}")
            L.append("")
            if ui.dl:
                L += download_status(ui.dl)
            L += button_rows("", acts, mw)
            if ui.text:
                L += ["", *cwrap(f"{B}{ui.text.prompt}{R} {CYN}{ui.text.value}▏{R}  "
                                 f"{DIM}(Enter looks it up, Esc cancels){R}", mw)]
            if ui.hf and ui.hf.status:
                L += ["", *cwrap(f"{YEL}{ui.hf.status}{R}", mw)]
            return L

        acts = [("Use it (Enter)", "museit")]
        if m and m["status"] != "downloaded" and jdict(m.get("hf")).get("repo"):
            acts.append(("Download (d)", "mdl"))
        if m and m["status"] == "downloaded":
            acts += [("Verify (v)", "mverify"), ("Auto-tune (u)", "mtune"), ("Delete (x)", "mdelete")]
        elif m and m["status"] == "partial":
            acts.append(("Delete (x)", "mdelete"))
        if m and m.get("custom"):
            acts.append(("Edit card (e)", "medit"))
        acts.append(("Add from Hugging Face (h)", "mhf"))
        about: List[CardLine] = []
        tip = "Press h to add any GGUF from Hugging Face."
        if m:
            hf = jdict(m.get("hf"))
            about += [f"{B}{m.get('label', m['name'])}{R}  {DIM}{m['source']}{R}",
                      f"{DIM}{m.get('description') or m.get('summary', '')}{R}"]
            if hf.get("repo"):
                about.append(lv("source", f"huggingface.co/{hf['repo']} · {hf.get('file')}"
                                + (f" @ {hf['revision'][:8]}" if hf.get("revision") else ""), 8))
            about.append(lv("file", home_short(m["path"], self.home), 8))
            if m.get("custom"):
                has = bool(jdict(jdict(m.get("local")).get("card")))
                about.append(lv("card", f"{GRN}your card{R}" if has else f"{YEL}none yet{R}", 8))
            tune = jdict(m.get("local")).get("tune")
            if tune:
                st = tune["settings"]
                about.append(lv("tuned", f"{GRN}{tune['date']}{R} on {tune.get('machine', '?')}: {st['spec']} "
                                         f"n={st['spec_n']}, {ctx_label(st['ctx'])} x {st['slots']} slot(s)", 8))
            tip = ("Press Enter to use it in the Server panel." if m["status"] == "downloaded"
                   else "Press d to download it (resumable, checked against its SHA-256)." if hf.get("repo")
                   else "Press Enter to use it in the Server panel.")
            if m.get("custom") and not jdict(jdict(m.get("local")).get("card")):
                tip = "Press e to say what this model is good for (role, tags, rank): its card."
        L = with_side(main, tip, [
            ("Selected model", about),
            ("The list", [f"speed: {GRN}measured here{R} (Auto-tune) or from another Mac (the catalogue); "
                          f"? = not measured · fits: the largest window per slot on this Mac"]),
            ("Models folder", [f"Any .gguf in {home_short(mdir.path, self.home)} shows up here · free disk "
                               f"{size(mdir.free)}"])], w - 4)
        ui.keys = [("↑↓", "select"), ("Enter", "use"), ("d", "download"), ("v", "verify"), ("u", "auto-tune"),
                   ("x", "delete"), ("e", "edit card"), ("h", "add from HF"), ("s f", "sort / filter"), ("[ ]", "panels")]
        ui.keys_more = ["S / F: the previous sort / filter; or click an option.", "c cancels a download.",
                        "e edits the card of a custom model (one you added): what it is good for, its rank, thinking."]
        return indent(draw_card("models", "MODELS", f"{DIM}catalogue + models folder + Hugging Face downloads{R}", L, w, 2))

    # ------------------------------------------------------------ panel 3: auto fit
    def autofit(self, ui: UIState, p: Pending, cols: int, height: int) -> List[Row]:
        """Auto fit: the goal and the models it picks from, this Mac's budget, the pick with its
        plan and reasons, Use this / Download, the other goal's pick, and the ranking. Scrolls
        (↑↓, PgUp PgDn, the wheel)."""
        svc = self.svc
        w = cols - 2
        goal, scope = as_goal(p.get("goal")), as_scope(p.get("scope"))
        af = svc.auto_fit(p)
        ui.keys = [("g", "the other goal"), ("f", "the other model set"), ("Enter", "use this"), ("d", "download it"),
                   ("↑↓ PgUp PgDn", "scroll"), ("[ ]", "panels")]
        ui.keys_more = ["The goal and the model set are saved at once: model auto starts the new pick.",
                        "Use this sets the Server panel's model, context, slots and KV cache; a there starts it."]
        how: List[CardLine] = ["Auto fit picks the best stock model that fits this Mac. Quality: parameters and density "
                               "first, then quantization (rank 1 = best). Speed comes first, so the goal picks the family. "
                               "Abliterated models are only picked by hand; a custom model takes part only when its card "
                               "opts in."]
        sections: List[Section] = [("How auto fit picks", how), ("Goal", [SET_HELP["goal"]]), ("From", [SET_HELP["scope"]])]

        def choices(mw: int) -> List[CardLine]:
            L: List[CardLine] = []
            for label, opts, cur in (("Goal", [(g, GOAL_TEXT[g], f"fgoal:{g}") for g in GOALS], goal),
                                     ("From", [(x, SCOPE_TEXT[x], f"fscope:{x}") for x in SCOPES], scope)):
                L += self._choice_line(label, opts, cur, mw)
            return L

        if af is None:
            L = with_side(lambda mw: [*choices(mw), "", *cwrap(f"{RED}auto fit unavailable: "
                                                              f"{svc.models.fit_error or 'no model list'}{R}", mw)],
                          "Press g or f to change what auto fit looks for.", sections, w - 4)
            return indent(draw_card("autofit", "AUTO FIT", f"{DIM}the best stock model for this Mac{R}", L, w, 2))
        b = af.budget
        mem = (f"GPU limit {gib(b.gpu_limit)}" + (f" · RAM {gib(b.ram)} less {gib(b.reserve)} kept for macOS and apps"
                                                   if b.ram > 0 else ""))
        vm = (f"{YEL}(more kept while VMware's network is up){R}" if b.vm_up
              else f"{DIM}(VMware's network is down; with it up, 10 GiB are kept){R}")
        sections.insert(1, ("This Mac", [f"{mem} → {B}{gib(b.allowed)} for a model{R} {vm}"]))
        sections.append(("The ranking", [f"max ctx = the largest window per slot that fits this Mac (1 slot, q4_0 KV) · "
                                         f"speed = Auto-tune's score, {GRN}measured here{R} or on another Mac (the "
                                         f"catalogue) · ./carl.sh fit prints the ranking too"]))
        other = as_goal("everyday" if goal == "hard-code" else "hard-code")
        oaf = svc.models.auto_fit(other, scope)
        if oaf:
            sections.append((f"The other goal: {GOAL_TEXT[other]}",
                             [(f"{oaf.pick.name}, {oaf.plan.label()}" if oaf.pick and oaf.plan else "nothing fits")]))
        tip = ("Press Enter to set the Server panel to this plan; a there starts it." if af.pick and af.pick.downloaded
               else "Press d to download the pick, then Enter to use it." if af.pick else "Press g or f to look wider.")

        def main(mw: int) -> List[CardLine]:
            L = [*choices(mw), "", heading("The pick", mw)]
            if af.pick and af.plan:
                where = f"{GRN}downloaded{R}" if af.pick.downloaded else f"{YEL}not downloaded{R}"
                L += cwrap(f"{B}{CYN}★ {af.pick.name}{R}  {af.plan.label()} · needs {gib(af.plan.need)} · {where}", mw, "    ")
                L += label_wrap("Why", af.because(), mw)
                if not af.pick.downloaded:
                    start = svc.resolved_model(dict(p, model="auto"))
                    L += label_wrap("Meanwhile", f"model auto starts {start} (the best downloaded model that fits) until "
                                                 f"{af.pick.name} is downloaded.", mw, YEL)
                for i, r in enumerate(af.rejected):
                    lines = wwrap(r.line(), mw - 14)
                    L.append(f"{B}{'Passed over' if i == 0 else '':<11}{R} {DIM}· {lines[0]}{R}")
                    L += [f"{' ' * 14}{DIM}{x}{R}" for x in lines[1:]]
                acts = [("Use this (Enter)", "fuse")]
                if not af.pick.downloaded:
                    acts.append(("Download it (d)", "fdl"))
                L += ["", *button_rows("", acts, mw)]
                if ui.dl:
                    L += download_status(ui.dl)
            else:
                L += cwrap(f"{RED}nothing fits: {af.because()}{R}", mw)
            return [*L, "", heading("Ranking", mw), *self.fit_ranking(af, mw)]

        L = with_side(main, tip, sections, w - 4, main_w=112)
        rows = indent(draw_card("autofit", "AUTO FIT", f"{DIM}{af.summary()}{R}", L, w, 2))
        ui.fit_scroll = max(0, min(ui.fit_scroll, len(rows) - height))
        return rows[ui.fit_scroll:ui.fit_scroll + height]

    @staticmethod
    def _choice_line(label: str, opts: Sequence[Tuple[str, str, str]], cur: str, w: int) -> List[CardLine]:
        """A label and clickable options (value, text, action), the current one highlighted; options
        that don't fit go on the next line."""
        out: List[CardLine] = []
        text, col = f"{B}{label:<11}{R}", 11
        spans: List[Tuple[int, int, str]] = []
        for value, shown, act in opts:
            chip = f" {shown} "
            if col > 11 and col + len(chip) > w:
                out.append(Ln(text, spans=spans))
                text, spans, col = " " * 11, [], 11
            text += (f"\x1b[7m{chip}{R}" if value == cur else f"{DIM}{chip}{R}") + " "
            spans.append((col, col + len(chip), act))
            col += len(chip) + 1
        return out + [Ln(fit(text, w), spans=spans)]

    def fit_ranking(self, af: AutoFit, w: int) -> List[CardLine]:
        """Every model by rank: arch, weights, the largest window (1 slot, q4_0), whether it is
        here, and what auto fit made of it."""
        passed = {r.name: r.reason for r in af.rejected}
        ms = sorted(self.svc.models.get(), key=lambda m: (m.get("rank") or 99, m["name"]))
        L: List[CardLine] = [f"{DIM}  {'model':<24} {'rank':>4} {'arch':<6}{'weights':>8} {'max ctx':>8} "
                             f"{'speed':>7}  {'here':<5} auto fit{R}"]
        for m in ms:
            name = m["name"]
            mx = self.svc.max_ctx(m)
            ctx = (f"{(GRN if mx >= 65536 else YEL if mx >= MIN_FIT else RED)}{ctx_label(mx) if mx else 'none':>8}{R}"
                   if mx is not None else f"{DIM}{'?':>8}{R}")
            if af.pick and name == af.pick.name:
                verdict = f"{CYN}★ the pick{R}"
            elif m.get("abliterated"):
                verdict = f"{DIM}abliterated: picked by hand only{R}"
            elif m.get("custom") and m.get("auto_fit") is not True:
                verdict = f"{DIM}custom: not in auto fit (its card can opt in){R}"
            elif not isinstance(m.get("rank"), int):
                verdict = f"{DIM}no rank{R}"
            elif name in passed:
                verdict = f"{YEL}{passed[name]}{R}"
            else:
                verdict = f"{DIM}ranked below the pick{R}"
            here = f"{GRN}yes{R}  " if m["status"] == "downloaded" else f"{DIM}no{R}   "
            rank = m.get("rank") if isinstance(m.get("rank"), int) else "–"
            arch = str(m.get("arch") or "?").replace("moe", "MoE")
            sp = speed_of(m)
            speed = f"{GRN if sp[1] else DIM}{sp[0]:>3.0f} t/s{R}" if sp else f"{DIM}{'?':>7}{R}"
            head = (f"{CYN if af.pick and name == af.pick.name else ''}{name:<24}{R} {rank!s:>4} {arch:<6}"
                    f"{size(int(m.get('bytes', 0))):>8} {ctx} {speed}  {here} ")
            L += cwrap(head + verdict, w, " " * 70)
        return L

    # ------------------------------------------------------------ panel 4: auto-tune
    def tune(self, ui: UIState, cols: int, server_up: bool) -> List[Row]:
        """The model to tune, the run in progress, and the last result."""
        ms = self.svc.models.downloaded()
        w = cols - 2
        if not ms:
            self._tune_keys(ui)
            return indent(draw_card("tune", "AUTO-TUNE", "", cwrap("No model is downloaded yet: the Models panel ([) "
                                                                   "downloads one.", w - 4), w, 2))
        names = [m["name"] for m in ms]
        if ui.tune_model not in names and ui.tune_model != TUNE_ALL:
            cur = self.svc.resolved_model(ui.pending) if ui.pending else names[0]
            ui.tune_model = cur if cur in names else names[0]
        if ui.tune_model == TUNE_ALL:
            return self._tune_all(ui, ms, cols, server_up)
        m = ms[names.index(ui.tune_model)]
        tn = ui.tune
        depth = as_depth(ui.tune_depth)
        t = jdict(m.get("local")).get("tune")
        self._tune_keys(ui)

        def main(mw: int) -> List[CardLine]:
            sel = f"{CYN}[<]{R} {B}{m['name']:^30}{R} {CYN}[>]{R}"
            L: List[CardLine] = [Ln(f"model     {sel}", spans=[(10, 13, "tprev"), (14, 44, "tpick"), (45, 48, "tnext")]),
                                 self._depth_line(depth), ""]
            if tn and (not tn.done or tn.model == m["name"]):
                L += tune_progress(tn, mw + 4, server_up)
            else:
                L.append(buttons("", [("Run auto-tune (Enter)", "trun")]))
            L.append("")
            if t:
                st = t["settings"]
                L += cwrap(f"{B}Last result{R} {DIM}{t['date']} · {t.get('machine', '?')} · {t.get('llama_cpp', '')}"
                           f"{' · ' + str(t['depth']) + ' mode' if t.get('depth') else ''}{R}", mw)
                L += cwrap(f"  {GRN}kv {st['kv']} · speculation {st['spec']} n={st['spec_n']} · context "
                           f"{ctx_label(st['ctx'])} per slot · {st['slots']} slot(s){R}", mw, "  ")
                res = jdict(jdict(t.get("results")).get("speculation"))
                best = f"{st['spec']}:{st['spec_n']}"
                L.append(f"  {DIM}{'speculation':<24}{'prose':>7}{'code':>7}{'re-emit':>9}{'score':>8}{R}")
                for mode, r in res.items():
                    hi = GRN + B if mode == best else ""
                    L.append(f"  {hi}{mode:<24}{r['prose']:>7}{r['code']:>7}{r['edit']:>9}{r['score']:>8}{R}")
                pr = jdict(t.get("results")).get("prompt_read") or []
                if pr:
                    L += cwrap("  prompt reading: " + " · ".join(f"{int(n) // 1024}K at {tps:.0f} tok/s" for n, tps in pr),
                               mw, "  ")
                par = jdict(t.get("results")).get("parallel") or []
                if par:
                    L += cwrap(f"  {parallel_text(par)}", mw, "  ")
                dd = jdict(t.get("results")).get("decode_at_depth") or []
                if dd:
                    L += cwrap("  decoding after a read of: " + " · ".join(f"{int(n) // 1024}K {tps:.1f} tok/s"
                                                                          for n, tps in dd), mw, "  ")
                z = t.get("ctx_zones")
                if z:
                    L.append(f"  context zones: {GRN}≤{ctx_label(z['good'])} fast{R} · {YEL}≤{ctx_label(z['slow'])} "
                             f"slow{R} · {RED}>{ctx_label(z['slow'])} very slow{R}")
                L += button_rows("  ", [("Use these values (clear my overrides)", "tclear")], mw)
            else:
                L += cwrap(f"{DIM}Not tuned on this Mac yet: the catalogue's values apply"
                           f"{' (measured on another Mac)' if not m.get('custom') else ' (from the GGUF header: a guess)'}.{R}",
                           mw)
            return L

        steps: List[CardLine] = [
            "Measures this model on this Mac and saves the best settings for it (~5-10 min; the model loads once per mode):",
            "1 memory: the largest window with 1 and 2 slots",
            "2 speculation: none, n-gram, MTP, MTP + n-gram (n = 1, 2) on prose, code and a code re-emit",
            "3 prompt reading at 8K/32K/64K → this Mac's context zones", "4 the result",
            "Every start of the model then uses it, unless you change a value in the Server panel (config.json wins)."]
        tip = ("Press c to cancel the run." if tn and not tn.done
               else "Press Enter to run it; ← → choose another model (or all of them).")
        L = with_side(main, tip, [("This model", [m["summary"]] if m.get("summary") else []),
                                  (f"Mode: {depth}", [DEPTH_TEXT[depth]]), ("What it measures", steps)], w - 4, main_w=80)
        return indent(draw_card("tune", "AUTO-TUNE", f"{DIM}per model, per Mac{R}", L, w, 2))

    @staticmethod
    def _tune_keys(ui: UIState) -> None:
        ui.keys = [("← →", "model"), ("space", "mode"), ("Enter", "run"), ("c", "cancel a run"), ("[ ]", "panels")]
        ui.keys_more = ["Click the model's name for a list of the downloaded models (and all of them at once).",
                        "Auto-tune needs the GPU to itself: it stops the running server and starts it again after."]

    @staticmethod
    def _depth_line(depth: str) -> Ln:
        text, spans, col = "mode      ", [], 10
        for d in DEPTHS:
            chip = f" {d} "
            text += (f"\x1b[7m{chip}{R}" if d == depth else f"{DIM}{chip}{R}") + " "
            spans.append((col, col + len(chip), f"tdepth:{d}"))
            col += len(chip) + 1
        return Ln(text, spans=spans)

    def _tune_all(self, ui: UIState, ms: Sequence[ModelInfo], cols: int, server_up: bool) -> List[Row]:
        """Auto-tune for every downloaded model: the list with each one's last result, the run."""
        w = cols - 2
        tn = ui.tune
        depth = as_depth(ui.tune_depth)
        self._tune_keys(ui)

        def main(mw: int) -> List[CardLine]:
            sel = f"{CYN}[<]{R} {B}{'all models (' + str(len(ms)) + ')':^30}{R} {CYN}[>]{R}"
            L: List[CardLine] = [Ln(f"model     {sel}", spans=[(10, 13, "tprev"), (14, 44, "tpick"), (45, 48, "tnext")]),
                                 self._depth_line(depth), ""]
            if tn and (not tn.done or tn.model == TUNE_ALL):
                L += tune_progress(tn, mw + 4, server_up)
            else:
                L.append(buttons("", [("Run auto-tune for all (Enter)", "trun")]))
            L += ["", f"{B}Last results{R}"]
            for m in ms:
                t = jdict(jdict(m.get("local")).get("tune"))
                st = jdict(t.get("settings"))
                res = (f"{GRN}{t.get('date')} · kv {st.get('kv')} · {st.get('spec')} n={st.get('spec_n')} · "
                       f"{ctx_label(int(st['ctx'])) if isinstance(st.get('ctx'), int) else '?'} × {st.get('slots')}{R}"
                       if st else f"{DIM}not tuned on this Mac yet{R}")
                L.append(f"  {m['name']:<28} {res}")
            return L

        L = with_side(main, "Press c to cancel the run." if tn and not tn.done else "Press Enter to tune every downloaded model.",
                      [("All models", ["Tunes every downloaded model, one after another (~5-10 min each, the chosen mode "
                                       "for all); a model that fails doesn't stop the rest. Each one's result is saved "
                                       "as when it is tuned alone."]),
                       (f"Mode: {depth}", [DEPTH_TEXT[depth]])], w - 4, main_w=96)
        return indent(draw_card("tune", "AUTO-TUNE", f"{DIM}all models, per Mac{R}", L, w, 2))

    # ------------------------------------------------------------ panel 5: router mode
    def router(self, ui: UIState, d: ServerData, saved: str, switches: Sequence[Tuple[str, str]],
               stale: Sequence[str], cols: int) -> List[Row]:
        """Model switching: dashboard only (single, the default) or router mode (OpenCode / Pi
        switch models), what each means, the router's models with Load / Unload, its recent
        switches, and whether the client configs list the installed models."""
        w = cols - 2
        running = "router" if d.router is not None else "single" if d.up else ""
        ui.keys = [("click", "a mode, Load / Unload"), ("[ ]", "panels")]
        ui.keys_more = ["A change of mode applies at the next start; with a server running it asks to restart now."]
        now = (f"{GRN}running as {running}{R}" if running == saved else
               f"{YEL}running as {running}: the change applies at the next start (it asks to restart now){R}"
               if running else f"{DIM}no server running{R}")

        def main(mw: int) -> List[CardLine]:
            L: List[CardLine] = self._choice_line("Mode", [("single", "Dashboard only (single model)", "rmode:single"),
                                                           ("router", "OpenCode / Pi switch models (router)", "rmode:router")],
                                                  saved, mw)
            L += [f"{' ' * 11}{x}" for x in cwrap(f"{DIM}saved: llama.mode = {saved} ·{R} {now}", mw - 11)]
            if d.router is not None:
                L += ["", heading("Models the router offers", mw)]
                for m in d.router.models:
                    L.append(self._router_row(m, mw))
                if not d.router.models:
                    L.append(f"{DIM}  none: the router has no models (./carl.sh fit){R}")
                if switches:
                    L += ["", heading("Recent switches", mw)]
                    L += [f"  {DIM}{t}{R}  {name}" for t, name in list(switches)[-5:]]
            L += ["", heading("OpenCode and Pi", mw)]
            if stale:
                L += [x for line in stale for x in cwrap(f"{YEL}⚠ {line}{R}", mw, "  ")]
            else:
                L += cwrap(f"{GRN}✓{R} {DIM}the configs on this Mac list the installed models (or are not installed: "
                           f"Connect tab){R}", mw)
            return L + button_rows("", [("Update the OpenCode / Pi configs", "insconfig")], mw)

        tip = ("Click Load to start a model now; OpenCode and Pi load the model they ask for themselves."
               if d.router is not None else "Click a mode: the dashboard picks the model, or OpenCode / Pi switch models.")
        L = with_side(main, tip, [
            ("Who switches the model", [
                f"{B}Dashboard only{R} (the default): one model runs, picked here or by auto fit; OpenCode / Pi use "
                f"whichever runs. {B}Router{R}: every downloaded model that fits is offered; the model you pick in "
                f"OpenCode (/models) or Pi (/model) is loaded, one at a time (a switch takes 30 s to 2 min while the old "
                f"model stops and the new one loads). For users who prefer to choose models on the fly; auto fit then "
                f"only picks the model loaded first."]),
            ("Warning", [f"{YEL}WARNING: every switch empties the prompt cache. The model that loads starts cold: OpenCode "
                         f"and Pi put a session back from the disk cache (about a second after the load, Caching panel); "
                         f"other clients re-read the whole conversation (a long one: minutes, see the context zones). "
                         f"Switch with this in consideration.{R}"]),
            ("Settings per model", [
                "Router mode gives each model the settings a single start of it would use (config.json > Auto-tune > "
                "catalogue) and leaves out a model that doesn't fit. A model a client asks for that isn't installed gets "
                "an error (HTTP 400, nothing loads); the OpenCode plugin says so. Custom models: set their card's "
                "thinking (Models panel) so OpenCode offers the right levels."]),
            ("Load and Unload", ["Load starts a model now (the loaded one stops first); Unload frees the memory."]
             if d.router is not None else [])], w - 4, main_w=96)
        return indent(draw_card("router", "ROUTER", f"{DIM}model switching: {saved}{R}", L, w, 2))

    # ------------------------------------------------------------ panel 6: the disk cache
    def caching(self, ui: UIState, conf: CacheConfig, files: Sequence[CacheFile], folder: str, cols: int) -> List[Row]:
        """The disk cache (diskcache.py): its limit, the prompt and conversation switches, what it
        holds, and Clear. Every change is saved at once; none needs a restart."""
        w = cols - 2
        use = used(files)
        n = len(files)

        def main(mw: int) -> List[CardLine]:
            gbs = sorted({*DISK_CHOICES, conf.disk_gb})
            L: List[CardLine] = self._choice_line("Disk limit", [(str(g), f"{g} GB", f"cache:disk:{g}") for g in gbs],
                                                  str(conf.disk_gb), mw)
            L += self._choice_line("Prompts", [("on", "pre-read each agent's", "cache:prefix:on"),
                                               ("off", "off", "cache:prefix:off")], "on" if conf.prefix else "off", mw)
            L += self._choice_line("Sessions", [("on", "save conversations", "cache:sessions:on"),
                                                ("off", "off", "cache:sessions:off")], "on" if conf.sessions else "off", mw)
            L += self._choice_line("Save", [(k, t, f"cache:save:{k}") for k, t in SAVE_TEXT], conf.save, mw)
            autos = sorted({*AUTO_CHOICES, conf.auto_s})
            L += self._choice_line("Auto after", [(str(a), dur(a), f"cache:auto:{a}") for a in autos], str(conf.auto_s), mw)
            L += self._choice_line("Shared", [("on", "store conversations against their prompt", "cache:share:on"),
                                              ("off", "off", "cache:share:off")], "on" if conf.share else "off", mw)
            L += self._choice_line("SWA models", [(k, t, f"cache:swa:{k}") for k, t in SWA_TEXT], conf.swa, mw)
            L += ["", heading("On disk", mw),
                  lv("used", f"{bar(use / conf.limit, 18)} {gb_text(use)} of {conf.disk_gb} GB · "
                             f"{n} file{'' if n == 1 else 's'}", 11),
                  lv("folder", f"{home_short(folder, self.home)}{DIM} (the server's --slot-save-path){R}", 11)]
            packed = [f for f in files if f.packed]
            if packed:
                L.append(lv("shared", f"{len(packed)} conversation(s) stored as patches against their prompt: "
                                      f"{gb_text(shared_saving(files))} saved", 11))
            nw = max(mw - 42, 12)
            for f in sorted(files, key=lambda f: -f.mtime)[:12]:
                what = "prompt      " if f.kind == PROMPT else "patch       " if f.packed else "conversation"
                L.append(f"  {DIM}{what}{R}  {fit(describe(f.name), nw):<{nw}} {size(f.bytes):>7}  "
                         f"{DIM}{dur(time.time() - f.mtime)} ago{R}")
            if n > 12:
                L.append(f"  {DIM}… and {n - 12} more{R}")
            if not files:
                L.append(f"  {DIM}nothing yet: OpenCode and Pi save here as they work{R}")
            return [*L, "", *button_rows("", [("Clear the disk cache (c)", "cache:clear")], mw)]

        L = with_side(main, "Click an option, or press its key (? lists them); every change is saved at once.", [
            ("Experimental", [f"{YEL}{B}EXPERIMENTAL{R}{YEL}: the disk cache is new. It works with the model that runs: "
                              f"saved states belong to one model file and llama.cpp build, and another model's sessions "
                              f"wait for that model (router mode loads it first). Turn sessions or prompts off here if "
                              f"something looks wrong.{R}"]),
            ("How it works", [f"OpenCode and Pi save prompt states through the server, so a restart, a model switch or "
                              f"many other sessions don't mean reading everything again: {B}prompts{R} = each agent's "
                              f"system prompt and tools (read once: a new session reads only its own messages) · "
                              f"{B}conversations{R} = each session, saved as the Save row says and put back before its "
                              f"next request when the server no longer holds it. Within the disk limit the oldest "
                              f"conversations go first, then the oldest prompts. The switches apply to the clients on "
                              f"this Mac (a VM's: NO_CACHE=1 ./install.sh there)."]),
            (f"Save: {conf.save}", [dict(SAVE_HELP)[conf.save].replace("AUTO", dur(conf.auto_s))]),
            (f"SWA models: {conf.swa}", [dict(SWA_HELP)[conf.swa]]),
            ("RAM cache", ["llama.cpp's own prompt cache in RAM, while the server runs: the Server panel's RAM cache row."])],
            w - 4, main_w=104)
        ui.keys = [("d", "disk limit"), ("p", "prompts"), ("s", "sessions"), ("o", "when to save"), ("t", "auto after"),
                   ("h", "shared"), ("w", "SWA models"), ("c", "clear"), ("[ ]", "panels")]
        ui.keys_more = ["Each key steps to the next choice of its row; a click picks one. Clear asks first."]
        return indent(draw_card("caching", "CACHING (EXPERIMENTAL)", f"{DIM}disk cache: {gb_text(use)} of {conf.disk_gb} GB{R}",
                                L, w, 2))

    @staticmethod
    def _router_row(m: RouterModel, w: int) -> Ln:
        """One router model: state, its setup (from the router's arguments), Load / Unload."""
        args = " ".join(m.args)
        per = flag(args, "--kv-unified-per-slot") or flag(args, "-c", "--ctx-size") or "?"
        setup = f"{flag(args, '--parallel', '-np') or '1'} × {ctx_label(int(per)) if per.isdigit() else per} " \
                f"{flag(args, '-ctk', '--cache-type-k') or ''}"
        col = {"loaded": GRN, "loading": YEL, "sleeping": CYN}.get(m.status, DIM)
        dot = {"loaded": "●", "loading": "◐", "sleeping": "◑"}.get(m.status, "○")
        state = m.status + (" (last load failed)" if m.failed else "")
        text = f"  {col}{dot}{R} {m.id:<26} {col}{state:<11}{R} {DIM}{setup:<16}{R} "
        label, act = ("[ Unload ]", f"runload:{m.id}") if m.active else ("[ Load ]", f"rload:{m.id}")
        c = vlen(text)
        return Ln(fit(text + f"{B}{CYN}{label}{R}", w), spans=[(c, c + len(label), act)])

_STEP = re.compile(r"STEP (\d+)/(\d+) (.*)")


def tune_progress(tn: TuneRun, w: int, server_up: bool) -> List[CardLine]:
    """The tune's step bar and its last output lines; Cancel while it runs, the result after."""
    steps = [x for x in tn.lines if x.startswith("STEP ")]
    out: List[CardLine] = []
    if steps:
        mm = _STEP.match(steps[-1])
        if mm:
            n, of = int(mm.group(1)), int(mm.group(2))
            out.append(f"{B}step {n} of {of}{R} {bar(n / of if of else 0, 24)} {mm.group(3)}")
    out += [f"{DIM}{fit(x, w - 6)}{R}" for x in tn.lines[-9:]]
    if not tn.done:
        out.append(buttons("", [("Cancel (c)", "tcancel")]))
    else:
        ok = tn.proc.returncode == 0
        out.append(f"{GRN if ok else RED}{'finished' if ok else 'failed'}{R}"
                   + (f"{DIM} · starting the server again…{R}" if tn.restart and not server_up else ""))
        out.append(buttons("", [("Run again (Enter)", "trun")]))
    return out


def download_status(dl: Download) -> List[CardLine]:
    """Progress (bytes so far, speed, ETA) while it runs; the outcome after."""
    if not dl.done:
        frac = dl.have / dl.total if dl.total else 0
        eta = dur((dl.total - dl.have) / dl.rate) if dl.rate > 0 and dl.total else "–"
        line = (f"{YEL}verifying the checksum of {dl.name}…{R}" if any("verifying" in x for x in dl.tail) else
                f"{B}downloading {dl.name}{R} {bar(frac, 24)} {frac:5.1%} {size(dl.have)} / {size(dl.total)} · "
                f"{size(dl.rate)}/s · ETA {eta}")
        return [line, buttons("", [("Cancel download (c)", "mcancel")])]
    ok = dl.proc.returncode == 0
    return [f"{GRN if ok else RED}{dl.name}: {'downloaded and verified' if ok else 'failed: ' + ' '.join(dl.tail)[-100:]}{R}"]

