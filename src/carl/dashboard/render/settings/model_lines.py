"""The model lists of the Settings panels: the models in the chosen sort and filter, and one model as
a table row (the Models panel, the model drop-down): its download size (GB), status, whether it fits
this Mac (and the plan), speed and what it is for; full detail: its quality rank and largest context."""
from __future__ import annotations

from typing import Dict, List, Mapping, Optional, Sequence

from ....application.settings_service import SettingsService
from ....domain.catalog.inventory import group_of
from ....domain.catalog.quants import group_models, mtp_kind, mtp_mark, quant_of, row_of
from ....domain.server.snapshot import ModelInfo, draft_bytes
from ....domain.shared.units import file_size, memory, tokens
from ....ui.fmt import CYN, DIM, GRN, R, RED, YEL, fit, vlen
from ....ui.words import computer, computer_cap, status_name
from ...arrange import arrange
from ...state import UIState
from .common import fit_cell, long_date, source_cells, speed_cell, speed_sources

NAME_W, FITS_W, SPEED_W, QUANT_W = 22, 14, 13, 16
WIDE = 150                          # from this text width the list shows each source's prose / code / edit


def has_quants(models: Sequence[ModelInfo]) -> bool:
    """Some model of the list has several quantizations: its table has a Quant column (decision A68)."""
    return any(m.get("quants") for m in models)


def model_header(full: bool = False, w: int = 0, speeds: bool = False, quant: bool = False) -> str:
    """The column names of a model table (w: the text width; speeds: the speeds view, t; quant: with the Quant
    column, ← → in the list change it)."""
    if speeds:
        return (f"{DIM}  {'Model':<{NAME_W}}  {computer_cap():<18}   {'Catalogue':<18}   {'Measured on':<20}{'Date':<13}"
                f"Speculation{R}\n{DIM}  {'':<{NAME_W}}  {'Prose  Code  Edit':>18}   {'Prose  Code  Edit':>18}{R}")
    q = f"{'Quant':<{QUANT_W}} " if quant else ""
    lead = (f"  {'Model':<{NAME_W}} {q}{'Rank':>4} {'Download':>9}  {'Status':<15} {'Max context':>11} " if full else
            f"  {'Model':<{NAME_W}} {q}{'Download':>9}  {'Status':<15} {'Fits ' + computer():<{FITS_W}} ")
    if w >= WIDE:
        return f"{DIM}{lead}{computer_cap():>18}   {'Catalogue':>18}  What it is for{R}\n" \
               f"{DIM}{'':<{len(lead)}}{'Prose  Code  Edit':>18}   {'Prose  Code  Edit':>18}{R}"
    return f"{DIM}{lead}{'Prose tok/s':>{SPEED_W}}  What it is for{R}"


class ModelLines:
    """Lists models for the panels; the plan and the largest context come from the SettingsService."""

    def __init__(self, svc: SettingsService) -> None:
        self.svc = svc

    def visible(self, sort: int, filt: int, quants: Optional[Mapping[str, str]] = None) -> List[ModelInfo]:
        """The models in the current sort order, filtered (the Models panel and the drop-down). A model with several
        quantizations is one row (decision A68): the entry of the quantization shown, quants (a model's name -> an
        entry's name), else the default one, with its name (group) and its quantizations (quants)."""
        return arrange(self.rows(self.svc.models.get(), quants or {}), sort, filt, self.svc.max_ctx)

    def rows(self, models: List[ModelInfo], quants: Mapping[str, str]) -> List[ModelInfo]:
        """One row for each model: an entry that stands alone as it is; a model's entries as the one shown."""
        defaults = self.svc.models.defaults()
        out: List[ModelInfo] = []
        for g in group_models(models):
            if not g.several:
                out.append(g.entries[0])
                continue
            names = [n for n in (quants.get(g.name), defaults.get(g.name)) if n]
            shown = next((m for n in names for m in g.entries if m.get("name") == n), g.entries[0])
            out.append(row_of(g, shown))
        return out

    def shown(self, ui: UIState) -> Dict[str, str]:
        """The quantization shown for each model: the Server panel's model first, then what was chosen in a list."""
        cur = str((ui.pending or {}).get("model", ""))
        group = group_of(self.svc.models.get(), cur) if cur else None
        pinned = {group.name: cur} if group is not None and group.several else {}
        return {**pinned, **ui.mquants}

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

    @staticmethod
    def quant_cell(m: ModelInfo) -> str:
        """The Quant column: the quantization (in ‹ › when the model has others), then its MTP mark (A73)."""
        mark = mtp_mark(mtp_kind(m))
        quant = f"‹{quant_of(m)}›" if m.get("quants") else quant_of(m)
        pad = " " * max(QUANT_W - len(quant) - (len(mark) + 1 if mark else 0), 0)
        return (f"{CYN if m.get('quants') else DIM}{quant}{R}" + (f" {GRN}{mark}{R}" if mark else "") + pad)

    def model_line(self, m: ModelInfo, pick: Optional[str] = None, full: bool = False, w: int = 0,
                   speeds: bool = False, quant: bool = False) -> str:
        """One model in a table (model_header): name (★ = Auto fit's choice, red = does not fit this Mac), the
        quantization shown (quant: in ‹ › when the model has others), download size, status, fits this Mac (full:
        rank and the largest context), speed, what it is for."""
        st = m["status"]
        stc = GRN if st == "downloaded" else YEL if st == "partial" else DIM
        star = f" {CYN}★{R}" if m["name"] == pick else ""
        name = f"{RED if self.too_big(m) else ''}{m.get('group') or m['name']}{R}{star}"
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
        qcell = ""
        if quant:
            qcell = self.quant_cell(m) + " "
        if full:
            rank = m.get("rank") if isinstance(m.get("rank"), int) else "–"
            mid = f"{qcell}{rank!s:>4} {size:>9}  {status} {fit_cell(self.svc.max_ctx(m), 11, 'none')}"
        else:
            ft = self.fits_text(m)
            mid = f"{qcell}{size:>9}  {status} {ft}{' ' * max(0, FITS_W - vlen(ft))}"
        if w >= WIDE:
            here, cat = (v for _, v in speed_sources(m))
            return f"{name} {mid} {source_cells(here)}   {source_cells(cat)}  {CYN}{what}{R}"
        return f"{name} {mid} {speed_cell(m, SPEED_W)}  {CYN}{what}{R}"
