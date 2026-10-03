"""The Settings tab: the Server panel (settings + what the model is and why it is tuned so),
the Models panel (catalogue, models folder, downloads, Hugging Face), the Auto-tune panel,
the model picker and confirmations. Draws from the UI state; reads models and config.json
through SettingsService (the ModelStore port)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Mapping, Optional, Tuple

from .fmt import (B, CYN, DIM, GRN, R, RED, YEL, CardLine, Ln, Row, bar, buttons, ctx_label, draw_card, dur, fit,
                  home_short, indent, lv, side_by_side, size, vlen, wwrap)
from .model import ModelInfo, ServerData, jdict
from .settings import (ADV_WARN, LLAMA_ADV, MODEL_ROW_KEYS, NUMERIC, SET_HELP, UNMARKED, Pending,
                       SettingsService, fmt_val, shown_value)
from .arrange import FILTERS, SORTS, arrange, label as arrange_label
from .state import SUBPANELS, Confirm, Download, PickItem, Picker, TuneRun, UIState

LIST_W = 40                                            # the Server panel's model list
MODEL_HEADER = f"{DIM}{'':2}{'model':<26} {'size':>8}  {'status':<10} {'fits':>5} {'':6} role and good for{R}"
PICKER_FOOT = ("↑↓ select · fits = largest window per slot that fits this Mac (q4_0, 1 slot) · "
               "download / add any GGUF from Hugging Face: ] Models panel (h)")
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
    text += f"{DIM}  [ ] switch panels{R}"
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
        w = cols - 1 - (LIST_W + 1 if side else 0)
        vw = 28 if w >= 105 else 18                        # value column: room for "draft-mtp,ngram-mod"
        L: List[CardLine] = []
        if svc.models.error:                                # a broken catalogue / models.json: say so here
            L += [f"{RED}{x}{R}" for x in wwrap(f"model list unavailable: {svc.models.error}", w - 6)[:3]]
            L += [f"{DIM}fix the file (./carl.sh models shows the same error); the list is read again every 10 s{R}", ""]
        L.append(f"{DIM}{'':2}{'setting':<14}{'new':<{vw + 8}}{'running now':<18}{R}")
        for i, (key, label, _, _, _) in enumerate(rws):
            L.append(self._row(ui, p, run, i, key, label, vw))
        key = rws[ui.set_row].key
        adv_on = p.get("adv") == "shown"
        hint = f"  {CYN}(type a value, Enter){R}" if key in NUMERIC else f"  {CYN}(Enter: choose){R}" if key == "model" else ""
        help_lines = [f"{DIM}{x}{R}" for x in wwrap(SET_HELP[key], w - 6)[:2]]
        help_lines[-1] += hint
        L += ["", *help_lines]
        if adv_on:
            L += [f"{YEL}{ADV_WARN[0]}{R}", f"{YEL}{ADV_WARN[1]}{R}"]
        ok, fl = svc.fit_cached(p)
        L.append(lv("fit", fl, 6))
        L.append(lv("file", home_short(self.config_file, self.home) + f"{DIM} (./carl.sh config show){R}", 6))
        L.append(f"{DIM}colours: {GRN}tuned / fast{R}{DIM} · {YEL}changed / slower{R}{DIM} · {RED}very slow / no MTP head{R}"
                 f"{DIM} · more models: ] Models panel → Add from Hugging Face (h){R}")
        if ui.restart:
            L.append(f"{YEL}{ui.restart}{R}")
        else:
            L.append(buttons("", [("Apply and restart (a)" if run else "Start server (a)", "setapply" if ok else "setnofit"),
                                  ("Revert (r)", "setrevert"), ("Tuned values (x)", "setdefaults")]))
        L.append(f"{DIM}↑↓ select · ←→ change · * differs from the running server · wheel / PgUp PgDn scroll · "
                 f"context or slots: run install.sh again{R}")
        srv = "llama.cpp" if run else "no server"
        card = draw_card("settings", "SERVER SETTINGS", f"{DIM}running: {srv} · port {port}{R}", L, w, 2)
        if side:
            rows = side_by_side(card, self.model_list(ui, p, LIST_W, len(card)), w, pad_left=False)
        else:
            rows = indent(card) + indent(self.model_list(ui, p, cols - 1, 14))
        ui.levels.setdefault("modelinfo", 1)
        rows += indent(self.model_info(p, key, cols - 1, ui.levels["modelinfo"]))
        if ui.confirm:
            rows = indent(self._confirm_restart(bool(run), port, min(w, 80))) + rows
        ui.set_scroll = max(0, min(ui.set_scroll, len(rows) - height))
        return rows[ui.set_scroll:ui.set_scroll + height]

    def model_list(self, ui: UIState, p: Pending, w: int, height: int) -> List[Row]:
        """A compact model selector beside the settings: click a model (or m, then ↑↓ Enter) to pick it;
        s / f sort and filter. Only names and status: the card below has the rest."""
        items = ["auto"] + [m["name"] for m in self.visible(ui.msort, ui.mfilter)]
        cur = str(p.get("model", "auto"))
        if not ui.slist:                                    # the cursor follows the chosen model
            ui.srow = items.index(cur) if cur in items else 0
        ui.srow = max(0, min(ui.srow, len(items) - 1))
        so, fi = arrange_label(ui.msort, ui.mfilter)
        st = {m["name"]: m["status"] for m in self.svc.models.get()}
        tw = w - 4
        L: List[CardLine] = arrange_lines(ui.msort, ui.mfilter, tw)
        vis = max(height - 5, 3)
        top = min(max(ui.srow - vis // 2, 0), max(len(items) - vis, 0))
        for i in range(top, min(top + vis, len(items))):
            name = items[i]
            dot = (f"{GRN}●{R}" if st.get(name) == "downloaded" else f"{YEL}◐{R}" if st.get(name) == "partial"
                   else f"{DIM}○{R}" if name != "auto" else f"{CYN}★{R}")
            chosen = name == cur
            text = f"{dot} {(B + CYN) if chosen else ''}{name}{R}" + (f" {DIM}(this Mac's default){R}" if name == "auto" else "")
            if ui.slist and i == ui.srow:
                text = f"\x1b[7m{fit(text, tw - 2)}{R}"
            L.append(Ln(("› " if chosen else "  ") + text, act=f"smodel:{name}"))
        more = len(items) - vis
        L.append(f"{DIM}{top + 1}-{min(top + vis, len(items))} of {len(items)} · " if more > 0 else DIM)
        L[-1] = str(L[-1]) + ("↑↓ Enter · m or Esc: back" if ui.slist else "click, or m for keys") + R
        summary = f"{DIM}● here ○ not downloaded{R}"
        return draw_card("modellist", "MODEL", summary, L, w, 2)

    def _row(self, ui: UIState, p: Pending, run: Pending, i: int, key: str, label: str, vw: int) -> Ln:
        """One setting: [<] new value [>]  * running value. The value is coloured (value_color)."""
        sel = i == ui.set_row
        val = shown_value(key, p[key])
        if key == "model" and p[key] == "auto":
            val = f"auto: {self.svc.resolved_model(p)}"
        if sel and ui.edit is not None:
            val = ui.edit + "▏"
        rv: object = run.get(key, "N/A")
        rv = "" if key == "adv" else rv
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
                f"{RED}abliterated{R}" if m.get("abliterated") else None, "custom" if m.get("custom") else None,
                f"{GRN}tuned here {tune['date']}{R}" if tune else f"{DIM}catalogue tune{R}" if not m.get("custom") else f"{YEL}not tuned{R}"]
        role = str(m.get("role") or m.get("summary") or "")
        title_info = (f"{B}{m.get('label', name)}{R}  {CYN}{role}{R}  " + f"{DIM} · {R}".join(t for t in tags if t))
        tw = w - 4
        L: List[CardLine] = []
        good = [str(t) for t in (m.get("good_for") or [])]
        if good:
            L.append(f"{B}{'Good for':<11}{R}" + "  ".join(good_for_chip(t) for t in good))
        for field, label, colour in (("why_use", "Why use it", ""), ("trade_offs", "Trade-offs", YEL)):
            text = str(m.get(field) or "")
            if text:
                L += label_wrap(label, text, tw, colour)
        if m.get("hardware"):
            L.append(f"{B}{'Hardware':<11}{R}{m['hardware']}")
        L.append(f"{B}{'Speed':<11}{R}{speed_line(m, tune)}")
        if lvl == 2:
            if m.get("uncensored"):
                L += [""] + label_wrap("Uncensored", str(m["uncensored"]), tw, RED)
            alts = [jdict(a) for a in (m.get("pick_instead") or [])]
            if alts:
                L += ["", f"{B}Pick instead{R}"]
                L += [f"  {CYN}{a.get('model')}{R} {DIM}when{R} {a.get('when')}" for a in alts]
            if m.get("rank"):
                L.append(f"{B}{'Quality':<11}{R}" + (f"rank {m['rank']} {DIM}(1 = best: parameters and density first, then quantization; "
                                       f"speed is the reverse){R}"))
            desc = wwrap(m.get("description", ""), tw)
            if desc:
                L += ["", *[f"{DIM}{x}{R}" for x in desc]]
        elif not good and m.get("summary"):                 # custom models: no card text, the summary
            L += [f"{CYN}{x}{R}" for x in wwrap(m["summary"], tw)]
        L += ["", f"{B}Recommended for this model{R} {DIM}(Auto-tune > catalogue; yellow = yours differs){R}"]
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
            L += ["  " + c for c in cells]
        zg, zs, _ = svc.store.ctx_zones(m)
        L.append(f"  {'zones':<13}{GRN}≤{ctx_label(zg)} fast{R} · {YEL}≤{ctx_label(zs)} slow{R} · {RED}>{ctx_label(zs)} very slow{R} "
                 f"{DIM}(cold re-read of a full window; {'measured here' if tune and tune.get('ctx_zones') else 'catalogue'}){R}")
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
            L += ["", f"{DIM}click the MODEL title for the full card: alternatives, what uncensored means, every reason, measurements{R}"]
        if lvl == 2:
            L += self._tune_table(tune)
            hf = jdict(m.get("hf"))
            L.append("")
            if hf.get("repo"):
                L.append(lv("source", f"huggingface.co/{hf['repo']} · {hf.get('file')}"
                            + (f" @ {str(hf.get('revision'))[:8]}" if hf.get("revision") else ""), 8))
            L.append(lv("file", home_short(str(m.get("path", "")), self.home), 8))
        return draw_card("modelinfo", "MODEL", title_info, L, w, lvl)

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
    def model_line(self, m: ModelInfo) -> str:
        """One model in a list: name, size, status, the largest window that fits, tuned, summary."""
        st = {"downloaded": f"{GRN}downloaded{R}", "partial": f"{YEL}partial{R}", "missing": f"{DIM}not here{R}"}[m["status"]]
        tuned = f"{GRN}✓tuned{R}" if jdict(m.get("local")).get("tune") else "      "
        mx = self.svc.max_ctx(m)
        fitc = (f"{(GRN if mx >= 65536 else YEL if mx >= 32768 else RED)}{ctx_label(mx) if mx else 'no fit':>5}{R}"
                if mx is not None else f"{DIM}{'?':>5}{R}")
        head = f"{m['name']:<26} {size(m['bytes']):>8}  {st:<10}{' ' * max(0, 10 - vlen(st))} {fitc} {tuned} "
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
            L.append(selectable(item if isinstance(item, str) else self.model_line(item), i == pk.sel, w, f"pick:{i}"))
        sel_item = pk.items[pk.sel][1]
        if not isinstance(sel_item, str):
            L += ["", *[f"{DIM}{x}{R}" for x in wwrap(sel_item.get("why_use") or sel_item.get("description")
                                                        or sel_item.get("summary", ""), w - 6)[:3]]]
            if sel_item.get("trade_offs"):
                L += [f"{YEL}{x}{R}" for x in wwrap(str(sel_item["trade_offs"]), w - 6)[:2]]
        L += ["", buttons("", [("Choose (Enter)", "pickok"), ("Cancel (Esc)", "pickno")]), f"{DIM}{pk.foot or PICKER_FOOT}{R}"]
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
                      noun="options", header=f"{DIM}  ↑↓ Enter{R}", foot="↑↓ select · Enter choose · Esc cancel", reopen=reopen)

    def model_picker(self, cur: str, sort: int = 0, filt: int = 0) -> Picker:
        """A drop-down of the models ("auto" on top) in the chosen order and filter; s / f change them."""
        ms = self.visible(sort, filt)
        items: List[PickItem] = [("auto", f"{'auto':<26} {DIM}this Mac's default: {self.svc.resolved_model({'model': 'auto'})}{R}")]
        items += [(m["name"], m) for m in ms]
        so, fi = arrange_label(sort, filt)
        return Picker(title="CHOOSE A MODEL", items=items, on_pick="pickmodel",
                      sel=next((i for i, it in enumerate(items) if it[0] == cur), 0),
                      noun=f"models · sort: {so} · show: {fi}",
                      foot=PICKER_FOOT + " · s / S sort, f / F filter: next / previous (use case, stock, dense / MoE, downloaded, fits)")

    def confirm(self, c: Confirm, cols: int) -> List[Row]:
        """A yes / no question."""
        w = min(cols - 2, 96)
        L: List[CardLine] = ["", *c.lines, "", buttons("  ", [("Yes (y)", c.yes), ("Cancel (n)", "c2no")])]
        return indent(draw_card("confirm2", c.title, "", L, w, 2))

    # ------------------------------------------------------------ panel 2: models + downloads
    def models(self, ui: UIState, cols: int, height: int, mdir: ModelsDir) -> List[Row]:
        """Every model, the selected one's details, the download in progress and the actions."""
        ms = self.visible(ui.msort, ui.mfilter)
        ui.mrow = max(0, min(ui.mrow, len(ms) - 1))
        w = cols - 2
        so, fi = arrange_label(ui.msort, ui.mfilter)
        L: List[CardLine] = [*arrange_chips(ui.msort, ui.mfilter, w - 4),
                             f"{DIM}{len(ms)} of {len(self.svc.models.get())} models · s / S and f / F step forward / back, or click{R}",
                             MODEL_HEADER]
        if not ms:
            L.append(f"{DIM}  no model matches \"{fi}\": pick another filter above{R}")
        vis = max(height - 22, 4)
        top = min(max(ui.mrow - vis // 2, 0), max(len(ms) - vis, 0))
        for i in range(top, min(top + vis, len(ms))):
            L.append(selectable(self.model_line(ms[i]), i == ui.mrow, w, f"mrow:{i}"))
        if len(ms) > vis:
            L.append(f"{DIM}  {top + 1}-{min(top + vis, len(ms))} of {len(ms)} (↑↓){R}")
        m = ms[ui.mrow] if ms else None
        if m:
            hf = jdict(m.get("hf"))
            L += ["", f"{B}{m.get('label', m['name'])}{R}  {DIM}{m['source']}{R}"]
            L += [f"{DIM}{x}{R}" for x in wwrap(m.get("description") or m.get("summary", ""), w - 6)[:4]]
            if hf.get("repo"):
                L.append(lv("source", f"huggingface.co/{hf['repo']} · {hf.get('file')}"
                            + (f" @ {hf['revision'][:8]}" if hf.get("revision") else ""), 8))
            L.append(lv("file", home_short(m["path"], self.home), 8))
            tune = jdict(m.get("local")).get("tune")
            if tune:
                s = tune["settings"]
                L.append(lv("tuned", f"{GRN}{tune['date']}{R} on {tune.get('machine', '?')}: {s['spec']} n={s['spec_n']}, "
                                     f"{ctx_label(s['ctx'])} x {s['slots']} slot(s)", 8))
        L.append("")
        if ui.dl:
            L += download_status(ui.dl)
        acts = [("Use (Enter)", "museit")]
        if m and m["status"] != "downloaded" and jdict(m.get("hf")).get("repo"):
            acts.append(("Download (d)", "mdl"))
        if m and m["status"] == "downloaded":
            acts += [("Verify (v)", "mverify"), ("Auto-tune (u)", "mtune"), ("Delete (x)", "mdelete")]
        elif m and m["status"] == "partial":
            acts.append(("Delete (x)", "mdelete"))
        acts.append(("Add from Hugging Face (h)", "mhf"))
        L.append(buttons("", acts))
        L.append(f"{DIM}↑↓ select · any .gguf in {home_short(mdir.path, self.home)} shows up here · free disk {size(mdir.free)}{R}")
        if ui.text:
            L += ["", f"{B}{ui.text.prompt}{R} {CYN}{ui.text.value}▏{R}  {DIM}(Enter ok · Esc cancel){R}"]
        if ui.hf and ui.hf.status:
            L += ["", f"{YEL}{ui.hf.status}{R}"]
        return indent(draw_card("models", "MODELS", f"{DIM}catalogue + models folder + Hugging Face downloads{R}", L, w, 2))

    # ------------------------------------------------------------ panel 3: auto-tune
    def tune(self, ui: UIState, cols: int, server_up: bool) -> List[Row]:
        """The model to tune, the run in progress, and the last result."""
        ms = self.svc.models.downloaded()
        w = cols - 2
        if not ms:
            return indent(draw_card("tune", "AUTO-TUNE", "", ["No model is downloaded yet: Models panel (]) → Download."], w, 2))
        names = [m["name"] for m in ms]
        if ui.tune_model not in names:
            cur = self.svc.resolved_model(ui.pending) if ui.pending else names[0]
            ui.tune_model = cur if cur in names else names[0]
        m = ms[names.index(ui.tune_model)]
        tn = ui.tune
        L: List[CardLine] = [
            f"{DIM}Measures this model on this Mac and saves the best settings for it (~5-10 min; the model loads once per mode):{R}",
            f"{DIM}  1 memory: the largest window with 1 and 2 slots · 2 speculation: none, n-gram, MTP, MTP + n-gram (n = 1, 2) on{R}",
            f"{DIM}    prose, code and a code re-emit · 3 prompt reading at 8K/32K/64K → this Mac's context zones · 4 the result{R}",
            f"{DIM}  Every start of the model then uses it, unless you change a value in the Server panel (config.json wins).{R}", ""]
        sel = f"{CYN}[<]{R} {B}{m['name']:^30}{R} {CYN}[>]{R}"
        L.append(Ln(f"model     {sel}   {DIM}{m.get('summary', '')}{R}", spans=[(10, 13, "tprev"), (14, 44, "tpick"), (45, 48, "tnext")]))
        L.append(Ln(f"mode      {CYN}[{'x' if ui.tune_quick else ' '}]{R} quick {DIM}(space; n=1 modes only, no 64K read: ~4 min){R}",
                    spans=[(10, 13, "tquick")]))
        L.append("")
        if tn and (not tn.done or tn.model == m["name"]):
            L += tune_progress(tn, w, server_up)
        else:
            L.append(buttons("", [("Run auto-tune (Enter)", "trun")]))
        t = jdict(m.get("local")).get("tune")
        L.append("")
        if t:
            s = t["settings"]
            L.append(f"{B}Last result{R} {DIM}{t['date']} · {t.get('machine', '?')} · {t.get('llama_cpp', '')}{R}")
            L.append(f"  {GRN}kv {s['kv']} · speculation {s['spec']} n={s['spec_n']} · context {ctx_label(s['ctx'])} per slot · "
                     f"{s['slots']} slot(s){R}")
            res = jdict(jdict(t.get("results")).get("speculation"))
            best = f"{s['spec']}:{s['spec_n']}"
            L.append(f"  {DIM}{'speculation':<24}{'prose':>7}{'code':>7}{'re-emit':>9}{'score':>8}{R}")
            for mode, r in res.items():
                hi = GRN + B if mode == best else ""
                L.append(f"  {hi}{mode:<24}{r['prose']:>7}{r['code']:>7}{r['edit']:>9}{r['score']:>8}{R}")
            pr = jdict(t.get("results")).get("prompt_read") or []
            if pr:
                L.append("  prompt reading: " + " · ".join(f"{int(n) // 1024}K at {tps:.0f} tok/s" for n, tps in pr))
            z = t.get("ctx_zones")
            if z:
                L.append(f"  context zones: {GRN}≤{ctx_label(z['good'])} fast{R} · {YEL}≤{ctx_label(z['slow'])} slow{R} · "
                         f"{RED}>{ctx_label(z['slow'])} very slow{R}")
            L.append(buttons("  ", [("Use these values (clear my overrides for this model)", "tclear")]))
        else:
            L.append(f"{DIM}Not tuned on this Mac yet: the catalogue's values apply"
                     f"{' (measured on another Mac)' if not m.get('custom') else ' (from the GGUF header: a guess)'}.{R}")
        return indent(draw_card("tune", "AUTO-TUNE", f"{DIM}per model, per Mac{R}", L, w, 2))


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

