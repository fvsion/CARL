"""CARL's models and settings as one typed API: the answers of the application services for the dashboard, Auto-tune
and the commands of the command line. It replaces the dict facade of the old models tool (W4.1): the same answers, typed,
from an object the composition builds (composition/entries.py Entry.api), not from module globals. Its documents
stay plain JSON values (config.json as a dict, a model as a ModelInfo dict), as the dashboard keeps them.

A cfg argument is a config.json document from the caller (it is validated), or None for config.json itself. A
models argument is the model list the caller has, or None to list the models again. Contract C-115.
"""
from __future__ import annotations

from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from ..domain.catalog import inventory as dm
from ..domain.catalog.hf import parse_hf
from ..domain.config.settings import MODEL_KEYS, SCHEMA, Config
from ..domain.memory.autofit import AutoFit, Budget, as_goal, as_scope
from ..domain.memory.gguf import ModelShape
from ..domain.memory.split import Slot
from ..domain.server.ub_order import UbAsk, UbOrder
from ..domain.shared.platform_words import PlatformWords
from ..domain.shared.types import (Catalog, CustomCard, CustomInfo, HfFileList, JsonObject, JsonValue, LocalDb,
                                   ModelInfo, SettingSource, SettingValue, Settings, Zone)
from ..domain.shared.errors import ConfigError
from .app import Carl
from .hub_token import HubToken


