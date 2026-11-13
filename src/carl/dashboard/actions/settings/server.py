"""The Server panel: a row, a value typed or stepped, the model drop-down, Undo, the recommended settings, Auto fit,
Apply and its question."""
from __future__ import annotations

import os

from ....application.settings_service import NUMERIC, Pending, parse_typed, spec_value, step_choice
from ....domain.server.ub_order import KEEP, USE_TUNED
from ....ui.words import command, computer
from ...act import Action, Verb, act
from ...keys import BACKSPACE, DOWN, ENTER, ESC, LEFTKEY, PGDN, PGUP, RIGHT, UP
from ...state import SP_FIT, Confirm
from .context import ARRANGE_KEYS, Context

SERVER_KEYS = {"a": act(Verb.SETAPPLY), "r": act(Verb.SETREVERT), "x": act(Verb.SETDEFAULTS), "A": act(Verb.SETAUTOFIT)}


class ServerActions:
    """Acts on the Server panel."""

    def __init__(self, c: Context) -> None:
        self.c = c

    def not_ready(self, p: Pending) -> str:
        """Why Apply cannot start this setup ('' when it can)."""
        f = self.c.svc.fit_cached(p)
        if not f.known:
            return f"{f.name} is not a known model. Choose another model."
        if not f.downloaded:
            return f"{f.name} is not downloaded. To download it: the Models panel (]), then d."
        if f.error:
            return f"CARL cannot check the memory: {f.error}"
        if not f.fits:
            return f"This setup does not fit {computer()} (see Memory). Use a smaller context or fewer slots."
        return self.no_build(p)

    def no_build(self, p: Pending) -> str:
        """A GPU backend with no installed build of llama.cpp (A46: refused when applied, not rolled back after)."""
        want = str(p.get("backend") or "auto")
        if want == "auto" or "backend" not in p:
            return ""
        have = self.c.jobs.llama_backends()
        if have is None or any(b.split("-")[0] == want for b in have):
            return ""
        return (f"No llama.cpp build for the backend {want} is installed. {command()} engine install --backend "
                f"{want} installs one, or choose auto.")

    def act(self, a: Action) -> None:
        """A row (setrow:N), a value (setinc:N / setdec:N), the model drop-down (setpick), Undo, the recommended
        settings, Auto fit, Apply and its question."""
        c = self.c
        ui, d, svc, verb = c.ui, c.snapshot(), c.svc, a.verb
        p = ui.pending
        if p is None:
            return
        rws = svc.rows(p)
        if verb is Verb.SETPICK:
            ui.set_row = 0
            c.open_model_picker()
        elif verb is Verb.SETROW:
            ui.set_row = min(int(a.arg), len(rws) - 1)
        elif verb in (Verb.SETINC, Verb.SETDEC):
            i = min(int(a.arg), len(rws) - 1)
            ui.set_row = i
            row = rws[i]
            step = 1 if verb is Verb.SETINC else -1
            if row.key == "spec" and row.choices:
                p["spec"], p["specn"] = str(step_choice(row.choices, spec_value(p), step)).split("|")
            elif row.choices:
                p[row.key] = step_choice(row.choices, p[row.key], step)
            if row.key == "model":
                svc.load_profile(p, str(p["model"]))
        elif verb is Verb.SETREVERT:
            ui.pending = None
        elif verb is Verb.SETDEFAULTS:
            ui.pending = svc.defaults_for(p)
        elif verb is Verb.SETAUTOFIT:
            ui.sp = SP_FIT
        elif verb is Verb.SETNOFIT:
            ui.toast(self.not_ready(p) or "This setup cannot start.", 8, error=True)
        elif verb in (Verb.SETUBTUNED, Verb.SETUBKEEP):
            self.ub_answer(p, KEEP if verb is Verb.SETUBKEEP else USE_TUNED)
        elif verb is Verb.SETAPPLY and not ui.restart:
            why = self.not_ready(p)
            if why:
                ui.toast(why, 8)
                return
            if self.ub_question(p):
                return
            ui.confirm_port, ui.confirm_taken = None, ""
            if d.cmd and "llama-server" not in d.cmd:      # another program has the port: offer the next free one
                sockets = c.jobs.collector.watch.sockets()
                free = sockets.next_free_port(c.jobs.collector.endpoint.port)
                if free is None:
                    ui.toast("Another program uses this port, and no port after it is free. Stop it first.", 8)
                    return
                pid = sockets.listen_pid(c.jobs.collector.endpoint.port)
                name = os.path.basename(d.cmd.split()[0]) if d.cmd.split() else "a program"
                ui.confirm_port, ui.confirm_taken = free, f"{name}, pid {pid}" if pid else name
            ui.confirm = True
        elif verb is Verb.SETNO:
            ui.confirm, ui.confirm_port = False, None
        elif verb is Verb.SETYES:
            ui.confirm = False
            c.jobs.restart(p, d, port=ui.confirm_port, ran=ui.set_run)
            ui.confirm_port = None

    def ub_question(self, p: Pending) -> bool:
        """A Batch size that differs from the model's tuned -ub, or a config -ub the tune outdates, is asked about
        once before Apply goes on (A51): Use tuned, or keep it for this model. True when the question is shown."""
        c = self.c
        ui = c.ui
        ask = c.svc.ub_question(p)
        if ask is None:
            return False
        name = c.svc.resolved_model(p)
        use, keep = ask.buttons()
        ui.ub_ask = ask
        ui.confirm2 = Confirm("BATCH SIZE (-ub)", [f"{ask.sets()}.", f"{ask.evidence(name)}."], act(Verb.SETUBTUNED),
                              yes_label=use, alt=act(Verb.SETUBKEEP), alt_label=keep)
        return True

    def ub_answer(self, p: Pending, choice: str) -> None:
        """The answer to the question: saved, then Apply goes on as if it had just been pressed."""
        c = self.c
        ui = c.ui
        ask, ui.confirm2, ui.ub_ask = ui.ub_ask, None, None
        if ask is None:
            return
        c.svc.answer_ub(c.svc.resolved_model(p), p, ask, choice)
        self.act(act(Verb.SETAPPLY))

    def selected_key(self) -> str:
        """The key of the selected Server-panel row."""
        rws = self.c.svc.rows(self.c.ui.pending or {})
        return rws[min(self.c.ui.set_row, len(rws) - 1)].key

    def commit_edit(self, key: str) -> None:
        """Settings: store a typed value (Enter) if it is a valid number for that row."""
        ui = self.c.ui
        value, err = parse_typed(key, ui.edit or "")
        ui.edit = None
        if value is None:
            ui.toast(err, 5)
        elif ui.pending is not None:
            ui.pending[key] = value

    def edit_key(self, k: str) -> bool:
        """A number being typed into the selected Server row: digits . k, Backspace, Enter keeps it, Esc
        drops it."""
        ui = self.c.ui
        if ui.edit is None:
            return False
        if k in "0123456789.kK" and len(k) == 1:
            ui.edit += k.lower()
        elif k in BACKSPACE and len(k) == 1:
            ui.edit = ui.edit[:-1]
        elif k in ENTER:
            self.commit_edit(self.selected_key())
        elif k == ESC:
            ui.edit = None
        return True

    def key(self, k: str) -> bool:
        """The Server panel: ↑↓ a row, ← → its value, Enter (the model list, or type a number), a apply,
        r undo, x the recommended settings, A Auto fit, m the model list beside it, s f its sort and filter,
        PgUp PgDn scroll."""
        c = self.c
        ui = c.ui
        p = ui.pending
        if p is None:
            return False
        items = ["auto"] + [m["name"] for m in c.visible()]
        if ui.slist:                                # the model list beside the settings has the keys
            if k == UP:
                ui.srow = max(ui.srow - 1, 0)
            elif k == DOWN:
                ui.srow = min(ui.srow + 1, len(items) - 1)
            elif k in (RIGHT, LEFTKEY) and ui.srow > 0:         # another quantization of the model (A68)
                rows = c.visible()
                at = rows[ui.srow - 1] if ui.srow <= len(rows) else None
                if at is not None and c.cycle_quant(at, -1 if k == LEFTKEY else 1):
                    ui.srow = 1 + next((i for i, x in enumerate(c.visible()) if x.get("group") == at.get("group")),
                                       ui.srow - 1)
            elif k in ENTER and items:
                c.choose_model(items[min(ui.srow, len(items) - 1)])
                ui.slist = False
            elif k in ("m", ESC):
                ui.slist = False
            elif k in ARRANGE_KEYS:
                c.act(ARRANGE_KEYS[k])
            else:
                return False
            return True
        n = len(c.svc.rows(p))
        if k in (UP, DOWN):
            ui.set_row = (ui.set_row + (1 if k == DOWN else -1)) % n
            ui.set_scroll = 0                       # the rows are at the top: keep the selected one in view
        elif k in (RIGHT, LEFTKEY):
            c.act(act(Verb.SETINC if k == RIGHT else Verb.SETDEC, ui.set_row))
        elif k in (PGUP, PGDN):
            ui.set_scroll = max(0, ui.set_scroll + (-10 if k == PGUP else 10))
        elif k in ENTER:
            key = self.selected_key()
            if key == "model":
                c.open_model_picker()
            elif key in NUMERIC:
                ui.edit = ""
        elif k in SERVER_KEYS:
            c.act(SERVER_KEYS[k])
        elif k == "m":
            ui.slist = True
        elif k in ARRANGE_KEYS:
            c.act(ARRANGE_KEYS[k])
        else:
            return False
        return True
