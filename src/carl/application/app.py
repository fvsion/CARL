"""The Carl facade: one object over the application services, for the callers that know it (the dict facade
api.py, the commands of cli/commands, the tests). It builds the services and delegates; the logic
is in the services: config.py, inventory.py, cards.py, fit.py (THE Budget and StartPlan builder), router.py,
client_list.py and downloads.py. The composition root builds one Carl with the real adapters (composition/wiring.py);
tests build it with fakes. The facade goes in W9.4.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ..domain.catalog import inventory as dm
from ..domain.config.settings import Config, PluginKeys
from ..domain.memory.autofit import AutoFit, Budget, Candidate, Goal, Plan, Scope
from ..domain.memory.gguf import ModelShape
from ..domain.memory.server_fit import PickNeed
from ..domain.server.router_presets import Common, Preset
from ..domain.server.ub_order import UbAsk
from ..domain.shared.platform_words import MACOS, PlatformWords
from ..domain.shared.types import (Catalog, CustomCard, CustomInfo, HfFileList, JsonObject, LocalDb, ModelInfo,
                                   SettingSource, SettingValue, Settings)
from ..ports import (Clock, Console, CpuLayout, Downloader, GpuLimit, HostMemory, HubClient, ModelFolder, PluginHost,
                     ServerFit, ShapeReader)
from .cards import CardService
from .client_list import ClientListService
from .config import ConfigService, Stores
from .downloads import DOWNLOAD_HEADROOM, Downloads
from .fit import FitService
from .hub_token import HubToken
from .inventory import Inventory
from .router import RouterService

__all__ = ["Carl", "Stores", "DOWNLOAD_HEADROOM"]


class Carl:
    """Models, settings and downloads for one user (one CONF_DIR and models folder): a thin facade that delegates
    each call to its service. Contract C-114."""

    def __init__(self, stores: Stores, files: ModelFolder, shapes: ShapeReader, gpu: GpuLimit, host: HostMemory,
                 hub: HubClient, downloader: Downloader, clock: Clock, console: Console, home: str,
                 env_models_dir: Optional[str], words: PlatformWords = MACOS, os_name: str = "macos",
                 gpu_env: Optional[Mapping[str, str]] = None, plugins: Optional[PluginHost] = None,
                 cpu: Optional[CpuLayout] = None, server_fit: Optional[Callable[[], ServerFit]] = None,
                 build_backend: Optional[Callable[[], str]] = None,
                 build_version: Optional[Callable[[], str]] = None, hub_token: Optional[HubToken] = None) -> None:
        self.files = files
        self.shapes = shapes
        self.gpu = gpu
        self.host = host
        self.hub = hub
        self.downloader = downloader
        self.clock = clock
        self.console = console
        self.home = home
        self.env_models_dir = env_models_dir
        self.words = words                  # the platform words of the texts (the composition's profile)
        self.hub_token = hub_token          # the user's Hugging Face token (None: no token seam, as a test's Carl)
        self.plugins = plugins              # the plugins that are installed (None: no plugin seam, as before WP)
        self.config = ConfigService(stores, console, home, env_models_dir, words, self.plugin_keys)
        self.inventory = Inventory(self.config, files, shapes, console)
        self.cards = CardService(self.config, self.inventory)
        self.fit = FitService(self.config, self.inventory, gpu, host, os_name, gpu_env, cpu, server_fit, build_backend,
                               build_version)
        self.router = RouterService(self.config, self.inventory, self.fit)
        self.client_list = ClientListService(self.inventory, self.fit)
        self.downloads = Downloads(self.config, self.inventory, files, hub, downloader, clock, console, hub_token)

    def plugin_keys(self) -> Optional[PluginKeys]:
        """The keys config.json plugins.<id>.<key> may have: what the installed plugins declare (None: none can)."""
        return self.plugins.setting_keys() if self.plugins is not None else None

    @property
    def stores(self) -> Stores:
        return self.config.stores

    @stores.setter
    def stores(self, stores: Stores) -> None:
        self.config.stores = stores                 # every service reads the stores through the config service

    # ------------------------------------------------------------ config.json
    def load_config(self) -> Config:
        return self.config.load_config()

    def config_warnings(self) -> List[str]:
        return self.config.config_warnings()

    def save_config(self, cfg: Config) -> Config:
        return self.config.save_config(cfg)

    def parse_config(self, cfg: Optional[object]) -> Config:
        return self.config.parse_config(cfg)

    def models_dir(self, cfg: Config) -> str:
        return self.config.models_dir(cfg)

    def expand(self, path: str) -> str:
        return self.config.expand(path)

    def swa_mode(self) -> str:
        return self.config.swa_mode()

    # ------------------------------------------------------------ catalogue, models.json and the models
    def load_catalog(self) -> Catalog:
        return self.inventory.load_catalog()

    def load_local(self) -> LocalDb:
        return self.inventory.load_local()

    def save_local(self, db: LocalDb) -> None:
        self.inventory.save_local(db)

    def all_models(self, cfg: Config) -> List[ModelInfo]:
        return self.inventory.all_models(cfg)

    def mark_heads(self, models: List[ModelInfo]) -> List[ModelInfo]:
        return self.inventory.mark_heads(models)

    def add_drafter(self, m: ModelInfo) -> ModelInfo:
        return self.inventory.add_drafter(m)

    def adopt(self, db: LocalDb, renames: Dict[str, str]) -> None:
        self.inventory.adopt(db, renames)

    def find(self, name: str, models: List[ModelInfo]) -> Optional[ModelInfo]:
        return self.inventory.find(name, models)

    def local_shape(self, path: str) -> Optional[ModelShape]:
        return self.inventory.local_shape(path)

    def custom_defaults(self, path: str, drafter: bool = False) -> Tuple[Settings, CustomInfo]:
        return self.inventory.custom_defaults(path, drafter)

    def effective_tune(self, m: ModelInfo, cfg: Config) -> Tuple[Settings, Dict[str, SettingSource]]:
        return self.fit.effective_tune(m, cfg)

    def shape_of(self, m: ModelInfo) -> Optional[ModelShape]:
        return self.inventory.shape_of(m)

    def with_thinking(self, m: ModelInfo) -> ModelInfo:
        return self.inventory.with_thinking(m)

    def model_file(self, name: Optional[str]) -> Optional[str]:
        return self.inventory.model_file(name)

    # ------------------------------------------------------------ model cards
    def card_model(self, name: str) -> Tuple[ModelInfo, List[ModelInfo]]:
        return self.cards.card_model(name)

    def save_card(self, name: str, card: object) -> CustomCard:
        return self.cards.save_card(name, card)

    def set_card_field(self, name: str, key: str, args: Sequence[str]) -> CustomCard:
        return self.cards.set_card_field(name, key, args)

    def unset_card_field(self, name: str, key: str) -> CustomCard:
        return self.cards.unset_card_field(name, key)

    # ------------------------------------------------------------ the budget, auto fit and the start
    def budget(self, ram_gb: Optional[float] = None, reserve_gb: Optional[float] = None,
               vram: Optional[Sequence[float]] = None) -> Budget:
        return self.fit.budget(ram_gb, reserve_gb, vram=vram)

    def candidates(self, models: List[ModelInfo]) -> List[Candidate]:
        return self.fit.candidates(models)

    @staticmethod
    def auto_settings(cfg: Config) -> Tuple[Goal, Scope]:
        return FitService.auto_settings(cfg)

    def auto_fit(self, models: List[ModelInfo], goal: Goal = "everyday", scope: Scope = "catalogue",
                 budget: Optional[Budget] = None) -> AutoFit:
        return self.fit.auto_fit(models, goal, scope, budget)

    def measured_fit(self, models: List[ModelInfo], goal: Goal = "everyday", scope: Scope = "catalogue",
                     budget: Optional[Budget] = None) -> Tuple[AutoFit, Optional[PickNeed]]:
        return self.fit.measured_fit(models, goal, scope, budget)

    def pick_default(self, models: List[ModelInfo], budget: Optional[Budget] = None, goal: Goal = "everyday") -> str:
        return self.fit.pick_default(models, budget, goal)

    def auto_launch(self, models: List[ModelInfo], cfg: Config) -> Tuple[ModelInfo, Optional[str], Plan]:
        return self.fit.auto_launch(models, cfg)

    def default_quants(self, models: List[ModelInfo], cfg: Config) -> Dict[str, str]:
        return self.fit.default_quants(models, cfg)

    def resolve_launch(self, name: Optional[str], cfg: Config) -> Tuple[ModelInfo, List[ModelInfo], Optional[str]]:
        return self.fit.resolve_launch(name, cfg)

    def launch_env(self, name: Optional[str], use_config: bool) -> Tuple[Dict[str, SettingValue], Optional[str]]:
        return self.fit.launch_env(name, use_config)

    def apply_order(self, m: ModelInfo, vals: Settings, src: Dict[str, SettingSource], cfg: Config,
                    shape: ModelShape, source: Optional[dm.MtpSource]) -> str:
        return self.fit.apply_order(m, vals, src, cfg, shape, source)

    def answer_ub(self, model: str, ask: UbAsk, choice: str) -> None:
        return self.fit.answer_ub(model, ask, choice)

    # ------------------------------------------------------------ the clients and router mode
    def client_models(self, cfg: Config, running: Optional[str] = None, router: Optional[bool] = None) -> JsonObject:
        return self.client_list.client_models(cfg, running, router)

    def router_preset(self, cfg: Config, templates_dir: str, thread_pin: str = "") -> Tuple[Preset, Common]:
        return self.router.router_preset(cfg, templates_dir, thread_pin)

    # ------------------------------------------------------------ Hugging Face + downloads
    def hf_files(self, repo: str, revision: str = "main") -> HfFileList:
        return self.downloads.hf_files(repo, revision)

    def verify(self, m: ModelInfo) -> bool:
        return self.downloads.verify(m)

    def download_drafter(self, m: ModelInfo) -> bool:
        return self.downloads.download_drafter(m)

    def download(self, m: ModelInfo, models_dir: str, vision: Optional[bool] = None) -> bool:
        return self.downloads.download(m, models_dir, vision)

    def download_hf(self, spec: str, name: Optional[str] = None) -> bool:
        return self.downloads.download_hf(spec, name)

    def delete(self, m: ModelInfo) -> None:
        self.downloads.delete(m)