class Api:
    """The models and settings of one settings folder. carl: the services (application/app.py); config_file,
    local_file: config.json and models.json of that folder."""

    def __init__(self, carl: Carl, config_file: str, local_file: str) -> None:
        self.carl = carl
        self.config_file = config_file
        self.local_file = local_file

    @property
    def words(self) -> PlatformWords:
        """The platform words of the texts (the composition gave them to the services)."""
        return self.carl.words

    def _config(self, cfg: Optional[Mapping[str, object]]) -> Config:
        return self.carl.parse_config(dict(cfg) if cfg else None)

    def _models(self, models: Optional[List[ModelInfo]]) -> List[ModelInfo]:
        return models or self.all_models()

    # ------------------------------------------------------------ config.json
    def load_config(self) -> JsonObject:
        return self.carl.load_config().to_json()

    def save_config(self, cfg: Mapping[str, object]) -> JsonObject:
        """Validate and write config.json; the document as written."""
        return self.carl.save_config(self.carl.config.validate(dict(cfg))[0]).to_file(self.words)

    def config_warnings(self) -> List[str]:
        return self.carl.config_warnings()

    @staticmethod
    def empty_config() -> JsonObject:
        """A config.json with no setting."""
        return {"schema": SCHEMA}

    @staticmethod
    def builtin_tune() -> Dict[str, JsonValue]:
        """Each model setting's built-in default (before the catalogue, Auto-tune and the user's settings)."""
        return {k: s.to_dict()["default"] for k, s in MODEL_KEYS.items()}

    def models_dir(self, cfg: Optional[Mapping[str, object]] = None) -> str:
        return self.carl.models_dir(self._config(cfg))

    # ------------------------------------------------------------ the catalogue, models.json and the models
    def load_catalog(self) -> Catalog:
        return self.carl.load_catalog()

    def catalog_default(self) -> str:
        """The catalogue's own default model."""
        return str(self.load_catalog()["default"])

    def load_local(self) -> LocalDb:
        return self.carl.load_local()

    def save_local(self, db: LocalDb) -> None:
        self.carl.save_local(db)

    def shape_of(self, path: str) -> ModelShape:
        """The GGUF header shape of a local file (cached by path and mtime: a header read is 64 MB)."""
        return self.carl.shapes.local(path)

    def model_shape(self, m: ModelInfo) -> Optional[ModelShape]:
        """A model's header shape: its file's, or (a catalogue model not downloaded) Hugging Face's, cached; None
        when it cannot be read."""
        return self.carl.shape_of(m)

    def custom_defaults(self, path: str) -> Tuple[Settings, CustomInfo]:
        """The starting tune of a model that is not in the catalogue, from its GGUF header."""
        return self.carl.custom_defaults(path)

    def all_models(self, cfg: Optional[Mapping[str, object]] = None) -> List[ModelInfo]:
        """Every model CARL knows: the catalogue, each .gguf in the models folder and each custom download."""
        return self.carl.all_models(self._config(cfg))

    def marked(self, models: List[ModelInfo]) -> List[ModelInfo]:
        """The models with the MTP mark of each (ModelInfo.head, A73), from the headers of the files and the cached
        headers of the others; the lists a person reads use it (the model list of the dashboard, carl models)."""
        return self.carl.mark_heads(models)

    def default_quants(self, models: Optional[List[ModelInfo]] = None, cfg: Optional[Mapping[str, object]] = None
                       ) -> Dict[str, str]:
        """For each model with several quantizations (by its name) the entry a start takes (decision A68)."""
        return self.carl.default_quants(self._models(models), self._config(cfg))

    def find(self, name: str, models: Optional[List[ModelInfo]] = None) -> Optional[ModelInfo]:
        return self.carl.find(name, self._models(models))

    def effective_tune(self, m: ModelInfo, cfg: Optional[Mapping[str, object]] = None
                       ) -> Tuple[Settings, Dict[str, SettingSource]]:
        """(settings, source per key): built-ins < catalogue < Auto-tune (this computer) < config profile."""
        return self.carl.effective_tune(m, self._config(cfg))

    @staticmethod
    def ctx_zones(m: ModelInfo) -> Tuple[int, int, int]:
        """Context per slot: (good, slow, very_slow); good is at least 96K."""
        return dm.ctx_zones(m)

    @staticmethod
    def ctx_zone(m: ModelInfo, ctx: int) -> Zone:
        return dm.ctx_zone(m, ctx)

    @staticmethod
    def parse_hf(spec: str) -> Tuple[str, Optional[str], str]:
        """hf:OWNER/REPO/FILE.gguf (or a huggingface.co address) -> (repo, file or None, revision)."""
        return parse_hf(spec)

    # ------------------------------------------------------------ auto fit and the start
    def gpu_limit(self) -> Tuple[int, str]:
        """(bytes, how CARL found it) a model may use on the GPU (application/fit.py, the one budget builder)."""
        return self.carl.fit.gpu_limit()

    def gpu_slots(self) -> List[Slot]:
        """The GPUs a start plans its split over, in the order of -dev, each with its room (FitService.slots); [] on a
        Mac and when no GPU can be named."""
        return self.carl.fit.slots()

    def model_ub(self, m: ModelInfo) -> int:
        """The model's own -ub: its Auto-tune record's, else this OS's default (A45, FitService.model_ub)."""
        return self.carl.fit.model_ub(m)

    def tune_stale(self, m: ModelInfo) -> str:
        """How this computer's llama.cpp build and backend differ from those of the model's Auto-tune record ("" when they
        do not; A72, FitService.tune_stale_parts)."""
        return self.carl.fit.tune_stale_parts(m)

    def ub_order(self, m: ModelInfo) -> UbOrder:
        """The -ub of a start of this model and where it comes from (A51, FitService.ub_order)."""
        return self.carl.fit.ub_order(m)

    def answer_ub(self, name: str, ask: UbAsk, choice: str) -> None:
        """Save the answer to the -ub question (A51, FitService.answer_ub)."""
        self.carl.fit.answer_ub(name, ask, choice)

    def budget(self, ram_gb: Optional[float] = None, reserve_gb: Optional[float] = None,
               vram: Optional[Sequence[float]] = None) -> Budget:
        return self.carl.budget(ram_gb, reserve_gb, vram)

    def pick_default(self, models: Optional[List[ModelInfo]] = None, budget: Optional[Budget] = None) -> str:
        """This computer's default: auto fit's pick from the whole catalogue (everyday goal)."""
        return self.carl.pick_default(self._models(models), budget)

    def resolve_launch(self, name: Optional[str] = None, cfg: Optional[Mapping[str, object]] = None
                       ) -> Tuple[ModelInfo, List[ModelInfo], Optional[str]]:
        """(model, all models, note) for a start: name, else config llama.model, else auto fit's pick (the best
        downloaded stock model that fits when the pick is not downloaded)."""
        return self.carl.resolve_launch(name, self._config(cfg))

    def launch_model(self, cfg: Optional[Mapping[str, object]] = None) -> str:
        """The model a start with cfg loads."""
        return str(self.resolve_launch(None, cfg)[0]["name"])

    def auto_fit(self, goal: Optional[str] = None, scope: Optional[str] = None,
                 models: Optional[List[ModelInfo]] = None, cfg: Optional[Mapping[str, object]] = None) -> AutoFit:
        """Auto fit for this computer: the best ranked stock model for the goal (everyday, hard-code) from the scope
        (catalogue, downloaded); None takes config.json's llama.auto_goal and llama.auto_fit."""
        c = self._config(cfg)
        g, s = self.carl.auto_settings(c)
        return self.carl.auto_fit(models or self.carl.all_models(c), as_goal(goal) if goal else g,
                                  as_scope(scope) if scope else s)

    def client_models(self, cfg: Optional[Mapping[str, object]] = None, running: Optional[str] = None,
                      router: Optional[bool] = None) -> JsonObject:
        """The installed models for the OpenCode and Pi configs: {schema, default, models}. Single-model mode: only
        the model the server runs (running; None: the model a start loads); router mode (router None: config.json's
        llama.mode): every downloaded model."""
        return self.carl.client_models(self._config(cfg), running, router)

    def launch_env(self, name: Optional[str] = None, use_config: bool = True
                   ) -> Tuple[Dict[str, SettingValue], Optional[str]]:
        return self.carl.launch_env(name, use_config)

    # ------------------------------------------------------------ model cards
    def save_card(self, name: str, card: Mapping[str, object]) -> CustomCard:
        """Check and store the user's card of a custom model (models.json); the card as stored. Raises ConfigError
        for a catalogue model or a bad field."""
        return self.carl.save_card(name, dict(card))

    def set_card_field(self, name: str, key: str, args: Sequence[str]) -> CustomCard:
        return self.carl.set_card_field(name, key, args)

    def unset_card_field(self, name: str, key: str) -> CustomCard:
        return self.carl.unset_card_field(name, key)

    # ------------------------------------------------------------ Hugging Face and downloads
    def hf_files(self, repo: str, revision: str = "main") -> HfFileList:
        """[(file, bytes, sha256)] of the GGUF files in a Hugging Face repo (first parts only)."""
        return self.carl.hf_files(repo, revision)

    def hub_token(self) -> HubToken:
        """The user's Hugging Face token (set, clear, status: application/hub_token.py). ConfigError when this API
        was built with no token seam."""
        if self.carl.hub_token is None:
            raise ConfigError("this CARL has no Hugging Face token store")
        return self.carl.hub_token

    def verify(self, m: ModelInfo) -> bool:
        return self.carl.verify(m)

    def download(self, m: ModelInfo, models_dir: str, vision: Optional[bool] = None) -> bool:
        """The model, its drafters and, with vision (None: when llama.vision is on), its vision projector."""
        return self.carl.download(m, models_dir, vision)

    def download_hf(self, spec: str, name: Optional[str] = None) -> bool:
        return self.carl.download_hf(spec, name)

    def download_drafter(self, m: ModelInfo) -> bool:
        """The MTP drafter of a custom Gemma 4 model."""
        return self.carl.download_drafter(self.carl.add_drafter(m))

    def delete(self, m: ModelInfo) -> None:
        self.carl.delete(m)
