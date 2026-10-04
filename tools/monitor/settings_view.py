"""The Settings tab: one object for the app and the controller over the panels in
settings_panels (Server with the MODEL card, Models, Auto fit, Auto-tune, Router, Caching), the
drop-downs and the questions. Draws from the UI state; reads models and config.json through
SettingsService (the ModelStore port)."""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from .diskcache import CacheConfig, CacheFile
from .fmt import Row
from .model import ModelInfo, ServerData
from .settings import Pending, SettingsService
from .settings_panels.autofit import AutoFitPanel
from .settings_panels.caching import caching_panel
from .settings_panels.model_card import ModelCard
from .settings_panels.model_lines import ModelLines
from .settings_panels.models import ModelsDir, ModelsPanel
from .settings_panels.pickers import Pickers
from .settings_panels.router import router_panel
from .settings_panels.server import ServerPanel
from .settings_panels.tune import TunePanel
from .state import Confirm, Picker, UIState


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

    def server(self, ui: UIState, p: Pending, d: ServerData, cols: int, height: int, port: int) -> List[Row]:
        """Panel 1: the settings beside what runs now, the model list, the MODEL card (settings_panels.server)."""
        return self._server.draw(ui, p, d, cols, height, port)

    def models(self, ui: UIState, cols: int, height: int, mdir: ModelsDir) -> List[Row]:
        """Panel 2: every model, the selected one's details, a download, the actions (settings_panels.models)."""
        return self._models.draw(ui, cols, height, mdir)

    def autofit(self, ui: UIState, p: Pending, cols: int, height: int) -> List[Row]:
        """Panel 3: auto fit's pick for this Mac, its reasons and the ranking (settings_panels.autofit)."""
        return self._autofit.draw(ui, p, cols, height)

    def tune(self, ui: UIState, cols: int, server_up: bool) -> List[Row]:
        """Panel 4: Auto-tune (settings_panels.tune)."""
        return self._tune.draw(ui, cols, server_up)

    @staticmethod
    def router(ui: UIState, d: ServerData, saved: str, switches: Sequence[Tuple[str, str]], stale: Sequence[str],
               cols: int) -> List[Row]:
        """Panel 5: who switches the model (settings_panels.router)."""
        return router_panel(ui, d, saved, switches, stale, cols)

    def caching(self, ui: UIState, conf: CacheConfig, files: Sequence[CacheFile], folder: str, cols: int) -> List[Row]:
        """Panel 6: the disk cache (settings_panels.caching)."""
        return caching_panel(ui, conf, files, folder, cols, self.home)

    def picker(self, pk: Picker, cols: int, height: int) -> List[Row]:
        """An open drop-down."""
        return self.pickers.picker(pk, cols, height)

    def confirm(self, c: Confirm, cols: int) -> List[Row]:
        """A yes / no question."""
        return self.pickers.confirm(c, cols)

    def visible(self, sort: int, filt: int) -> List[ModelInfo]:
        """The models in the current sort order, filtered (the Models panel and the drop-down)."""
        return self.lines.visible(sort, filt)

    def arrange_picker(self, kind: str, sort: int, filt: int, reopen: Optional[str] = None) -> Picker:
        """A drop-down of every sort (or filter) option."""
        return self.pickers.arrange_picker(kind, sort, filt, reopen)

    def model_picker(self, cur: str, sort: int = 0, filt: int = 0, p: Optional[Pending] = None) -> Picker:
        """The model drop-down ("auto" on top)."""
        return self.pickers.model_picker(cur, sort, filt, p)
