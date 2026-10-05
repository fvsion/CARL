"""The Auto fit panel: the goal and the candidates, Auto fit's choice for this Mac with its plan and
reasons, Use this / Download, the memory a model can use here, the other goal's choice, and the
ranking of every model."""
from __future__ import annotations

from typing import List

from carl_core.domain.autofit import GOALS, SCOPES, AutoFit, as_goal, as_scope
from carl_core.domain.units import file_size, memory

from ..model import draft_bytes
from ..fmt import (B, CYN, DIM, GRN, R, RED, YEL, CardLine, Row, Section, button_rows, cwrap, draw_card, fit, heading,
                   indent, vlen, with_side)
from ..settings import SET_HELP, Pending, SettingsService, plan_words
from ..state import UIState
from ..words import goal_name, scope_name, status_name
from .common import choice_line, download_status, fit_cell, label_wrap, passed_over, speed_cell

INTRO = "Auto fit chooses the best catalogue model that fits this Mac, with its slots and context."


class AutoFitPanel:
    """Draws the Auto fit panel."""

    def __init__(self, svc: SettingsService) -> None:
        self.svc = svc

    def draw(self, ui: UIState, p: Pending, cols: int, height: int) -> List[Row]:
        """The goal and the candidates, the choice with its plan and reasons, Use this / Download, the
        other goal's choice, and the ranking. Scrolls (↑↓, PgUp PgDn, the wheel)."""
        svc = self.svc
        w = cols - 2
        goal, scope = as_goal(p.get("goal")), as_scope(p.get("scope"))
        af = svc.auto_fit(p)
        ui.keys = [("Enter", "use this"), ("d", "download it"), ("g", "the other goal"), ("f", "the other candidates"),
                   ("↑↓ PgUp PgDn", "scroll"), ("[ ]", "panels")]
        ui.keys_more = ["CARL saves the goal and the candidates at once. Then auto starts the new choice.",
                        "Use this sets the model, the context, the slots and the context memory in the Server panel. "
                        "Press a there to start it."]
        how: List[CardLine] = ["Auto fit chooses the best stock model that fits this Mac. Quality comes from the quality "
                               "rank (1 = best). Speed is important too, so the goal chooses the model family first. "
                               "Auto fit never chooses an abliterated model: you choose it yourself. It uses a custom "
                               "model only when its card lets it."]
        sections: List[Section] = [("How Auto fit chooses", how), ("Goal", [SET_HELP["goal"]]),
                                   ("Candidates", [SET_HELP["scope"]])]

        def choices(mw: int) -> List[CardLine]:
            L: List[CardLine] = [*cwrap(f"{DIM}{INTRO}{R}", mw), ""]
            for label, opts, cur in (("Goal", [(g, goal_name(g), f"fgoal:{g}") for g in GOALS], goal),
                                     ("Candidates", [(x, scope_name(x), f"fscope:{x}") for x in SCOPES], scope)):
                L += choice_line(label, opts, cur, mw, 13)
            return L

        if af is None:
            L = with_side(lambda mw: [*choices(mw), "", *cwrap(f"{RED}Auto fit cannot work now: "
                                                              f"{svc.models.fit_error or 'no model list'}.{R}", mw)],
                          "Press g or f to change the goal or the candidates.", sections, w - 4)
            return indent(draw_card("autofit", "AUTO FIT", "", L, w, 1))
        b = af.budget
        vm = ("CARL keeps more free while the VMware network is up." if b.vm_up
              else "When the VMware network is up, CARL keeps 10 GiB free.")
        sections.insert(1, ("This Mac", [f"{b.explain()} {DIM}{vm}{R}"]))
        sections.append(("The ranking", [
            f"Max context = the largest context per slot that fits this Mac (1 slot, q4 context memory"
            f"{'; a Gemma model with the full cache' if svc.swa_mode() == 'full' else '; a Gemma model with the window cache'}"
            f"). Speed: {GRN}●{R} measured on this Mac, ○ on another Mac. The same rank = the same base model. "
            f"./carl.sh fit shows the ranking too."]))
        other = as_goal("everyday" if goal == "hard-code" else "hard-code")
        oaf = svc.models.auto_fit(other, scope)
        if oaf:
            sections.append((f"The other goal: {goal_name(other)}", [
                f"Auto fit would choose {oaf.pick.name} with {plan_words(oaf.plan.slots, oaf.plan.ctx)}."
                if oaf.pick and oaf.plan else "No model fits with that goal."]))
        tip = ("Press Enter to set this choice in the Server panel. Then press a there to start it."
               if af.pick and af.pick.downloaded
               else "Press d to download the choice. Then press Enter to use it." if af.pick
               else "Press g or f to change the goal or the candidates.")

        def main(mw: int) -> List[CardLine]:
            L = [*choices(mw), "", heading("Auto fit suggests", mw)]
            if af.pick and af.plan:
                m = svc.models.by_name(af.pick.name)
                where = (f"{GRN}It is downloaded.{R}" if af.pick.downloaded else
                         f"{YEL}It is not downloaded ({file_size(int(m['bytes']) + draft_bytes(m)) if m else 'a download'}).{R}")
                L += cwrap(f"{B}{CYN}★ {af.pick.name}{R} with {af.plan.describe()}. It needs {memory(af.plan.need)} of "
                           f"the {memory(b.allowed)} that a model can use here. {where}", mw, "  ")
                L += label_wrap("Why", af.because().rstrip(".") + ".", mw)
                if not af.pick.downloaded:
                    start = svc.resolved_model(dict(p, model="auto"))
                    L += label_wrap("Until then", f"auto starts {start}: the best downloaded model that fits.", mw, YEL)
                L += passed_over(af.rejected, mw)
                acts = [("Use this (Enter)", "fuse")]
                if not af.pick.downloaded:
                    acts.append(("Download it (d)", "fdl"))
                L += ["", *button_rows("", acts, mw)]
                if ui.dl:
                    L += download_status(ui.dl)
            else:
                L += cwrap(f"{RED}No model fits this Mac with this goal and these candidates.{R} "
                           f"{af.because().rstrip('.')}.", mw)
            return [*L, "", heading("Ranking", mw), *self.ranking(af, mw)]

        L = with_side(main, tip, sections, w - 4, main_w=118)
        summ = (f"{af.pick.name} with {plan_words(af.plan.slots, af.plan.ctx)}" if af.pick and af.plan
                else f"{RED}no model fits{R}")
        rows = indent(draw_card("autofit", "AUTO FIT", f"{DIM}{summ}{R}", L, w, 1))
        ui.fit_scroll = max(0, min(ui.fit_scroll, len(rows) - height))
        return rows[ui.fit_scroll:ui.fit_scroll + height]

    def ranking(self, af: AutoFit, w: int) -> List[CardLine]:
        """Every model by quality rank: its type, download size, the largest context (1 slot, q4), speed,
        status, and what Auto fit made of it."""
        passed = {r.name: r.reason for r in af.rejected}
        ms = sorted(self.svc.models.get(), key=lambda m: (m.get("rank") or 99, m["name"]))
        L: List[CardLine] = [f"{DIM}  {'Model':<24} {'Rank':>4} {'Type':<6}{'Download':>9} {'Max context':>11} "
                             f"{'Speed':>15}  {'Status':<15} Auto fit{R}"]
        for m in ms:
            name = m["name"]
            ctx = fit_cell(self.svc.max_ctx(m), 11, "none")
            if af.pick and name == af.pick.name:
                verdict = f"{CYN}★ its choice{R}"
            elif m.get("abliterated"):
                verdict = f"{DIM}abliterated: you choose it yourself{R}"
            elif m.get("custom") and m.get("auto_fit") is not True:
                verdict = f"{DIM}custom: not in Auto fit (its card can let it in){R}"
            elif not isinstance(m.get("rank"), int):
                verdict = f"{DIM}no quality rank{R}"
            elif name in passed:
                verdict = f"{YEL}{passed[name]}{R}"
            else:
                verdict = f"{DIM}ranked below the choice{R}"
            st = m["status"]
            status = f"{GRN if st == 'downloaded' else DIM}{status_name(st):<15}{R}"
            rank = m.get("rank") if isinstance(m.get("rank"), int) else "–"
            arch = str(m.get("arch") or "?").replace("moe", "MoE")
            head = (f"  {CYN if af.pick and name == af.pick.name else ''}{fit(name, 24)}{R} {rank!s:>4} {arch:<6}"
                    f"{file_size(int(m.get('bytes', 0)) + draft_bytes(m)):>9} {ctx} {speed_cell(m)}  {status} ")
            L += cwrap(head + verdict, w, " " * min(vlen(head), w - 20))
        return L

