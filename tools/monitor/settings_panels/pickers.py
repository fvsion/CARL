"""The Settings tab's drop-downs (a model, a sort or filter, a Hugging Face file, the model to
tune) and its yes / no questions."""
from __future__ import annotations

from typing import List, Optional

from ..arrange import FILTERS, SORTS, label as arrange_label
from ..fmt import CYN, DIM, R, YEL, CardLine, Row, buttons, cwrap, draw_card, indent, wwrap
from ..settings import Pending, SettingsService
from ..state import Confirm, PickItem, Picker
from .common import selectable
from .model_lines import MODEL_HEADER, ModelLines

PICKER_FOOT = ("Press ↑ ↓ to select a model. Press Enter to choose it. Press Esc to cancel. ★ = auto fit's pick · "
               "fits = the largest window per slot that fits this Mac (q4_0, 1 slot; red = too big). To download any "
               "GGUF from Hugging Face, press ] for the Models panel, then h.")


class Pickers:
    """Builds and draws the drop-downs; draws the questions."""

    def __init__(self, svc: SettingsService, lines: ModelLines) -> None:
        self.svc = svc
        self.lines = lines

    def picker(self, pk: Picker, cols: int, height: int) -> List[Row]:
        """A drop-down list around its selection, with the selected model's description."""
        w = cols - 2
        n = len(pk.items)
        vis = max(height - 9, 3)
        top = min(max(pk.sel - vis // 2, 0), max(n - vis, 0))
        L: List[CardLine] = [pk.header or MODEL_HEADER]
        for i in range(top, min(top + vis, n)):
            item = pk.items[i][1]
            L.append(selectable(item if isinstance(item, str) else self.lines.model_line(item, pk.mark), i == pk.sel, w,
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

    @staticmethod
    def arrange_picker(kind: str, sort: int, filt: int, reopen: Optional[str] = None) -> Picker:
        """A drop-down of every sort (or filter) option, the current one selected."""
        opts, cur = (SORTS, sort) if kind == "sort" else (FILTERS, filt)
        items: List[PickItem] = [(str(i), f"{i + 1:>2}. {o}") for i, o in enumerate(opts)]
        return Picker(title="SORT MODELS BY" if kind == "sort" else "SHOW MODELS", items=items,
                      on_pick="picksort" if kind == "sort" else "pickfilter", sel=cur % len(opts),
                      noun="options", header=f"{DIM}  press ↑ ↓ to select, then Enter{R}",
                      foot="Press ↑ ↓ to select an option. Press Enter to choose it. Press Esc to cancel.", reopen=reopen)

    def model_picker(self, cur: str, sort: int = 0, filt: int = 0, p: Optional[Pending] = None) -> Picker:
        """A drop-down of the models ("auto" on top) in the chosen order and filter; s / f change them.
        p: the pending settings (auto fit's goal and scope)."""
        ms = self.lines.visible(sort, filt)
        q: Pending = dict(p or {}, model="auto")
        af = self.svc.auto_fit(q)
        start = self.svc.resolved_model(q)
        pick = af.name if af else None
        items: List[PickItem] = [("auto", f"{CYN}★{R} {'auto':<23} {DIM}→ {start}{R}")]
        note = (f"auto = auto fit's pick for this Mac: {af.summary()}." if af and af.pick else
                f"auto fit: {af.because() if af else self.svc.models.fit_error or 'unavailable'}.")
        if pick and pick != start:
            note += f" {pick} is not downloaded. Until then, a start uses {start}."
        items += [(m["name"], m) for m in ms]
        so, fi = arrange_label(sort, filt)
        return Picker(title="CHOOSE A MODEL", items=items, on_pick="pickmodel",
                      sel=next((i for i, it in enumerate(items) if it[0] == cur), 0),
                      noun=f"models · sort: {so} · show: {fi}", mark=pick, note=note,
                      foot=PICKER_FOOT + " Press s / S for the next / previous sort. Press f / F for the next / "
                           "previous filter (use case, stock, dense / MoE, downloaded, fits).")

    @staticmethod
    def confirm(c: Confirm, cols: int) -> List[Row]:
        """A yes / no question."""
        w = min(cols - 2, 96)
        L: List[CardLine] = ["", *[x for line in c.lines for x in cwrap(line, w - 4)], "",
                             buttons("  ", [("Yes (y)", c.yes), ("Cancel (n)", "c2no")]),
                             f"{DIM}  Press y for yes. Press n or Esc to cancel.{R}"]
        return indent(draw_card("confirm2", c.title, "", L, w, 2))
