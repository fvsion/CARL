"""The Settings tab's drop-downs (a model, a sort or filter, a Hugging Face file, the model to tune)
and its yes / no questions. Each sets its own keys for the footer."""
from __future__ import annotations

from typing import List, Optional

from ..arrange import FILTERS, SORTS, label as arrange_label
from ..fmt import ANSI, DIM, R, YEL, CardLine, Row, buttons, cwrap, draw_card, indent, wrap, wwrap
from ..settings import Pending, SettingsService, plan_words
from ..state import Confirm, PickItem, Picker, UIState
from .common import selectable
from .model_lines import ModelLines, model_header

PICKER_FOOT = ("★ = Auto fit's choice for this Mac. Fits this Mac = the slots and the context that fit; red = it does "
               "not fit. To add a model from Hugging Face: the Models panel (]), then h.")


class Pickers:
    """Builds and draws the drop-downs; draws the questions."""

    def __init__(self, svc: SettingsService, lines: ModelLines) -> None:
        self.svc = svc
        self.lines = lines

    def picker(self, pk: Picker, cols: int, height: int, ui: Optional[UIState] = None) -> List[Row]:
        """A drop-down list around its selection, with the selected model's description."""
        full = bool(ui and ui.full)
        w = cols - 2
        n = len(pk.items)
        vis = max(height - 9, 3)
        top = min(max(pk.sel - vis // 2, 0), max(n - vis, 0))
        L: List[CardLine] = [pk.header or model_header(full)]
        for i in range(top, min(top + vis, n)):
            item = pk.items[i][1]
            L.append(selectable(item if isinstance(item, str) else self.lines.model_line(item, pk.mark, full),
                                i == pk.sel, w - 4, f"pick:{i}"))
        if n > vis:
            L.append(f"{DIM}  {top + 1}-{min(top + vis, n)} of {n} (↑↓ PgUp PgDn){R}")
        sel_item = pk.items[pk.sel][1]
        if not isinstance(sel_item, str):
            L += ["", *[f"{DIM}{x}{R}" for x in wwrap(sel_item.get("why_use") or sel_item.get("summary")
                                                        or "", w - 6)[:3]]]
            if sel_item.get("trade_offs"):
                L += [f"{YEL}{x}{R}" for x in wwrap(f"Trade-off: {sel_item['trade_offs']}", w - 6)[:2]]
        elif pk.sel == 0 and pk.note:
            L += ["", *cwrap(f"{DIM}{pk.note}{R}", w - 6)]
        L += ["", buttons("", [("Choose (Enter)", "pickok"), ("Cancel (Esc)", "pickno")])]
        if pk.foot:
            L += cwrap(f"{DIM}{pk.foot}{R}", w - 4)
        if ui is not None:
            ui.keys = [("↑↓", "select"), ("Enter", "choose"), ("Esc", "cancel")] + (
                [("s f", "sort / show")] if pk.on_pick == "pickmodel" else [])
        count = f"{n - 1} {pk.noun} + auto" if pk.on_pick == "pickmodel" else f"{n} {pk.noun}"
        return indent(draw_card("picker", pk.title, f"{DIM}{count}{R}", L, w, 1))

    @staticmethod
    def arrange_picker(kind: str, sort: int, filt: int, reopen: Optional[str] = None) -> Picker:
        """A drop-down of every sort (or filter) option, the current one selected."""
        opts, cur = (SORTS, sort) if kind == "sort" else (FILTERS, filt)
        items: List[PickItem] = [(str(i), f"{i + 1:>2}. {o}") for i, o in enumerate(opts)]
        return Picker(title="SORT THE MODELS BY" if kind == "sort" else "SHOW THESE MODELS", items=items,
                      on_pick="picksort" if kind == "sort" else "pickfilter", sel=cur % len(opts),
                      noun="choices", header=f"{DIM}  quality = CARL's quality rank (1 = best){R}" if kind == "sort"
                      else f"{DIM}  fits this Mac = at least 32K tokens fit with 1 slot{R}",
                      foot="", reopen=reopen)

    def model_picker(self, cur: str, sort: int = 0, filt: int = 0, p: Optional[Pending] = None) -> Picker:
        """A drop-down of the models ("auto" on top) in the chosen order and filter; s / f change them.
        p: the pending settings (Auto fit's goal and candidates)."""
        ms = self.lines.visible(sort, filt)
        q: Pending = dict(p or {}, model="auto")
        af = self.svc.auto_fit(q)
        start = self.svc.resolved_model(q)
        pick = af.name if af else None
        items: List[PickItem] = [("auto", f"{'auto':<24} {DIM}(now {start}){R}")]
        if af and af.pick and af.plan:
            note = f"auto = Auto fit's choice for this Mac: {af.pick.name} with {plan_words(af.plan.slots, af.plan.ctx)}."
        else:
            note = f"auto = Auto fit's choice. Auto fit cannot choose now: {self.svc.models.fit_error or 'no model fits'}."
        if pick and pick != start:
            note += f" {pick} is not downloaded. Until then, auto starts {start}."
        items += [(m["name"], m) for m in ms]
        so, fi = arrange_label(sort, filt)
        return Picker(title="CHOOSE A MODEL", items=items, on_pick="pickmodel",
                      sel=next((i for i, it in enumerate(items) if it[0] == cur), 0),
                      noun="models", header=None, mark=pick, note=note,
                      foot=f"Sort: {so} (s). Show: {fi} (f). " + PICKER_FOOT)

    @staticmethod
    def confirm(c: Confirm, cols: int, ui: Optional[UIState] = None) -> List[Row]:
        """A yes / no question: the verb on the button."""
        w = min(cols - 2, 96)
        L: List[CardLine] = ["", *[x for line in c.lines for x in (cwrap(line, w - 4) if ANSI.search(line) or " " in line
                                                                   else wrap(line, w - 4))], "",
                             buttons("  ", [(f"{c.yes_label} (y)", c.yes), ("Cancel (n)", "c2no")])]
        if ui is not None:
            ui.keys = [("y", c.yes_label.lower()), ("n Esc", "cancel")]
        return indent(draw_card("confirm2", c.title, "", L, w, 1))
