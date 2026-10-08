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
from .common import fit_cell, long_date, source_cells, speed_cell, speed_sources

NAME_W, FITS_W, SPEED_W = 22, 14, 13
WIDE = 150                          # from this text width the list shows each source's prose / code / edit


def model_header(full: bool = False, w: int = 0, speeds: bool = False) -> str:
    """The column names of a model table (w: the text width; speeds: the speeds view, t)."""
    if speeds:
        return (f"{DIM}  {'Model':<{NAME_W}}  {'This Mac':<18}   {'Catalogue':<18}   {'Measured on':<20}{'Date':<13}"
                f"Speculation{R}\n{DIM}  {'':<{NAME_W}}  {'Prose  Code  Edit':>18}   {'Prose  Code  Edit':>18}{R}")
    lead = (f"  {'Model':<{NAME_W}} {'Rank':>4} {'Download':>9}  {'Status':<15} {'Max context':>11} " if full else
            f"  {'Model':<{NAME_W}} {'Download':>9}  {'Status':<15} {'Fits this Mac':<{FITS_W}} ")
    if w >= WIDE:
        return f"{DIM}{lead}{'This Mac':>18}   {'Catalogue':>18}  What it is for{R}\n" \
               f"{DIM}{'':<{len(lead)}}{'Prose  Code  Edit':>18}   {'Prose  Code  Edit':>18}{R}"
    return f"{DIM}{lead}{'Prose tok/s':>{SPEED_W}}  What it is for{R}"


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

    def model_line(self, m: ModelInfo, pick: Optional[str] = None, full: bool = False, w: int = 0,
                   speeds: bool = False) -> str:
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
        if speeds:
            here, cat = (v for _, v in speed_sources(m))
            src = cat or here or {}
            return (f"{name}  {source_cells(here)}   {source_cells(cat)}   {str(src.get('machine') or '–'):<20}"
                    f"{long_date(src.get('date')):<13}{src.get('spec') or ''}")
        if full:
            rank = m.get("rank") if isinstance(m.get("rank"), int) else "–"
            mid = f"{rank!s:>4} {size:>9}  {status} {fit_cell(self.svc.max_ctx(m), 11, 'none')}"
        else:
            ft = self.fits_text(m)
            mid = f"{size:>9}  {status} {ft}{' ' * max(0, FITS_W - vlen(ft))}"
        if w >= WIDE:
            here, cat = (v for _, v in speed_sources(m))
            return f"{name} {mid} {source_cells(here)}   {source_cells(cat)}  {CYN}{what}{R}"
        return f"{name} {mid} {speed_cell(m, SPEED_W)}  {CYN}{what}{R}"
