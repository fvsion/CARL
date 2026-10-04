"""The Server panel's MODEL card: what the selected model is for and why, auto fit's reasons, the
values recommended for it next to yours, and how it is tuned (with Auto-tune's measurements)."""
from __future__ import annotations

from typing import List, Mapping, Optional

from carl_core.domain.autofit import GOAL_TEXT, SCOPE_TEXT, AutoFit, as_goal, as_scope
from carl_core.domain.cards import CHOICE_TEXT

from ..fmt import B, CYN, DIM, GRN, R, RED, YEL, CardLine, Row, ctx_label, cwrap, draw_card, fit, home_short, lv, wwrap
from ..model import ModelInfo, jdict
from ..settings import LLAMA_ADV, MODEL_ROW_KEYS, Pending, SettingsService, fmt_val
from .common import good_for_chip, label_wrap, parallel_text, passed_over, spec_table

WHY_KEY = {"specn": "spec", "presence": "temp", "top_k": "temp", "top_p": "temp", "min_p": "temp", "repeat": "temp"}


def speed_line(m: ModelInfo, tune: object) -> str:
    """This Mac's measured speed after Auto-tune, else the catalogue's figure and the Mac it came from."""
    t = jdict(tune)
    st = jdict(t.get("settings"))
    best = jdict(jdict(jdict(t.get("results")).get("speculation")).get(f"{st.get('spec')}:{st.get('spec_n')}"))
    if best:
        return (f"{GRN}{best.get('prose')} tok/s prose · {best.get('code')} code · {best.get('edit')} re-emit{R} "
                f"{DIM}(measured on this Mac, {t.get('date')}){R}")
    measured = m.get("measured") or []
    ref = jdict(measured[0]) if measured else {}
    if ref.get("decode"):
        return f"{ref['decode']} {DIM}(on an {ref.get('machine', '?')}; run Auto-tune for this Mac){R}"
    return f"{DIM}not measured: run Auto-tune{R}"


def why_text(tune: object, wk: str, why_all: Mapping[str, object]) -> str:
    """The reason for one tuned value; for speculation, this Mac's measurements when tuned."""
    t = jdict(tune)
    if t and wk == "spec":
        res = jdict(jdict(t.get("results")).get("speculation"))
        if res:
            return "Measured on this Mac (" + str(t.get("date")) + "): " + " · ".join(
                f"{mode.replace('draft-mtp', 'MTP').replace('ngram-mod', 'n-gram').replace(',', '+')} "
                f"{jdict(r).get('prose')}/{jdict(r).get('code')}/{jdict(r).get('edit')}"
                for mode, r in res.items()) + " tok/s (prose/code/re-emit)."
    return str(why_all.get(wk) or "")


def tune_table(tune: object) -> List[CardLine]:
    """Auto-tune's speculation table and prompt reading, the chosen mode highlighted."""
    t = jdict(tune)
    res = jdict(jdict(t.get("results")).get("speculation"))
    if not res:
        return []
    st = jdict(t.get("settings"))
    head: List[CardLine] = ["", f"{B}Auto-tune on this Mac{R} {DIM}{t.get('date')} · {t.get('machine', '?')}{R}"]
    return head + spec_table(res, f"{st.get('spec')}:{st.get('spec_n')}")


