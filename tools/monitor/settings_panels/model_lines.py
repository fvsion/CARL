"""The model lists of the Settings panels: the models in the chosen sort and filter, and one model as
a table row (the Models panel, the model drop-down): its download size (GB), status, whether it fits
this Mac (and the plan), speed and what it is for; full detail: its quality rank and largest context."""
from __future__ import annotations

from typing import List, Optional

from carl_core.domain.units import file_size, memory, tokens

from ..arrange import arrange
from ..fmt import CYN, DIM, GRN, R, RED, YEL, fit, vlen
from ..model import ModelInfo, draft_bytes
from ..settings import SettingsService
from ..words import status_name
from .common import fit_cell, speed_cell

NAME_W, FITS_W, SPEED_W = 22, 14, 14


def model_header(full: bool = False) -> str:
    """The column names of a model table."""
    if full:
        return (f"{DIM}  {'Model':<{NAME_W}} {'Rank':>4} {'Download':>9}  {'Status':<15} {'Max context':>11} "
                f"{'Speed':>{SPEED_W}}  What it is for{R}")
    return (f"{DIM}  {'Model':<{NAME_W}} {'Download':>9}  {'Status':<15} {'Fits this Mac':<{FITS_W}} "
            f"{'Speed':>{SPEED_W}}  What it is for{R}")


class ModelLines:
    """Lists models for the panels; the plan and the largest context come from the SettingsService."""

    def __init__(self, svc: SettingsService) -> None:
        self.svc = svc

    def visible(self, sort: int, filt: int) -> List[ModelInfo]:
        """The models in the current sort order, filtered (the Models panel and the drop-down)."""
        return arrange(self.svc.models.get(), sort, filt, self.svc.max_ctx)

    def too_big(self, m: ModelInfo) -> bool:
        """Known not to fit this Mac with its recommended plan (1 slot at the least)."""
        plan = self.svc.plan_of(m)
        return plan is not None and not plan.fits and not plan.error

    def fits_text(self, m: ModelInfo) -> str:
        """'yes, 2 × 96K' (the plan that fits), 'no (17.5 GiB)' (what it needs), or 'not known'."""
        plan = self.svc.plan_of(m)
        if plan is None or plan.error:
            return f"{DIM}not known{R}"
        if plan.fits:
            return f"{GRN}yes{R}, {plan.slots} × {tokens(plan.ctx)}"
        return f"{RED}no{R} ({memory(plan.need)})"

    def model_line(self, m: ModelInfo, pick: Optional[str] = None, full: bool = False) -> str:
        """One model in a table (model_header): name (★ = Auto fit's choice, red = does not fit this Mac),
        download size, status, fits this Mac (full: rank and the largest context), speed, what it is for."""
        st = m["status"]
        stc = GRN if st == "downloaded" else YEL if st == "partial" else DIM
        star = f" {CYN}★{R}" if m["name"] == pick else ""
        name = f"{RED if self.too_big(m) else ''}{m['name']}{R}{star}"
        name = fit(name, NAME_W) if vlen(name) > NAME_W else name + " " * (NAME_W - vlen(name))
        size = file_size(int(m.get("bytes", 0)) + draft_bytes(m))
        status = f"{stc}{status_name(st):<15}{R}"
        what = str(m.get("role") or m.get("summary") or "")
        if m.get("custom") and not m.get("role"):
            what = "Custom model: write its card (e)"
        if full:
            rank = m.get("rank") if isinstance(m.get("rank"), int) else "–"
            mid = f"{rank!s:>4} {size:>9}  {status} {fit_cell(self.svc.max_ctx(m), 11, 'none')}"
        else:
            ft = self.fits_text(m)
            mid = f"{size:>9}  {status} {ft}{' ' * max(0, FITS_W - vlen(ft))}"
        return f"{name} {mid} {speed_cell(m, SPEED_W)}  {CYN}{what}{R}"
