"""The Settings tab's drop-downs (a model, a sort or filter, a Hugging Face file, the model to tune)
and its yes / no questions. Each sets its own keys for the footer."""
from __future__ import annotations

from typing import Dict, List, Mapping, Optional

from ....application.settings_service import Pending, SettingsService, plan_words
from ....domain.catalog.inventory import group_of
from ....ui.fmt import (ANSI, CYN, DIM, GRN, R, RED, YEL, CardLine, Row, aligned, buttons, cwrap, draw_card, indent,
                        row, wrap)
from ....ui.words import computer, other_computer
from ...act import Verb, act as make
from ...arrange import FILTERS, SORTS, label as arrange_label
from ...shown import Shown
from ...state import Confirm, PickItem, Picker, UIState
from .common import quant_line, selectable
from .model_lines import ModelLines, has_quants, model_header


def picker_foot() -> str:
    return (f"{GRN}●{R} {computer()} (Auto-tune)   {DIM}○{R} {other_computer()} (the catalogue)   {CYN}★{R} "
            f"Auto fit's choice   {RED}red{R}: does not fit")


class Pickers:
    """Builds and draws the drop-downs; draws the questions."""

    def __init__(self, svc: SettingsService, lines: ModelLines) -> None:
        self.svc = svc
        self.lines = lines

    def picker(self, pk: Picker, cols: int, height: int, ui: Optional[UIState] = None,
               out: Optional[Shown] = None) -> List[Row]:
        """A drop-down list around its selection, with the selected model's description. Its keys go to out."""
        full = bool(ui and ui.full)
        w = cols - 2
        n = len(pk.items)
        vis = max(height - 9, 3)
        top = min(max(pk.sel - vis // 2, 0), max(n - vis, 0))
        qc = has_quants([it[1] for it in pk.items if not isinstance(it[1], str)])
        L: List[CardLine] = [*(pk.header or model_header(full, w - 4, quant=qc)).split("\n")]
        for i in range(top, min(top + vis, n)):
            item = pk.items[i][1]
            L.append(selectable(item if isinstance(item, str) else
                                self.lines.model_line(item, pk.mark, full, w - 4, quant=qc),
                                i == pk.sel, w - 4, make(Verb.PICK, i)))
        if n > vis:
            L.append(f"{DIM}  {top + 1}-{min(top + vis, n)} of {n} (↑↓ PgUp PgDn){R}")
        sel_item = pk.items[pk.sel][1]
        if not isinstance(sel_item, str):
            sel_rows = [row("why use it", str(sel_item.get("why_use") or sel_item.get("summary") or "–"))]
            if sel_item.get("trade_offs"):
                sel_rows.append(row("trade-off", f"{YEL}{sel_item['trade_offs']}{R}"))
            if sel_item.get("quants"):
                sel_rows.insert(0, row("quantization", quant_line(sel_item)))
            L += ["", *aligned(sel_rows, w - 4)]
        elif pk.sel == 0 and pk.note:
            L += ["", *cwrap(f"{DIM}{pk.note}{R}", w - 6)]
        L += ["", buttons("", [("Choose (Enter)", make(Verb.PICKOK)), ("Cancel (Esc)", make(Verb.PICKNO))])]
        if pk.foot:
            L += ["", *[x for part in pk.foot.split("\n") for x in cwrap(part, w - 4)]]
        if out is not None:
            out.keys = [("↑↓", "select"), *([("← →", "quantization")] if qc else []), ("Enter", "choose"),
                        ("Esc", "cancel")] + ([("s f", "sort / show")] if pk.on_pick in ("pickmodel", "pickagents")
                                              else [])
        count = f"{n - 1} {pk.noun} + auto" if pk.on_pick == "pickmodel" else f"{n} {pk.noun}"
        return indent(draw_card("picker", pk.title, f"{DIM}{count}{R}", L, w, 1))

    @staticmethod
    def arrange_picker(kind: str, sort: int, filt: int, reopen: Optional[str] = None) -> Picker:
        """A drop-down of every sort (or filter) option, the current one selected."""
        opts, cur = (SORTS, sort) if kind == "sort" else (FILTERS, filt)
        items: List[PickItem] = [(str(i), f"{i + 1:>2}. {o}") for i, o in enumerate(opts)]
        return Picker(title="SORT THE MODELS BY" if kind == "sort" else "SHOW THESE MODELS", items=items,
                      on_pick="picksort" if kind == "sort" else "pickfilter", sel=cur % len(opts),
                      noun="choices", header=f"{DIM}  Quality is CARL's quality rank (1 = best).{R}" if kind == "sort"
                      else f"{DIM}  Fits {computer()}: at least 48K tokens fit with 1 slot.{R}",
                      foot="", reopen=reopen)

    def model_picker(self, cur: str, sort: int = 0, filt: int = 0, p: Optional[Pending] = None,
                     quants: Optional[Mapping[str, str]] = None) -> Picker:
        """A drop-down of the models ("auto" on top) in the chosen order and filter; s / f change them. A model with
        several quantizations is one item (← → change the quantization shown; quants: the ones chosen so far).
        p: the pending settings (Auto fit's goal and candidates)."""
        ms = self.lines.visible(sort, filt, self.pinned(quants, cur))
        q: Pending = dict(p or {}, model="auto")
        af = self.svc.auto_fit(q)
        start = self.svc.resolved_model(q)
        pick = af.name if af else None
        items: List[PickItem] = [("auto", f"{'auto':<24} {DIM}(now {start}){R}")]
        if af and af.pick and af.plan:
            note = f"Auto is Auto fit's choice for {computer()}: {af.pick.name} with {plan_words(af.plan.slots, af.plan.ctx)}."
        else:
            note = f"Auto is Auto fit's choice. Auto fit cannot choose now: {self.svc.models.fit_error or 'no model fits'}."
        if pick and pick != start:
            note += f" {pick} is not downloaded. Until then, auto starts {start}."
        items += [(m["name"], m) for m in ms]
        so, fi = arrange_label(sort, filt)
        return Picker(title="CHOOSE A MODEL", items=items, on_pick="pickmodel",
                      sel=next((i for i, it in enumerate(items) if it[0] == cur), 0),
                      noun="models", header=None, mark=pick, note=note,
                      foot=f"Sort: {CYN}{so}{R} (s)   Show: {CYN}{fi}{R} (f)\n" + picker_foot())

    def pinned(self, quants: Optional[Mapping[str, str]], cur: str) -> Dict[str, str]:
        """quants, and the quantization cur names shown for its model."""
        group = group_of(self.svc.models.get(), cur) if cur else None
        return {**({group.name: cur} if group is not None and group.several else {}), **(quants or {})}

    def agents_picker(self, cur: str, sort: int = 0, filt: int = 0,
                      quants: Optional[Mapping[str, str]] = None) -> Picker:
        """The Agents panel's drop-down: the models in the chosen order and filter, the shown one selected."""
        items: List[PickItem] = [(m["name"], m) for m in self.lines.visible(sort, filt, self.pinned(quants, cur))]
        so, fi = arrange_label(sort, filt)
        return Picker(title="CHOOSE A MODEL", items=items, on_pick="pickagents",
                      sel=next((i for i, it in enumerate(items) if it[0] == cur), 0), noun="models", header=None,
                      foot=f"Sort: {CYN}{so}{R} (s)   Show: {CYN}{fi}{R} (f)\n" + picker_foot())

    @staticmethod
    def confirm(c: Confirm, cols: int, ui: Optional[UIState] = None, out: Optional[Shown] = None) -> List[Row]:
        """A yes / no question: the verb on the button."""
        w = min(cols - 4, 96)
        body = [x for line in aligned(list(c.lines), w - 4) for x in
                ([line] if line.startswith((" ", DIM)) else            # aligned rows come wrapped already
                 cwrap(line, w - 4) if ANSI.search(line) or " " in line else wrap(line, w - 4))]
        answers = [(f"{c.yes_label} (y)", c.yes)] + ([(f"{c.alt_label} (k)", c.alt)] if c.alt else [])
        L: List[CardLine] = ["", *body, "", buttons("  ", answers), buttons("  ", [("Cancel (n)", make(Verb.C2NO))])]
        if out is not None:
            out.keys = [("y", c.yes_label.lower())] + ([("k", c.alt_label.lower())] if c.alt else []) + [
                ("n Esc", "cancel")]
        return [("", [])] * 2 + indent(draw_card("confirm2", c.title, "", L, w, 1), (cols - w) // 2)   # centred
