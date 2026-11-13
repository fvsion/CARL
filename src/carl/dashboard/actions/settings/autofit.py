"""The Auto fit panel: its goal and candidates, Use this, Download it, Measure (A72)."""
from __future__ import annotations

from ....application.settings_service import Pending, plan_words
from ....domain.shared.units import file_size, memory
from ....ui.fmt import R, RED, row
from ....ui.words import computer
from ...act import Action, Verb, act
from ...keys import DOWN, PGDN, PGUP, UP
from ...state import SP_SERVER, Confirm
from .context import Context

FIT_KEYS = {"\r": act(Verb.FUSE), "\n": act(Verb.FUSE), "d": act(Verb.FDL), "g": act(Verb.FGOAL),
            "f": act(Verb.FSCOPE), "m": act(Verb.FMEASURE)}


class FitActions:
    """Acts on the Auto fit panel."""

    def __init__(self, c: Context) -> None:
        self.c = c

    def act(self, a: Action) -> None:
        """The Auto fit panel: the goal and the candidates (fgoal / fscope), Use this, Download it."""
        c = self.c
        ui, verb = c.ui, a.verb
        if verb in (Verb.FGOAL, Verb.FSCOPE):
            self.auto_choice(a)
        elif verb is Verb.FUSE:
            if ui.pending is None:
                ui.pending = c.pending_init(c.snapshot())
            self.auto_fit(ui.pending)
            if ui.confirm2 is None:                     # downloaded: the Server panel shows the plan
                ui.sp = SP_SERVER
        elif verb is Verb.FMEASURE:                     # the Server panel's model, else Auto fit's choice
            if ui.pending is None:
                ui.pending = c.pending_init(c.snapshot())
            fit = c.svc.auto_fit(ui.pending)
            name = (fit.pick.name if fit and fit.pick else "") if ui.pending.get("model", "auto") == "auto" \
                else str(ui.pending["model"])
            if name:
                c.jobs.measure_fit(name)
            else:
                ui.toast("There is no model to measure: Auto fit has no choice.", 5)
        elif verb is Verb.FDL:
            fit = c.svc.auto_fit(ui.pending or {})
            if fit and fit.pick and not fit.pick.downloaded:
                c.jobs.start_download(fit.pick.name)
            else:
                ui.toast("Auto fit's choice is downloaded already.", 5)

    def auto_choice(self, a: Action) -> None:
        """The Auto fit panel's goal / candidates: fgoal / fscope switch to the other one, fgoal:X /
        fscope:X choose X. Saved to config.json at once."""
        c = self.c
        ui = c.ui
        if ui.pending is None:
            ui.pending = c.pending_init(c.snapshot())
        p = ui.pending
        key = "goal" if a.verb is Verb.FGOAL else "scope"
        opts = ("everyday", "hard-code") if key == "goal" else ("catalogue", "downloaded")
        value = a.arg or opts[1 - opts.index(str(p.get(key, opts[0])))]
        try:
            c.svc.save_auto_choice(p, key, value)
        except Exception as e:      # config.json unreadable or not writable, a bad value: say so
            ui.toast(f"{RED}Auto fit: {e}{R}", 10)
            return
        fit = c.svc.auto_fit(p)
        now = (f"Auto fit now suggests {fit.pick.name} with {plan_words(fit.plan.slots, fit.plan.ctx)}."
               if fit and fit.pick and fit.plan else "No model fits with this choice.")
        ui.toast(f"Saved. {now}", 8)

    def auto_fit(self, p: Pending) -> None:
        """Auto fit (Enter in its panel): its choice for this Mac with its context, slots and context
        memory, in one step; a choice that isn't downloaded is offered for download."""
        c = self.c
        ui = c.ui
        fit = c.svc.apply_auto_fit(p)
        if fit is None:
            ui.toast(f"{RED}Auto fit cannot work now: {c.models.fit_error or 'no model list'}{R}", 10)
        elif fit.pick is None or fit.plan is None:
            ui.toast(f"{RED}Auto fit: no model fits {computer()}.{R}", 12)
        elif not fit.pick.downloaded:
            m = c.models.by_name(fit.pick.name)
            ui.confirm2 = Confirm("DOWNLOAD AUTO FIT'S CHOICE?", [
                row("choice", fit.pick.name), row("with", plan_words(fit.plan.slots, fit.plan.ctx)),
                row("context memory", str(getattr(fit.plan, "kv", "") or "q4").split("_")[0]),
                row("needs", f"{memory(fit.plan.need)} of {memory(fit.budget.allowed)} (the GPU limit)"),
                row("download", file_size(m['bytes']) if m else "size not known"), "",
                "The Models panel shows the progress. After the download, press a in the Server panel to start it. "
                "If you cancel, the Server panel keeps this choice; it can start only after the download."],
                act(Verb.MAUTODL), fit.pick.name, yes_label="Download")
        else:
            ui.toast(f"Auto fit's choice is set: {fit.pick.name} with {plan_words(fit.plan.slots, fit.plan.ctx)}. "
                     f"Press a to start it.", 8)

    def key(self, k: str) -> bool:
        """The Auto fit panel: ↑↓ PgUp PgDn scroll, Enter use, d download, g goal, f candidates."""
        c = self.c
        if k in (UP, DOWN, PGUP, PGDN):
            c.scroll({UP: 3, DOWN: -3, PGUP: 10, PGDN: -10}[k])
        elif k in FIT_KEYS:
            c.act(FIT_KEYS[k])
        else:
            return False
        return True
