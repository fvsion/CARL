"""The Auto-tune panel: the model, the length, a run, its cancel, and the recommended settings."""
from __future__ import annotations

from typing import List

from ....domain.tune.scoring import DEPTHS, as_depth
from ....ui.fmt import R, RED
from ...act import Action, Verb, act
from ...keys import DOWN, LEFTKEY, PGDN, PGUP, RIGHT, UP
from ...state import TUNE_ALL, PickItem, Picker
from .context import TUNE_ALL_LABEL, Context

TUNE_KEYS = {"\r": act(Verb.TRUN), "\n": act(Verb.TRUN), RIGHT: act(Verb.TNEXT), LEFTKEY: act(Verb.TPREV),
             "c": act(Verb.TCANCEL), " ": act(Verb.TQUICK), "x": act(Verb.TCLEAR)}


class TuneActions:
    """Acts on the Auto-tune panel."""

    def __init__(self, c: Context) -> None:
        self.c = c

    def act(self, a: Action) -> None:
        """The model (tprev / tnext / tpick), the length (tquick, tdepth:DEPTH), a run (trun, tyes after the
        question, tcancel) and tclear (the recommended settings apply again)."""
        c = self.c
        ui, verb = c.ui, a.verb
        if verb in (Verb.TPREV, Verb.TNEXT):
            if ui.tune and not ui.tune.done:
                ui.toast(f"Auto-tune is running for {ui.tune.model}. To change the model, cancel the run first (c).", 6)
                return
            names = [x["name"] for x in c.models.downloaded()] + [TUNE_ALL]
            if len(names) > 1:
                i = names.index(ui.tune_model) if ui.tune_model in names else 0
                ui.tune_model = names[(i + (1 if verb is Verb.TNEXT else -1)) % len(names)]
        elif verb is Verb.TPICK:
            items: List[PickItem] = [(x["name"], x) for x in c.models.downloaded()]
            ui.picker = Picker("AUTO-TUNE WHICH MODEL?", items + [(TUNE_ALL_LABEL, TUNE_ALL_LABEL)], "picktune")
        elif verb is Verb.TQUICK:                       # quick -> normal -> long -> quick
            ui.tune_depth = DEPTHS[(DEPTHS.index(as_depth(ui.tune_depth)) + 1) % len(DEPTHS)]
        elif verb is Verb.TDEPTH:
            ui.tune_depth = as_depth(a.arg)
        elif verb is Verb.TRUN:
            c.jobs.run_tune(c.snapshot(), ui.tune_model or "", ui.tune_depth)
        elif verb is Verb.TYES:
            ui.confirm2 = None
            c.jobs.run_tune(c.snapshot(), ui.tune_model or "", ui.tune_depth, confirmed=True)
        elif verb is Verb.TCANCEL and ui.tune:
            c.jobs.cancel_tune()
        elif verb is Verb.TCLEAR:
            try:
                c.svc.clear_model_settings(str(ui.tune_model))
            except Exception as e:      # config.json unreadable or not writable: say so
                ui.toast(f"{RED}CARL cannot save config.json: {e}{R}", 10)
                return
            ui.pending = None
            ui.toast(f"{ui.tune_model}: CARL uses the recommended settings again. Your changes for this model in "
                     f"config.json are removed.", 8)

    def key(self, k: str) -> bool:
        """The Auto-tune panel: ← → the model, space the length, Enter run, c cancel, x recommended; ↑↓ PgUp
        PgDn scroll the page."""
        ui = self.c.ui
        if k in (UP, DOWN, PGUP, PGDN):
            ui.page_scroll = max(0, ui.page_scroll + {UP: -3, DOWN: 3, PGUP: -10, PGDN: 10}[k])
            return True
        if k in TUNE_KEYS:
            self.c.act(TUNE_KEYS[k])
            return True
        return False
