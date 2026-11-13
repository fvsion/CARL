"""The Settings tab's actions and keys: the drop-downs and questions, and each panel's (Server, Models, Agents, Auto
fit, Auto-tune, Router, Caching, and a plugin's). One module for each panel, over the shared Context; this class sends
an action to the panel that owns its verb (act.OWNERS) and a key to the panel shown. Keys come one at a time
(controller.Controller.key); a text being typed takes a whole read (keys()). The card edit form: actions/card.py.

The actions change config.json only through the settings service, take the Caching panel's choices from the
settings schema, and reach the computer through the jobs (the disk cache, the processes) and the collector's watch
(the port's listener): no adapter here (rule R7)."""
from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from ....application.server_jobs import ServerJobs
from ....application.settings_service import AgentsInfo, Pending, SettingsService
from ....domain.server.snapshot import ModelInfo, ServerData
from ....ports import PluginHost, ServerEndpoint
from ....ports.plugin import PanelView
from ...act import Action, Owner
from ...keys import BACKSPACE, ENTER, ESC
from ...render.settings import SettingsView
from ...state import (SP_AGENTS, SP_CACHE, SP_FIT, SP_MODELS, SP_ROUTER, SP_SERVER, SP_TUNE, SUBPANELS, UIState)
from .agents import AgentsActions
from .caching import CacheActions
from .context import Context
from .dialogs import DialogActions
from .autofit import FitActions
from .models import ModelsActions
from .plugin import PluginActions
from .router import RouterActions
from .server import ServerActions
from .tune import TuneActions


class SettingsActions:
    """Acts on the Settings tab. snapshot() is the latest data the app collected; run(action) runs
    an action the way a click does (a question's Yes)."""

    def __init__(self, ui: UIState, svc: SettingsService, jobs: ServerJobs, view: SettingsView,
                 endpoint: ServerEndpoint, home: str, snapshot: Callable[[], ServerData], run: Callable[[str], None],
                 scroll: Callable[[int], None], plugins: Callable[[], Optional[PluginHost]] = lambda: None) -> None:
        self.ui = ui
        self.ctx = ctx = Context(ui, svc, jobs, view, endpoint, home, snapshot, run, scroll, plugins)
        ctx.act = self.action
        self.dialogs = DialogActions(ctx)
        self.models = ModelsActions(ctx)
        self.fit = FitActions(ctx)
        self.tune = TuneActions(ctx)
        self.server = ServerActions(ctx)
        self.agents = AgentsActions(ctx)
        self.router = RouterActions(ctx)
        self.cache = CacheActions(ctx)
        self.plugin = PluginActions(ctx)
        self.by_owner = {Owner.DIALOG: self.dialogs, Owner.MODELS: self.models, Owner.FIT: self.fit,
                         Owner.TUNE: self.tune, Owner.SERVER: self.server, Owner.AGENTS: self.agents,
                         Owner.ROUTER: self.router, Owner.CACHE: self.cache, Owner.PLUGIN: self.plugin}
        self.by_panel = {SP_SERVER: self.server, SP_MODELS: self.models, SP_AGENTS: self.agents, SP_FIT: self.fit,
                         SP_TUNE: self.tune, SP_ROUTER: self.router, SP_CACHE: self.cache}

    def windows(self) -> Tuple[str, ...]:
        return self.cache.windows()

    def pending_init(self, d: ServerData) -> Pending:
        """The Server panel's starting values; a config.json that can't be read is shown and skipped."""
        return self.ctx.pending_init(d)

    def visible(self) -> List[ModelInfo]:
        """The Models panel's list: sorted and filtered as the user chose."""
        return self.ctx.visible()

    def set_arrangement(self, kind: str, index: int) -> None:
        """Set the model lists' sort or filter (index wraps), keeping the selected model when it is still listed."""
        self.ctx.set_arrangement(kind, index)

    def agents_info(self) -> Optional[AgentsInfo]:
        """The shown model's thinking per role (None: no model is known)."""
        return self.agents.info()

    def plugin_panels(self) -> List[PanelView]:
        """The plugins' configuration panels, in the order after Caching."""
        return self.ctx.plugin_panels()

    # ------------------------------------------------------------ actions
    def action(self, a: Action) -> None:
        """An action of the Settings panels, the drop-downs or the questions: the panel that owns its verb runs it."""
        panel = self.by_owner.get(a.owner)
        if panel is not None:
            panel.act(a)

    # ------------------------------------------------------------ keys
    def keys(self, rest: str) -> bool:
        """A whole read while a text is typed (a Hugging Face repo, a card field): a paste stays one
        text. True when the input was used here."""
        ui = self.ui
        if ui.text:
            tx = ui.text
            for ch in rest:
                if ch in ENTER:
                    v = tx.value.strip()
                    ui.text = None
                    if v and tx.on_enter == "hf":
                        self.ctx.jobs.hf_lookup(v)
                    elif v and tx.on_enter == "hftoken":
                        self.models.set_token(v)
                        tx.value = ""
                    elif v and tx.on_enter.startswith("plugin:"):
                        self.plugin.typed(tx.on_enter, v)
                    return True
                if ch == ESC:
                    ui.text = None
                    return True
                if ch in BACKSPACE:
                    tx.value = tx.value[:-1]
                elif ch.isprintable():
                    tx.value += ch
            return True
        if ui.card and ui.sp == SP_MODELS:
            return self.ctx.card.keys(rest)
        return False

    def key(self, k: str) -> bool:
        """One key in the Settings tab. True when it was used here (else the keys of every screen get it)."""
        ui = self.ui
        if ui.text or (ui.card is not None and ui.card.typing is not None and ui.sp == SP_MODELS):
            return self.keys(k)
        if ui.picker:
            return self.dialogs.picker_key(k)
        if ui.confirm2:
            return self.dialogs.confirm_key(k)
        if ui.card and ui.sp == SP_MODELS:
            return self.ctx.card.keys(k)
        if ui.edit is not None and ui.sp == SP_SERVER:
            return self.server.edit_key(k)
        if k in ("[", "]"):
            ui.sp = (ui.sp + (1 if k == "]" else -1)) % self.ctx.panel_count()
            return True
        if ui.sp >= len(SUBPANELS):
            return self.plugin.key(k)
        return self.by_panel[ui.sp].key(k)
