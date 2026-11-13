"""What the Settings panels' actions share: the UI state, the services they act through, and the few steps more than
one panel takes (the model lists' sort and filter, choosing a model, the Server panel's starting values)."""
from __future__ import annotations

from typing import Callable, List, Optional

from ....application.server_jobs import ServerJobs
from ....application.settings_service import Pending, SettingsService
from ....domain.catalog.inventory import group_of
from ....domain.server.snapshot import ModelInfo, ServerData
from ....ports import PluginHost, ServerEndpoint
from ....ports.plugin import PanelView
from ....ui.fmt import R, RED
from ....ui.words import status_name
from ...act import Action, Verb, act
from ...arrange import FILTERS, SORTS
from ...render.settings import SettingsView
from ...state import SUBPANELS, UIState
from ..card import CardActions

TUNE_ALL_LABEL = "All downloaded models, one after the other"      # the Auto-tune model list's last item
ARRANGE_KEYS = {"s": act(Verb.MSORT), "S": act(Verb.MSORT_BACK), "f": act(Verb.MFILTER),
                "F": act(Verb.MFILTER_BACK)}     # the keys of every model list


class Context:
    """The shared side of the panels' actions. snapshot() is the latest data the app collected; run(action) runs an
    action the way a click does (a question's Yes); act(action) runs it in the panel that owns it (the facade sets it)."""

    def __init__(self, ui: UIState, svc: SettingsService, jobs: ServerJobs, view: SettingsView,
                 endpoint: ServerEndpoint, home: str, snapshot: Callable[[], ServerData],
                 run: Callable[[str], None], scroll: Callable[[int], None],
                 plugins: Callable[[], Optional[PluginHost]]) -> None:
        self.ui = ui
        self.svc = svc
        self.models = svc.models
        self.store = svc.store
        self.jobs = jobs
        self.view = view
        self.endpoint = endpoint
        self.home = home
        self.snapshot = snapshot
        self.run = run
        self.scroll = scroll
        self.plugins = plugins                  # the plugins' panels come after Caching (WP)
        self.card = CardActions(ui, svc, view)
        self.act: Callable[[Action], None] = lambda a: None

    def pending_init(self, d: ServerData) -> Pending:
        """The Server panel's starting values; a config.json that can't be read is shown and skipped."""
        try:
            cfg = self.store.load_config()
        except Exception as e:      # carl.ConfigError or a broken file: say so, start from the defaults
            self.ui.toast(f"{RED}CARL cannot read config.json: {e}{R}", 15)
            cfg = self.store.empty_config()
        return self.svc.pending_init(d, cfg)

    def visible(self) -> List[ModelInfo]:
        """The Models panel's list: sorted and filtered as the user chose."""
        return self.view.visible(self.ui.msort, self.ui.mfilter, self.view.shown(self.ui))

    def cycle_quant(self, row: ModelInfo, step: int) -> Optional[str]:
        """Show the next (step 1) or the previous (-1) quantization of the model a list row stands for; the name of
        its entry, None when the model has only one (decision A68)."""
        quants = row.get("quants") or []
        if len(quants) < 2:
            return None
        at = next((i for i, q in enumerate(quants) if q["name"] == row["name"]), 0)
        name = quants[(at + step) % len(quants)]["name"]
        self.ui.mquants[row.get("group", row["name"])] = name
        return name

    def set_arrangement(self, kind: str, index: int) -> None:
        """Set the model lists' sort or filter (index wraps), keeping the selected model when it is
        still listed; an open model drop-down is rebuilt in the new order."""
        ui = self.ui
        name = self.visible()[ui.mrow]["name"] if self.visible() and ui.mrow < len(self.visible()) else None
        if kind == "sort":
            ui.msort = index % len(SORTS)
        else:
            ui.mfilter = index % len(FILTERS)
        ms = self.visible()
        ui.mrow = next((i for i, x in enumerate(ms) if x["name"] == name), 0)

    def open_model_picker(self) -> None:
        """Open the model drop-down on the pending model."""
        self.ui.picker = self.view.model_picker(str((self.ui.pending or {}).get("model", "auto")), self.ui.msort,
                                                self.ui.mfilter, self.ui.pending, self.ui.mquants)

    def choose_model(self, name: str) -> None:
        """Make name the pending model and load its profile (the picker and the Server panel's list)."""
        ui = self.ui
        if ui.pending is None:
            return
        ui.pending["model"] = name
        group = group_of(self.models.get(), name)
        if group is not None and group.several:                # the list shows the quantization that was chosen
            ui.mquants[group.name] = name
        self.svc.load_profile(ui.pending, name)
        m = self.models.by_name(self.svc.resolved_model(ui.pending))
        if m and m["status"] != "downloaded":
            ui.toast(f"{m['name']} is {status_name(m['status'])}. To download it: the Models panel (]), then d.", 8)

    def plugin_panels(self) -> List[PanelView]:
        """The plugins' configuration panels, in the order after Caching."""
        host = self.plugins()
        return list(host.panels()) if host is not None else []

    def panel_count(self) -> int:
        """The panels of the bar: the built-in ones and the plugins'."""
        return len(SUBPANELS) + len(self.plugin_panels())