class ModelCard:
    """Draws the MODEL card of the Server panel."""

    def __init__(self, svc: SettingsService, home: str) -> None:
        self.svc = svc
        self.home = home

    def draw(self, p: Pending, key: str, w: int, lvl: int = 1) -> List[Row]:
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
            L += cwrap(f"{B}{'Parallel':<11}{R}{parallel_text(par)} {DIM}(measured on this Mac; set more slots in the "
                       f"Server panel){R}",
                       tw, " " * 11)
        if lvl == 2:
            if m.get("uncensored"):
                L += [""] + label_wrap("Uncensored", str(m["uncensored"]), tw, RED)
            alts = [jdict(a) for a in (m.get("pick_instead") or [])]
            if alts:
                L += ["", f"{B}Pick instead{R}"]
                L += [x for a in alts for x in cwrap(f"  {CYN}{a.get('model')}{R} {DIM}when{R} {a.get('when')}", tw, "    ")]
            if m.get("rank") and m.get("custom"):
                L += cwrap(f"{B}{'Quality':<11}{R}rank {m['rank']} {DIM}(from your card, not measured: 1 = best, the "
                           f"catalogue uses 1-10; auto fit {'can pick it' if m.get('auto_fit') else 'does not use it'}){R}",
                           tw, " " * 11)
            elif m.get("rank"):
                L += cwrap(f"{B}{'Quality':<11}{R}rank {m['rank']} {DIM}(1 = best: published benchmarks and CARL's code "
                           f"test first, then the quantization){R}", tw, " " * 11)
            desc = wwrap(m.get("description", ""), tw)
            if desc:
                L += ["", *[f"{DIM}{x}{R}" for x in desc]]
        elif not good and m.get("summary"):                 # custom models: no card text, the summary
            L += [f"{CYN}{x}{R}" for x in wwrap(m["summary"], tw)]
        if m.get("custom") and not jdict(m.get("local")).get("card"):
            L += cwrap(f"{DIM}This model has no card. To write one, select the model in the Models panel (]) and "
                       f"press e. You can also run ./carl.sh card {name}.{R}", tw)
        L += ["", *cwrap(f"{B}Recommended for this model{R} {DIM}(Auto-tune > catalogue; yellow = your value is "
                         f"different){R}", tw)]
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
                   f"slow{R} {DIM}(cold read of a full window; "
                   f"{'measured here' if tune and tune.get('ctx_zones') else 'catalogue'}){R}", tw, " " * 15)
        labels = {r.key: r.label for r in svc.schema.llama + LLAMA_ADV}
        why_all = jdict(m.get("why"))
        sel_wk = WHY_KEY.get(key, key)
        order = [sel_wk] + [k for k in ("spec", "ctx", "kv", "slots", "temp") if k != sel_wk] if lvl == 2 else [sel_wk]
        for wk in order:
            why = why_text(tune, wk, why_all)
            if why:
                L += ["", f"{B}Why{R} {DIM}({labels.get(wk if wk != sel_wk else key, wk)}){R}"]
                L += wwrap(why, tw)[: (12 if lvl == 2 else 6)]
        if lvl == 1:
            L += ["", *cwrap(f"{DIM}Click the MODEL title to see the full card: alternatives, what uncensored means, "
                             f"all reasons and the measurements.{R}", tw)]
        if lvl == 2:
            L += tune_table(tune)
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
        head = (f"picked {af.pick.name}, {af.plan.label()}: {af.because()}." if af.pick and af.plan
                else f"nothing fits: {af.because()}.")
        L = label_wrap("Auto fit", head, tw, CYN if af.pick else RED)
        L += [f"{pad}{x}" for x in cwrap(f"{DIM}goal: {GOAL_TEXT[as_goal(p.get('goal'))]} · from: "
                                          f"{SCOPE_TEXT[as_scope(p.get('scope'))]} (to change them, press A for the Auto "
                                          f"fit panel) · stock models only{R}", tw - 11)]
        if af.pick and not af.pick.downloaded:
            meanwhile = (f"Until then, model auto starts {start} (the best downloaded model that fits)"
                         if p.get("model") == "auto" else "A start is possible only after the download")
            L += [f"{pad}{YEL}{x}{R}" for x in wwrap(f"{af.pick.name} is not downloaded. {meanwhile}. To download it, "
                                                      f"use the Auto fit panel (A), or the Models panel (]) and d.",
                                                      tw - 11)]
        shown = af.rejected if lvl == 2 else af.rejected[:4]
        L += passed_over(shown, tw)
        if len(shown) < len(af.rejected):
            L.append(f"{pad}{DIM}+{len(af.rejected) - len(shown)} more: click the MODEL title for the full card{R}")
        return L + [""]
