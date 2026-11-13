"""The Settings tab's drop-downs and questions: the panel bar, the picker (a list to choose from), a yes / no question
and the card edit form's buttons."""
from __future__ import annotations

from ...act import Action, Verb
from ...keys import DOWN, ENTER, ESC, LEFTKEY, PGDN, PGUP, RIGHT, UP
from ...state import SP_AGENTS, TUNE_ALL, Picker
from .context import ARRANGE_KEYS, TUNE_ALL_LABEL, Context


class DialogActions:
    """Acts on the open drop-down or question, and on the panel bar."""

    def __init__(self, c: Context) -> None:
        self.c = c

    def act(self, a: Action) -> None:
        """The drop-down (pick:N selects, pickok / pickno), a question's Cancel (c2no), the panel bar (sp:N) and the
        card edit form (card...)."""
        ui, verb = self.c.ui, a.verb
        if verb is Verb.PICK:
            if ui.picker:
                ui.picker.sel = int(a.arg)
        elif verb is Verb.PICKOK:
            self.picker_choose()
        elif verb is Verb.PICKNO:
            ui.picker = None
        elif verb is Verb.C2NO:
            ui.confirm2 = None
        elif verb is Verb.SP:
            ui.sp = int(a.arg)
        elif ui.card:
            self.c.card.button(a, ui.card)

    def picker_choose(self) -> None:
        """Use the picker's selection: a model for the Server panel, a file to download, a model to tune."""
        c = self.c
        ui = c.ui
        pk, ui.picker = ui.picker, None
        if pk is None:
            return
        val = pk.items[pk.sel][0]
        if pk.on_pick == "pickmodel":
            c.choose_model(str(val))
        elif pk.on_pick == "pickhf" and ui.hf:
            c.jobs.start_download(f"hf:{ui.hf.repo}/{val}")
        elif pk.on_pick == "pickagents":
            ui.agents_model, ui.agents_row = str(val), 0
        elif pk.on_pick == "picktune":
            ui.tune_model = TUNE_ALL if val == TUNE_ALL_LABEL else val
        elif pk.on_pick == "pickcard" and ui.card:
            ui.card.add_pick(str(val))
        elif pk.on_pick in ("picksort", "pickfilter"):
            c.set_arrangement("sort" if pk.on_pick == "picksort" else "filter", int(str(val)))
            if pk.reopen and ui.sp == SP_AGENTS:       # back to the drop-down it came from
                ui.picker = c.view.agents_picker(pk.reopen, ui.msort, ui.mfilter, ui.mquants)
            elif pk.reopen:
                ui.picker = c.view.model_picker(pk.reopen, ui.msort, ui.mfilter, ui.pending, ui.mquants)

    def rebuilt(self, on_pick: str, cur: str) -> Picker:
        """The model drop-down again (the sort, the filter or a quantization changed), on the model cur."""
        c, ui = self.c, self.c.ui
        return (c.view.model_picker(cur, ui.msort, ui.mfilter, ui.pending, ui.mquants) if on_pick == "pickmodel"
                else c.view.agents_picker(cur, ui.msort, ui.mfilter, ui.mquants))

    def picker_key(self, k: str) -> bool:
        c = self.c
        ui = c.ui
        pk = ui.picker
        if pk is None:
            return False
        if k == UP:
            pk.sel = max(pk.sel - 1, 0)
        elif k == DOWN:
            pk.sel = min(pk.sel + 1, len(pk.items) - 1)
        elif k == PGUP:
            pk.sel = max(pk.sel - 10, 0)
        elif k == PGDN:
            pk.sel = min(pk.sel + 10, len(pk.items) - 1)
        elif k in ARRANGE_KEYS and pk.on_pick in ("pickmodel", "pickagents"):
            cur = str(pk.items[pk.sel][0])
            c.act(ARRANGE_KEYS[k])
            ui.picker = self.rebuilt(pk.on_pick, cur)
        elif k in (RIGHT, LEFTKEY) and pk.on_pick in ("pickmodel", "pickagents"):     # another quantization (A68)
            item = pk.items[pk.sel][1]
            new = c.cycle_quant(item, -1 if k == LEFTKEY else 1) if isinstance(item, dict) else None
            if new:
                ui.picker = self.rebuilt(pk.on_pick, new)
        elif k in ENTER:
            self.picker_choose()
        elif k == ESC:
            ui.picker = None
        return True

    def confirm_key(self, k: str) -> bool:
        """A question of the Settings tab: y / Enter yes, n / Esc no."""
        ui = self.c.ui
        if ui.confirm2 is None:
            return False
        if k in ("y", "Y") or k in ENTER:
            self.c.run(ui.confirm2.yes)
        elif k in ("k", "K") and ui.confirm2.alt:
            self.c.run(ui.confirm2.alt)
        elif k in ("n", "N", ESC):
            ui.confirm2 = None
        return True

