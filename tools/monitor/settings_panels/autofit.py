"""The Auto fit panel: the goal and the candidates, Auto fit's choice for this Mac with its plan and
reasons, Use this / Download, and the ranking of every model; beside them (from SIDE_AT columns, else
under them) the Quick tip and the explanations. Each part is a section with its own level (Tab
selects it, L changes it); the page scrolls."""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from carl_core.domain.autofit import GOALS, SCOPES, AutoFit, Rejection, as_goal, as_scope
from carl_core.domain.units import file_size

from ..cards import sections as register
from ..fmt import (CYN, DIM, GRN, R, RED, YEL, CardLine, Row, bar, button_rows, row, vlen)
from ..model import ModelInfo, draft_bytes
from ..settings import SET_HELP, Pending, SettingsService, gib_pair, plan_words
from ..state import UIState
from ..words import goal_name, kv_name, scope_name, status_name
from .common import choice_line, fit_cell, speed_cell

from .page import Part, body_height, help_part, layout, progress, scrolled, section, side_width

register(three=["fit", "fitrank"], two=["fittip", "fithow", "fitmac", "fitgoal", "fitscope", "fitrankhelp",
                                        "fitother"])

INTRO = "Auto fit chooses the best catalogue model that fits this Mac, with its slots and context."
KEY_ROW = f"{GRN}●{R} This Mac (Auto-tune)   {DIM}○{R} Another Mac (the catalogue)   {CYN}★{R} Auto fit's choice"


def verdict(m: ModelInfo, af: AutoFit, passed: Dict[str, str]) -> str:
    """What Auto fit made of a model, in a few words (AUTO FIT's passed-over rows say why in full)."""
    name = m["name"]
    if af.pick and name == af.pick.name:
        return f"{CYN}★ its choice{R}"
    if af.scope == "downloaded" and m.get("status") != "downloaded":
        return f"{DIM}not downloaded{R}"
    if m.get("abliterated"):
        return f"{DIM}abliterated{R}"
    if m.get("custom") and m.get("auto_fit") is not True:
        return f"{DIM}custom{R}"
    if not isinstance(m.get("rank"), int):
        return f"{DIM}no quality rank{R}"
    reason = passed.get(name, "")
    if not reason:
        return f"{DIM}below the choice{R}"
    if reason.startswith("is not downloaded"):
        return f"{YEL}not downloaded{R}"
    if "keeps it for the " in reason:
        return f"{YEL}{reason.split('keeps it for the ', 1)[1]}{R}"
    if reason.startswith("does not fit"):
        return f"{YEL}does not fit{R}"
    return f"{YEL}size unknown{R}"


def passed_rows(rejected: Sequence[Rejection]) -> List[CardLine]:
    """Auto fit's better-ranked models that it passed over, one row each with the reason."""
    return [row("passed over" if i == 0 else "", f"{r.line().rstrip('.')}.") for i, r in enumerate(rejected)]


