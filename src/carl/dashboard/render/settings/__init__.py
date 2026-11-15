"""The Settings tab: one object for the app and the controller over the panels of this folder (Server with the MODEL
card, Models, Agents, Auto fit, Auto-tune, Router, Caching), the drop-downs and the questions. One module for each
panel: server (with model_card, the MODEL card below it), models, agents, autofit, tune, router and caching; pickers
(the drop-downs and questions), model_lines (the model lists) and common (pieces several panels draw). Draws from the
UI state; reads models and config.json through SettingsService (the ModelStore port). Pure: what a panel tells the
dashboard (its keys, its sections, a corrected scroll) goes to the Shown that the app passes."""
from __future__ import annotations

from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from ....application.settings_service import AgentsInfo, Pending, SettingsService
from ....domain.server.slot_files import CacheConfig, CacheFile
from ....domain.server.snapshot import ModelInfo, ServerData
from ....ports.cache import StatRow
from ....ports.plugin import PanelView
from ....ui.fmt import Row
from ...clients import Drift
from ...shown import Shown
from ...state import Confirm, Picker, UIState
from .agents import agents_panel
from .autofit import AutoFitPanel
from .caching import caching_panel
from .model_card import ModelCard
from .model_lines import ModelLines
from .models import ModelsDir, ModelsPanel
from .page import plugin_panel
from .pickers import Pickers
from .router import router_panel
from .server import ServerPanel
from .tune import TunePanel


class SettingsView:
    """Draws the Settings panels."""

    def __init__(self, svc: SettingsService, config_file: str, home: str) -> None:
        self.home = home
        self.lines = ModelLines(svc)
        self.pickers = Pickers(svc, self.lines)
        self._server = ServerPanel(svc, self.lines, ModelCard(svc, home), config_file, home)
        self._models = ModelsPanel(svc, self.lines, home)
        self._autofit = AutoFitPanel(svc)
        self._tune = TunePanel(svc)

    def server(self, ui: UIState, p: Pending, d: ServerData, cols: int, height: int, port: int,
               limit_src: str = "", out: Optional[Shown] = None) -> List[Row]:
        """Panel 1: the settings beside what runs now, the model list, the MODEL card (server.py)."""
        return self._server.draw(ui, p, d, cols, height, port, limit_src, out)

    def models(self, ui: UIState, cols: int, height: int, mdir: ModelsDir, running: str = "",
               out: Optional[Shown] = None) -> List[Row]:
        """Panel 2: every model, the selected one's details, a download, the actions (models.py)."""
        return self._models.draw(ui, cols, height, mdir, running, out)

    @staticmethod
    def agents(ui: UIState, info: Optional[AgentsInfo], cols: int, height: int, error: str = "",
               out: Optional[Shown] = None) -> List[Row]:
        """Panel 3: the thinking of the main session and of the coder, per model (agents.py)."""
        return agents_panel(ui, info, cols, height, error, out)

    def autofit(self, ui: UIState, p: Pending, cols: int, height: int, out: Optional[Shown] = None) -> List[Row]:
        """Panel 3: auto fit's pick for this Mac, its reasons and the ranking (autofit.py)."""
        return self._autofit.draw(ui, p, cols, height, out)

    def tune(self, ui: UIState, cols: int, server_up: bool, height: int = 0, out: Optional[Shown] = None) -> List[Row]:
        """Panel 4: Auto-tune (tune.py)."""
        return self._tune.draw(ui, cols, server_up, height, out)

    @staticmethod
    def router(ui: UIState, d: ServerData, saved: str, switches: Sequence[Tuple[str, str]], stale: Sequence[Drift],
               installed: int, cols: int, here: bool = False, height: int = 0,
               out: Optional[Shown] = None) -> List[Row]:
        """Panel 5: who switches the model; stale: the client configs that are out of date, installed: the models they
        should list (router.py)."""
        return router_panel(ui, d, saved, switches, stale, installed, cols, here, height, out)

    def caching(self, ui: UIState, conf: CacheConfig, files: Sequence[CacheFile], folder: str, cols: int,
                api: str = "", height: int = 0, names: Optional[Mapping[str, str]] = None,
                window: str = "", did: Sequence[StatRow] = (), did_when: str = "",
                out: Optional[Shown] = None, free: int = 0) -> List[Row]:
        """Panel 6: the disk cache (caching.py); free: the free disk where it saves (bytes, 0 unknown)."""
        return caching_panel(ui, conf, files, folder, cols, self.home, api, height, names, window, did, did_when, out,
                             free)

    @staticmethod
    def plugin(ui: UIState, pv: PanelView, cols: int, height: int, out: Optional[Shown] = None) -> List[Row]:
        """A plugin's panel, after Caching: its settings and what it reads (page.py; WP)."""
        return plugin_panel(ui, pv, cols, height, out)

    def picker(self, pk: Picker, cols: int, height: int, ui: Optional[UIState] = None,
               out: Optional[Shown] = None) -> List[Row]:
        """An open drop-down (its footer keys go to out)."""
        return self.pickers.picker(pk, cols, height, ui, out)

    def confirm(self, c: Confirm, cols: int, ui: Optional[UIState] = None, out: Optional[Shown] = None) -> List[Row]:
        """A yes / no question (its footer keys go to out)."""
        return self.pickers.confirm(c, cols, ui, out)

    def visible(self, sort: int, filt: int, quants: Optional[Mapping[str, str]] = None) -> List[ModelInfo]:
        """The models in the current sort order, filtered (the Models panel and the drop-down); one row for a model with
        several quantizations (model_lines.py)."""
        return self.lines.visible(sort, filt, quants)

    def shown(self, ui: UIState) -> Dict[str, str]:
        """The quantization shown for each model with several (the Server panel's model, then what a list chose)."""
        return self.lines.shown(ui)

    def arrange_picker(self, kind: str, sort: int, filt: int, reopen: Optional[str] = None) -> Picker:
        """A drop-down of every sort (or filter) option."""
        return self.pickers.arrange_picker(kind, sort, filt, reopen)

    def model_picker(self, cur: str, sort: int = 0, filt: int = 0, p: Optional[Pending] = None,
                     quants: Optional[Mapping[str, str]] = None) -> Picker:
        """The model drop-down ("auto" on top)."""
        return self.pickers.model_picker(cur, sort, filt, p, quants)

    def agents_picker(self, cur: str, sort: int = 0, filt: int = 0,
                      quants: Optional[Mapping[str, str]] = None) -> Picker:
        """The Agents panel's model drop-down (no "auto")."""
        return self.pickers.agents_picker(cur, sort, filt, quants)
