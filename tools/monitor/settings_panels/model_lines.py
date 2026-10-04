"""The model lists of the Settings panels: the models in the chosen sort and filter, and one
model as a list line (the Models panel, the model drop-down)."""
from __future__ import annotations

from typing import List, Optional

from ..arrange import MIN_FIT, arrange
from ..fmt import CYN, DIM, GRN, R, RED, YEL, size, vlen
from ..model import ModelInfo
from ..settings import SettingsService
from .common import fit_cell, good_for_chip, speed_cell

MODEL_HEADER = f"{DIM}{'':2}{'model':<26} {'size':>8}  {'status':<10} {'fits':>5} {'speed':>7} role and good for{R}"


class ModelLines:
    """Lists models for the panels; the largest window that fits comes from the SettingsService."""

    def __init__(self, svc: SettingsService) -> None:
        self.svc = svc

    def visible(self, sort: int, filt: int) -> List[ModelInfo]:
        """The models in the current sort order, filtered (the Models panel and the drop-down)."""
        return arrange(self.svc.models.get(), sort, filt, self.svc.max_ctx)

    def too_big(self, m: ModelInfo) -> bool:
        """Known not to fit this Mac: less than a 32K window (1 slot, q4_0)."""
        mx = self.svc.max_ctx(m)
        return mx is not None and mx < MIN_FIT

    def model_line(self, m: ModelInfo, pick: Optional[str] = None) -> str:
        """One model in a list: name (★ = auto fit's pick, red = too big for this Mac), size,
        status, the largest window that fits, tuned, summary."""
        st = {"downloaded": f"{GRN}downloaded{R}", "partial": f"{YEL}partial{R}", "missing": f"{DIM}not here{R}"}[m["status"]]
        fitc = fit_cell(self.svc.max_ctx(m), 5, "no fit")
        star = f" {CYN}★{R}" if m["name"] == pick else ""
        name = f"{RED if self.too_big(m) else ''}{m['name']}{R}{star}"
        name += " " * max(0, 26 - vlen(name))
        head = f"{name} {size(m['bytes']):>8}  {st:<10}{' ' * max(0, 10 - vlen(st))} {fitc} {speed_cell(m)} "
        tags = " ".join(good_for_chip(str(t)) for t in (m.get("good_for") or []))
        return head + f"{CYN}{m.get('role') or ''}{R} {tags} " + ("" if m.get("role") else f"{DIM}{m.get('summary', '')}{R}")