class AutoFitPanel:
    """Draws the Auto fit panel."""

    def __init__(self, svc: SettingsService) -> None:
        self.svc = svc

    def draw(self, ui: UIState, p: Pending, cols: int, height: int) -> List[Row]:
        """The sections: AUTO FIT (the goal, the candidates, the choice and why), RANKING; the Quick tip and
        the explanations beside them (or under them). The page scrolls (↑↓, PgUp PgDn, the wheel)."""
        svc = self.svc
        goal, scope = as_goal(p.get("goal")), as_scope(p.get("scope"))
        af = svc.auto_fit(p)
        tip = ("Press Enter to set this choice in the Server panel. Then press a there to start it."
               if af and af.pick and af.pick.downloaded
               else "Press d to download the choice. Then press Enter to use it." if af and af.pick
               else "Press g or f to change the goal or the candidates.")
        parts: List[Part] = [Part("fit", lambda w: self.choice_section(ui, af, p, goal, scope, w)),
                             help_part(ui, "fittip", "QUICK TIP", [f"{CYN}{tip}{R}"])]
        if af is not None:
            parts.append(Part("fitrank", lambda w: self.ranking_section(ui, af, w)))
        parts.append(help_part(ui, "fithow", "HOW AUTO FIT CHOOSES", [
            "Auto fit chooses the best stock model that fits this Mac. Quality comes from the quality rank (1 = best). "
            "Speed is important too, so the goal chooses the model family first. Auto fit never chooses an abliterated "
            "model: you choose one in the Server panel. It uses a custom model only when the model's card lets it."]))
        if af is not None:
            b = af.budget
            vm = ("CARL keeps more free while the VMware network is up." if b.vm_up
                  else "When the VMware network is up, CARL keeps 10 GiB free.")
            parts.append(help_part(ui, "fitmac", "THIS MAC", [f"{b.explain()} {vm}"]))
        parts += [help_part(ui, "fitgoal", "GOAL", [SET_HELP["goal"]]),
                  help_part(ui, "fitscope", "CANDIDATES", [SET_HELP["scope"]])]
        if af is not None:
            window = "the full cache" if svc.swa_mode() == "full" else "the window cache"
            parts.append(help_part(ui, "fitrankhelp", "ABOUT THE RANKING", [
                f"Max context (the full level) is the largest context per slot that fits this Mac, with 1 slot and q4 "
                f"context memory (for a Gemma model, with {window}). Prose tok/s: {GRN}●{R} is measured on this Mac by "
                f"Auto-tune, ○ on another Mac (the catalogue). Models of the same rank are builds of the same base "
                f"model. The command ./carl.sh fit shows the ranking too."]))
            other = as_goal("everyday" if goal == "hard-code" else "hard-code")
            oaf = svc.models.auto_fit(other, scope)
            if oaf:
                ol: List[CardLine] = [row("goal", goal_name(other))]
                if oaf.pick and oaf.plan:
                    ol += [row("choice", oaf.pick.name), row("with", plan_words(oaf.plan.slots, oaf.plan.ctx)),
                           row("context memory", kv_name(oaf.plan.kv, short=True))]
                else:
                    ol.append(row("choice", f"{RED}No model fits.{R}"))
                parts.append(help_part(ui, "fitother", "THE OTHER GOAL", ol, oaf.pick.name if oaf.pick else ""))
        rows = layout(ui, parts, cols, side_width(cols, side_min=48, main_max=118), balance=True)
        h = body_height(height)
        ui.keys = [("Enter", "use this")]
        if af and af.pick and not af.pick.downloaded:
            ui.keys.append(("d", "download it"))
        if len(rows) > h:
            ui.keys.append(("PgUp PgDn", "scroll"))
        ui.keys += [("g", "goal"), ("f", "candidates"), ("Tab", "section"), ("L", "level"), ("[ ]", "panels")]
        ui.keys_more = ["CARL saves the goal and the candidates at once. Then a start with the model set to auto "
                         "uses the new choice.",
                        "Use this sets the model, the context, the slots and the context memory in the Server panel. "
                        "Press a there to start it."]
        return scrolled(ui, rows, h, "fit_scroll")

    def choice_section(self, ui: UIState, af: Optional[AutoFit], p: Pending, goal: str, scope: str,
                       w: int) -> List[Row]:
        """AUTO FIT: the goal and the candidates, then the choice: its plan, its memory, whether it is
        downloaded, why, and the better-ranked models it passed over; the buttons; full: how the rank is
        built."""
        svc = self.svc
        tw = w - 4
        full = ui.levels.get("fit", 1) == 2
        L: List[CardLine] = [f"{DIM}{INTRO}{R}", ""]
        L += choice_line("Goal", [(g, goal_name(g), f"fgoal:{g}") for g in GOALS], goal, tw, 16)
        L += choice_line("Candidates", [(x, scope_name(x), f"fscope:{x}") for x in SCOPES], scope, tw, 16)
        L.append("")
        if af is None:
            why = (svc.models.fit_error or "there is no model list").rstrip(".")
            L.append(row("choice", f"{RED}Auto fit cannot work now: {why}.{R}"))
            return section(ui, "fit", "AUTO FIT", f"{RED}not available{R}", L, w, 3)
        if not af.pick or not af.plan:
            L += [row("choice", f"{RED}No model fits.{R}"), row("why", af.because().rstrip(".") + ".")]
            return section(ui, "fit", "AUTO FIT", f"{RED}no model{R}", L, w, 3)
        b = af.budget
        m = svc.models.by_name(af.pick.name)
        need, limit = gib_pair(af.plan.need, b.allowed)
        size = file_size(int(m["bytes"]) + draft_bytes(m)) if m else ""
        L += [row("suggests", f"{CYN}{af.pick.name}{R} ★"),
              row("with", plan_words(af.plan.slots, af.plan.ctx)),
              row("context memory", kv_name(af.plan.kv, short=True)),
              row("needs", f"{bar(af.plan.need / b.allowed if b.allowed else 0, 24)}  {need.replace(' GiB', '')} "
                           f"of {limit}"),
              row("status", f"{GRN}downloaded{R}" if af.pick.downloaded else
                  f"{YEL}not downloaded{R}" + (f"   {size} to download" if size else "")),
              row("why", af.because().rstrip(".") + ".")]
        if full:
            L.append(row("quality rank", "The parameters and the density count first, then the quantization. "
                                         "Rank 1 is the best."))
        if not af.pick.downloaded:
            L.append(row("until then", f"Auto starts {svc.resolved_model(dict(p, model='auto'))}: the best "
                                       f"downloaded model that fits."))
        L += passed_rows(af.rejected)
        acts = [("Use this (Enter)", "fuse")]
        if not af.pick.downloaded:
            acts.append(("Download it (d)", "fdl"))
        L += ["", *button_rows("", acts, tw)]
        if ui.dl:
            L += ["", *progress(ui.dl, tw)]
        return section(ui, "fit", "AUTO FIT", af.pick.name, L, w, 3)

    def ranking_section(self, ui: UIState, af: AutoFit, w: int) -> List[Row]:
        """RANKING: every model by quality rank: download size, prose speed (● this Mac, ○ another Mac),
        status and what Auto fit made of it; full adds the type and the largest context. The verdict goes
        on its own row when the table is wider than the card."""
        full = ui.levels.get("fitrank", 1) == 2
        tw = w - 4
        passed = {r.name: r.reason for r in af.rejected}
        ms = sorted(self.svc.models.get(), key=lambda m: (m.get("rank") if isinstance(m.get("rank"), int) else 99,
                                                          m["name"]))
        nw = max([len(str(m["name"])) for m in ms] + [5]) + 2
        mid = f"{'Type':<5}  {'Max context':>11}  " if full else ""
        head = f"  {'Model':<{nw}}{'Rank':>4}  {mid}{'Download':>8}  {'Prose tok/s':>11}  {'Status':<14}  "
        verdicts = [verdict(m, af, passed) for m in ms]
        own_row = vlen(head) + max([vlen(v) for v in verdicts] + [8]) > tw
        L: List[CardLine] = [f"{DIM}{head}{'' if own_row else 'Auto fit'}{R}"]
        for m, v in zip(ms, verdicts):
            name = str(m["name"])
            rank = m.get("rank") if isinstance(m.get("rank"), int) else "–"
            mid = ""
            if full:
                arch = str(m.get("arch") or "?").replace("moe", "MoE")
                mid = f"{arch:<5}  {fit_cell(self.svc.max_ctx(m), 11, 'none')}  "
            st = str(m["status"])
            col = CYN if af.pick and name == af.pick.name else ""
            text = (f"  {col}{name:<{nw}}{R}{rank!s:>4}  {mid}{file_size(int(m.get('bytes', 0)) + draft_bytes(m)):>8}  "
                    f"{speed_cell(m, 11)}  {GRN if st == 'downloaded' else DIM}{status_name(st):<14}{R}  ")
            L += [text, f"    {DIM}Auto fit:{R} {v}"] if own_row else [text + v]
        return section(ui, "fitrank", "RANKING", f"{len(ms)} models", [*L, "", KEY_ROW], w, 3)
