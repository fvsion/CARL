"""The Auto fit panel: the goal and the models it picks from, this Mac's budget, the pick with its
plan and reasons, Use this / Download, the other goal's pick, and the ranking."""
from __future__ import annotations

from typing import List

from carl_core.domain.autofit import GOAL_TEXT, GOALS, SCOPE_TEXT, SCOPES, AutoFit, as_goal, as_scope, gib

from ..fmt import (B, CYN, DIM, GRN, R, RED, YEL, CardLine, Row, Section, button_rows, cwrap, draw_card, heading,
                   indent, size, with_side)
from ..settings import SET_HELP, Pending, SettingsService
from ..state import UIState
from .common import choice_line, download_status, fit_cell, label_wrap, passed_over, speed_cell


class AutoFitPanel:
    """Draws the Auto fit panel."""

    def __init__(self, svc: SettingsService) -> None:
        self.svc = svc

    def draw(self, ui: UIState, p: Pending, cols: int, height: int) -> List[Row]:
        """Auto fit: the goal and the models it picks from, this Mac's budget, the pick with its
        plan and reasons, Use this / Download, the other goal's pick, and the ranking. Scrolls
        (↑↓, PgUp PgDn, the wheel)."""
        svc = self.svc
        w = cols - 2
        goal, scope = as_goal(p.get("goal")), as_scope(p.get("scope"))
        af = svc.auto_fit(p)
        ui.keys = [("g", "the other goal"), ("f", "the other model set"), ("Enter", "use this"), ("d", "download it"),
                   ("↑↓ PgUp PgDn", "scroll"), ("[ ]", "panels")]
        ui.keys_more = ["CARL saves the goal and the model set immediately. Then model auto starts the new pick.",
                        "Use this sets the model, context, slots and KV cache in the Server panel. Press a there to "
                        "start it."]
        how: List[CardLine] = ["Auto fit picks the best stock model that fits this Mac. For quality, parameters and "
                               "density count first, then quantization (rank 1 = best). Speed is more important, so the "
                               "goal selects the model family first. Auto fit never picks an abliterated model: you "
                               "must select it yourself. Auto fit uses a custom model only when its card lets it."]
        sections: List[Section] = [("How auto fit picks", how), ("Goal", [SET_HELP["goal"]]), ("From", [SET_HELP["scope"]])]

        def choices(mw: int) -> List[CardLine]:
            L: List[CardLine] = []
            for label, opts, cur in (("Goal", [(g, GOAL_TEXT[g], f"fgoal:{g}") for g in GOALS], goal),
                                     ("From", [(x, SCOPE_TEXT[x], f"fscope:{x}") for x in SCOPES], scope)):
                L += choice_line(label, opts, cur, mw)
            return L

        if af is None:
            L = with_side(lambda mw: [*choices(mw), "", *cwrap(f"{RED}auto fit unavailable: "
                                                              f"{svc.models.fit_error or 'no model list'}{R}", mw)],
                          "Press g or f to change the goal or the model set.", sections, w - 4)
            return indent(draw_card("autofit", "AUTO FIT", f"{DIM}the best stock model for this Mac{R}", L, w, 2))
        b = af.budget
        mem = (f"GPU limit {gib(b.gpu_limit)}" + (f" · RAM {gib(b.ram)} minus {gib(b.reserve)} for macOS and apps"
                                                   if b.ram > 0 else ""))
        vm = (f"{YEL}(CARL keeps more while the VMware network is up){R}" if b.vm_up
              else f"{DIM}(the VMware network is down. When it is up, CARL keeps 10 GiB){R}")
        sections.insert(1, ("This Mac", [f"{mem} → {B}{gib(b.allowed)} for a model{R} {vm}"]))
        sections.append(("The ranking", [f"max ctx = the largest window per slot that fits this Mac (1 slot, q4_0 KV) · "
                                         f"speed = Auto-tune's score, {GRN}measured here{R} or on another Mac (the "
                                         f"catalogue) · ./carl.sh fit also shows the ranking"]))
        other = as_goal("everyday" if goal == "hard-code" else "hard-code")
        oaf = svc.models.auto_fit(other, scope)
        if oaf:
            sections.append((f"The other goal: {GOAL_TEXT[other]}",
                             [(f"{oaf.pick.name}, {oaf.plan.label()}" if oaf.pick and oaf.plan else "nothing fits")]))
        tip = ("Press Enter to set this plan in the Server panel. Then press a there to start it."
               if af.pick and af.pick.downloaded
               else "Press d to download the pick. Then press Enter to use it." if af.pick
               else "Press g or f to change the goal or the model set.")

        def main(mw: int) -> List[CardLine]:
            L = [*choices(mw), "", heading("The pick", mw)]
            if af.pick and af.plan:
                where = f"{GRN}downloaded{R}" if af.pick.downloaded else f"{YEL}not downloaded{R}"
                L += cwrap(f"{B}{CYN}★ {af.pick.name}{R}  {af.plan.label()} · needs {gib(af.plan.need)} · {where}", mw, "    ")
                L += label_wrap("Why", af.because() + ".", mw)
                if not af.pick.downloaded:
                    start = svc.resolved_model(dict(p, model="auto"))
                    L += label_wrap("Meanwhile", f"until {af.pick.name} is downloaded, model auto starts {start} (the "
                                                 f"best downloaded model that fits).", mw, YEL)
                L += passed_over(af.rejected, mw)
                acts = [("Use this (Enter)", "fuse")]
                if not af.pick.downloaded:
                    acts.append(("Download it (d)", "fdl"))
                L += ["", *button_rows("", acts, mw)]
                if ui.dl:
                    L += download_status(ui.dl)
            else:
                L += cwrap(f"{RED}nothing fits: {af.because()}.{R}", mw)
            return [*L, "", heading("Ranking", mw), *self.ranking(af, mw)]

        L = with_side(main, tip, sections, w - 4, main_w=112)
        rows = indent(draw_card("autofit", "AUTO FIT", f"{DIM}{af.summary()}{R}", L, w, 2))
        ui.fit_scroll = max(0, min(ui.fit_scroll, len(rows) - height))
        return rows[ui.fit_scroll:ui.fit_scroll + height]

    def ranking(self, af: AutoFit, w: int) -> List[CardLine]:
        """Every model by rank: arch, weights, the largest window (1 slot, q4_0), whether it is
        here, and what auto fit made of it."""
        passed = {r.name: r.reason for r in af.rejected}
        ms = sorted(self.svc.models.get(), key=lambda m: (m.get("rank") or 99, m["name"]))
        L: List[CardLine] = [f"{DIM}  {'model':<24} {'rank':>4} {'arch':<6}{'weights':>8} {'max ctx':>8} "
                             f"{'speed':>7}  {'here':<5} auto fit{R}"]
        for m in ms:
            name = m["name"]
            ctx = fit_cell(self.svc.max_ctx(m), 8, "none")
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
            head = (f"{CYN if af.pick and name == af.pick.name else ''}{name:<24}{R} {rank!s:>4} {arch:<6}"
                    f"{size(int(m.get('bytes', 0))):>8} {ctx} {speed_cell(m)}  {here} ")
            L += cwrap(head + verdict, w, " " * 70)
        return L
