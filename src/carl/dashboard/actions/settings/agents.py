"""The Agents panel: the thinking of the main session and of the coder, per model."""
from __future__ import annotations

from typing import Dict, Optional

from ....application.settings_service import (CODER_OFF_NOTE, THINKING_LABELS, THINKING_ROWS, AgentsInfo, coder_off,
                                              step_choice)
from ....ui.fmt import R, RED
from ...act import Action, AgentsOp, agents
from ...settings_texts import shown_value
from ...keys import ENTER, LEFTKEY, PGDN, PGUP, RIGHT, UP, DOWN
from ...render.settings.agents import AGENT_ROWS
from .context import Context

AGENTS_KEYS = {"r": agents(AgentsOp.UNDO), "x": agents(AgentsOp.REC)}


class AgentsActions:
    """Acts on the Agents panel."""

    def __init__(self, c: Context) -> None:
        self.c = c

    def model(self) -> str:
        """The model the Agents panel shows: the one chosen there, else the running model, else the Server panel's
        ("" when no model is known)."""
        c = self.c
        ui, svc = c.ui, c.svc
        if ui.agents_model and svc.models.by_name(ui.agents_model):
            return ui.agents_model
        run = svc.running(c.snapshot()).get("model")
        if isinstance(run, str) and svc.models.by_name(run):
            return run
        name = svc.resolved_model(ui.pending or {"model": "auto"})
        return name if svc.models.by_name(name) else ""

    def info(self) -> Optional[AgentsInfo]:
        """The shown model's thinking per role (None: no model is known)."""
        name = self.model()
        return self.c.svc.agents(name) if name else None

    def act(self, a: Action) -> None:
        """row:N selects a row, inc:N / dec:N change it (the model: the next one; a thinking row: saved at once), pick
        opens the model drop-down, undo puts back the values from before this session's changes, rec uses the
        recommended values."""
        c = self.c
        ui = c.ui
        verb, _, n = a.arg.partition(":")
        if verb == AgentsOp.ROW:
            ui.agents_row = max(0, min(int(n), len(AGENT_ROWS) - 1))
        elif verb == AgentsOp.PICK:
            ui.agents_row = 0
            ui.picker = c.view.agents_picker(self.model(), ui.msort, ui.mfilter)
        elif verb in (AgentsOp.INC, AgentsOp.DEC):
            ui.agents_row = max(0, min(int(n), len(AGENT_ROWS) - 1))
            step = 1 if verb == AgentsOp.INC else -1
            key = AGENT_ROWS[ui.agents_row]
            if key == "model":
                names = [m["name"] for m in c.visible()]
                cur = self.model()
                if names:
                    ui.agents_model = names[(names.index(cur) + step) % len(names)] if cur in names else names[0]
                return
            info = self.info()
            if info is not None:
                self.save(info, {key: str(step_choice(list(info.choices[key]), info.mine[key], step))})
        elif verb in (AgentsOp.UNDO, AgentsOp.REC):
            info = self.info()
            if info is None:
                return
            if verb == AgentsOp.UNDO:
                before = ui.agents_undo.get(info.name)
                if not before:
                    ui.toast(f"There is nothing to undo for {info.name}.", 5)
                    return
                self.save(info, before, undo=True)
            else:
                self.save(info, {k: None for k in THINKING_ROWS}, what="the recommended settings")

    def save(self, info: AgentsInfo, want: Dict[str, Optional[str]], undo: bool = False, what: str = "") -> None:
        """Save Main thinking and Coder thinking of the shown model (None: the recommended value) and say so; the
        values before the first change of this session are kept for r (undo)."""
        c = self.c
        ui = c.ui
        try:
            saved = c.svc.thinking_saved(info.name)
            for key, value in want.items():
                c.svc.save_thinking(info.name, key, value)
        except Exception as e:      # config.json unreadable or not writable, a value the model does not take: say so
            ui.toast(f"{RED}CARL cannot save config.json: {e}{R}", 10)
            return
        if undo:
            ui.agents_undo.pop(info.name, None)
        else:
            ui.agents_undo.setdefault(info.name, saved)
        now = c.svc.agents(info.name)
        if what or undo:
            said = f"{info.name} uses {'its earlier settings' if undo else what} again"
        else:
            key = next(iter(want))
            said = f"{THINKING_LABELS[key]} of {info.name}: {shown_value(key, now.mine[key])}"
        if coder_off(now.mine):
            # the message line has two lines at most: the note alone after "Saved.", so its full-spec sentence is
            # never cut (the panel shows the model and the values; its text above says when OpenCode and Pi use it)
            ui.toast(f"Saved. {CODER_OFF_NOTE}", 12)
            return
        ui.toast(f"Saved. {said}. OpenCode and Pi use it after the next update (the Connect tab, u; other "
                 f"computers: P).", 12)

    def key(self, k: str) -> bool:
        """The Agents panel: ↑↓ a row, ← → its value (saved at once; the model row: the next model), Enter the model
        drop-down, r undo, x recommended, PgUp PgDn scroll the page."""
        c = self.c
        ui = c.ui
        if k in (UP, DOWN):
            ui.agents_row = (ui.agents_row + (1 if k == DOWN else -1)) % len(AGENT_ROWS)
            ui.page_scroll = 0
        elif k in (RIGHT, LEFTKEY):
            c.act(agents(AgentsOp.INC if k == RIGHT else AgentsOp.DEC, ui.agents_row))
        elif k in ENTER and ui.agents_row == 0:
            c.act(agents(AgentsOp.PICK))
        elif k in AGENTS_KEYS:
            c.act(AGENTS_KEYS[k])
        elif k in (PGUP, PGDN):
            ui.page_scroll = max(0, ui.page_scroll + (-10 if k == PGUP else 10))
        else:
            return False
        return True
