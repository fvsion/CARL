"""The Router panel: single model or router mode, and Load / Unload of a router's model."""
from __future__ import annotations

from ....ui.fmt import R, RED, row
from ....ui.words import command, computer
from ...act import Action, Verb, act
from ...keys import DOWN, ENTER, PGDN, PGUP, UP
from ...state import Confirm
from .context import Context

MODES = ("single", "router")


class RouterActions:
    """Acts on the Router panel."""

    def __init__(self, c: Context) -> None:
        self.c = c

    def act(self, a: Action) -> None:
        """rmode:MODE asks to switch between single model and router mode, rmodeyes saves it (llama.mode) and
        restarts a running server in it; rload:ID / runload:ID load or unload a router's model (in the background: a
        load takes 30 s to 2 min)."""
        c = self.c
        d, verb = c.snapshot(), a.verb
        if verb is Verb.RMODE:
            self.ask(a.arg)
        elif verb is Verb.RMODEYES:
            self.switch()
        elif verb in (Verb.RLOAD, Verb.RUNLOAD):
            c.jobs.router_load(a.arg, d, unload=verb is Verb.RUNLOAD)

    def ask(self, mode: str) -> None:
        """The question before a switch of mode."""
        c = self.c
        ui, d = c.ui, c.snapshot()
        if mode not in MODES:
            return
        running = "router" if d.router is not None else "single" if d.up else None
        now = ("The server restarts in this mode now. Requests in progress stop, and the model loads again."
               if running and running != mode else "The next server start uses it.")
        here = c.jobs.host.installed_here(c.endpoint.base, c.home)     # as rmodeyes decides
        configs = (f"CARL updates the OpenCode and Pi configs on {computer()}." if here else
                   f"Update the OpenCode and Pi configs yourself ({command()} install --config-only): CARL did not "
                   f"set them up on {computer()}.")
        if mode == "router":
            lines = ["OpenCode and Pi can then switch the model. Each switch loads the model again: 30 s to 2 "
                     "minutes.", "",
                     "After a switch, the new model has none of the sessions in memory. A saved session belongs to "
                     "the model it ran on: when you switch back to that model, OpenCode and Pi restore it from the "
                     "disk cache (with Saved sessions on, and for a Gemma model the full cache). A session that "
                     "continues on the new model is read again: minutes for a long session.", "", configs, now]
            reg = c.jobs.registry
            if reg is not None and reg.list():                   # computers that sync their configs (#33)
                lines.append("The client configs on other computers must list every model (the Connect tab, P).")
            title = "SWITCH TO ROUTER MODE?"
        else:
            lines = ["One model runs. You choose it in Settings > Server.", "", configs, now]
            title = "SWITCH TO A SINGLE MODEL?"
        if ui.full:
            lines += ["", row("config.json", "llama.mode = router" if mode == "router" else
                              "llama.mode is removed (single is the default)")]
        ui.confirm2 = Confirm(title, lines, act(Verb.RMODEYES), mode, yes_label="Switch")

    def switch(self) -> None:
        """The question's Yes: save the mode and restart a running server in it."""
        c = self.c
        ui, d = c.ui, c.snapshot()
        q, ui.confirm2 = ui.confirm2, None
        target = q.model if q else None
        if target not in MODES:
            return
        mode = str(target)
        try:
            c.svc.set_mode(mode)
        except Exception as e:      # config.json unreadable or not writable: say so
            ui.toast(f"{RED}CARL cannot save config.json: {e}{R}", 10)
            return
        running = "router" if d.router is not None else "single" if d.up else None
        # the clients on this Mac follow: their configs are updated (after the restart: the
        # installer reads the new server), when CARL set them up here
        update = c.jobs.host.installed_here(c.endpoint.base, c.home)
        if running and running != mode and not ui.restart:
            if ui.pending is None:
                ui.pending = c.pending_init(d)
            ui.install_after_restart = update
            c.jobs.restart(ui.pending, d, ran=ui.set_run, then_install=update)
        else:
            name = "router mode" if mode == "router" else "a single model"
            ui.toast(f"Saved: {name}." + ("" if running == mode else " The next start uses it."), 8)
            if update:
                c.jobs.start_install(config_only=True)

    def key(self, k: str) -> bool:
        """The Router panel: s single model, r router mode (both ask first), ↑↓ a router model, Enter loads
        or unloads it, u updates the OpenCode and Pi configs."""
        c = self.c
        ui, d = c.ui, c.snapshot()
        models = d.router.models if d.router is not None else []
        if k in ("s", "r"):
            c.act(act(Verb.RMODE, "single" if k == "s" else "router"))
        elif k in (UP, DOWN) and models:
            ui.router_row = max(min(ui.router_row + (1 if k == DOWN else -1), len(models) - 1), 0)
        elif k in ENTER and models:
            m = models[min(ui.router_row, len(models) - 1)]
            c.act(act(Verb.RUNLOAD if m.active else Verb.RLOAD, m.id))
        elif k == "u":
            c.run(act(Verb.INSCONFIG))
        elif k in (PGUP, PGDN):
            ui.page_scroll = max(0, ui.page_scroll + (-10 if k == PGUP else 10))
        else:
            return False
        return True
