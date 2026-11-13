"""The models CARL knows, config.json and GGUF shapes: the ModelStore port, CarlStore over the typed API
(carl.application.api, W4.1; the dashboard's composition gives it: composition/dashboard.py store) with its GPU limit
(the budget's builder's), and a short-lived cache of the model list. Contract C-125."""
from __future__ import annotations

import os
import time
from typing import Callable, Dict, List, Optional, Protocol, Tuple

from ..domain.catalog.hf_token import TokenStatus
from ..domain.memory.autofit import AutoFit
from ..domain.memory.split import Slot
from ..domain.server.ub_order import UbAsk, UbOrder
from ..domain.server.snapshot import JSONDict, ModelInfo, Shape
from .api import Api
from .hub_token import Cleared

HFFile = Tuple[str, Optional[int], Optional[str]]     # (file name, bytes, sha256)


class ModelStore(Protocol):
    """Models, their tunes, config.json and GGUF memory shapes."""

    config_file: str

    def all_models(self) -> List[ModelInfo]: ...
    def find(self, name: str, models: List[ModelInfo]) -> Optional[ModelInfo]: ...
    def default_quants(self, models: List[ModelInfo]) -> Dict[str, str]: ...
    def builtin_tune(self) -> JSONDict: ...
    def effective_tune(self, m: ModelInfo, cfg: JSONDict) -> Tuple[JSONDict, Dict[str, str]]: ...
    def ctx_zones(self, m: ModelInfo) -> Tuple[int, int, int]: ...
    def ctx_zone(self, m: ModelInfo, ctx: int) -> str: ...
    def header_info(self, path: str) -> JSONDict: ...
    def shape_of(self, path: str) -> Shape: ...
    def model_shape(self, m: ModelInfo) -> Optional[Shape]: ...
    def file_size(self, path: str) -> int: ...
    def gpu_limit(self) -> Tuple[int, str]: ...
    def gpu_slots(self) -> List[Slot]: ...
    def model_ub(self, m: ModelInfo) -> int: ...
    def tune_stale(self, m: ModelInfo) -> str: ...
    def ub_order(self, m: ModelInfo) -> UbOrder: ...
    def answer_ub(self, name: str, ask: UbAsk, choice: str) -> None: ...
    def empty_config(self) -> JSONDict: ...
    def load_config(self) -> JSONDict: ...
    def save_config(self, cfg: JSONDict) -> None: ...
    def launch_model(self, cfg: JSONDict) -> str: ...
    def auto_fit(self, goal: str, scope: str) -> AutoFit: ...
    def catalog_default(self) -> str: ...
    def models_dir(self) -> str: ...
    def parse_hf(self, spec: str) -> Tuple[str, Optional[str], Optional[str]]: ...
    def hf_files(self, repo: str) -> List[HFFile]: ...
    def hf_token_status(self) -> TokenStatus: ...
    def set_hf_token(self, text: str) -> str: ...
    def clear_hf_token(self) -> Cleared: ...
    def delete(self, m: ModelInfo) -> None: ...
    def save_card(self, name: str, card: JSONDict) -> None: ...
    def client_models(self, running: Optional[str] = None, router: Optional[bool] = None) -> JSONDict: ...


class CarlStore:
    """ModelStore over the typed API of the dashboard's entry (composition/dashboard.py store: its settings folder and
    platform words)."""

    def __init__(self, api: Api) -> None:
        self._carl: Api = api
        self.config_file: str = api.config_file

    def all_models(self) -> List[ModelInfo]:
        models: List[ModelInfo] = self._carl.marked(self._carl.all_models())
        return models

    def find(self, name: str, models: List[ModelInfo]) -> Optional[ModelInfo]:
        if not models:          # carl.find would read the list again (and raise what made it empty)
            return None
        m: Optional[ModelInfo] = self._carl.find(name, models)
        return m

    def default_quants(self, models: List[ModelInfo]) -> Dict[str, str]:
        """For each model with several quantizations (by the model's name) the entry a start takes."""
        return self._carl.default_quants(models)

    def builtin_tune(self) -> JSONDict:
        return self._carl.builtin_tune()

    def effective_tune(self, m: ModelInfo, cfg: JSONDict) -> Tuple[JSONDict, Dict[str, str]]:
        vals, src = self._carl.effective_tune(m, cfg)
        return vals, src

    def ctx_zones(self, m: ModelInfo) -> Tuple[int, int, int]:
        good, slow, very = self._carl.ctx_zones(m)
        return good, slow, very

    def ctx_zone(self, m: ModelInfo, ctx: int) -> str:
        zone: str = self._carl.ctx_zone(m, ctx)
        return zone

    def header_info(self, path: str) -> JSONDict:
        """What the GGUF header says about a model that is not in the catalogue (mtp, quant, ...)."""
        info: JSONDict = self._carl.custom_defaults(path)[1]
        return info

    def shape_of(self, path: str) -> Shape:
        shape: Shape = self._carl.shape_of(path)
        return shape

    def model_shape(self, m: ModelInfo) -> Optional[Shape]:
        """A model's header shape: the file's, or (a catalogue model not downloaded) the cached
        one from Hugging Face; None when it can't be read."""
        shape: Optional[Shape] = self._carl.model_shape(m)
        return shape

    def file_size(self, path: str) -> int:
        return os.path.getsize(path)

    def gpu_limit(self) -> Tuple[int, str]:
        """(bytes, how it was found) the GPU may use (may run a Swift probe, up to 2 min, once): the budget's
        builder, application/fit.py."""
        limit, how = self._carl.gpu_limit()
        return int(limit), str(how)

    def gpu_slots(self) -> List[Slot]:
        """The GPUs a start plans its split over, in the order of -dev (the budget's builder; [] on a Mac)."""
        slots: List[Slot] = self._carl.gpu_slots()
        return slots

    def model_ub(self, m: ModelInfo) -> int:
        """The model's own -ub: its Auto-tune record's, else this OS's default (A45; the budget's builder)."""
        return int(self._carl.model_ub(m))

    def tune_stale(self, m: ModelInfo) -> str:
        """How the installed build and backend differ from the model's Auto-tune record ("" when they do not; A72)."""
        return self._carl.tune_stale(m)

    def ub_order(self, m: ModelInfo) -> UbOrder:
        """The -ub of a start of this model and where it comes from (A51; FitService.ub_order)."""
        return self._carl.ub_order(m)

    def answer_ub(self, name: str, ask: UbAsk, choice: str) -> None:
        """The answer to the -ub question saved in config.json (A51; FitService.answer_ub)."""
        self._carl.answer_ub(name, ask, choice)

    def empty_config(self) -> JSONDict:
        return self._carl.empty_config()

    def load_config(self) -> JSONDict:
        cfg: JSONDict = self._carl.load_config()
        return cfg

    def save_config(self, cfg: JSONDict) -> None:
        self._carl.save_config(cfg)

    def launch_model(self, cfg: JSONDict) -> str:
        """The model a llama.cpp start with cfg loads (carl.resolve_launch)."""
        return self._carl.launch_model(cfg)

    def auto_fit(self, goal: str, scope: str) -> AutoFit:
        """Auto fit for this Mac with a goal (everyday / hard-code) and scope (catalogue / downloaded)."""
        fit: AutoFit = self._carl.auto_fit(goal, scope)
        return fit

    def catalog_default(self) -> str:
        return self._carl.catalog_default()

    def models_dir(self) -> str:
        path: str = self._carl.models_dir()
        return path

    def parse_hf(self, spec: str) -> Tuple[str, Optional[str], Optional[str]]:
        repo, file, rev = self._carl.parse_hf(spec)
        return repo, file, rev

    def hf_files(self, repo: str) -> List[HFFile]:
        return [(str(f), b, sha) for f, b, sha in self._carl.hf_files(repo)]

    def hf_token_status(self) -> TokenStatus:
        """Which Hugging Face token a call uses and its last characters (never the token)."""
        return self._carl.hub_token().status()

    def set_hf_token(self, text: str) -> str:
        """Keep a Hugging Face token (ConfigError when it is none); where it is kept."""
        return self._carl.hub_token().set(text)

    def clear_hf_token(self) -> Cleared:
        """Remove the token CARL keeps."""
        return self._carl.hub_token().clear()

    def delete(self, m: ModelInfo) -> None:
        self._carl.delete(m)

    def save_card(self, name: str, card: JSONDict) -> None:
        """Check and store a custom model's card (carl.ConfigError says what is wrong)."""
        self._carl.save_card(name, card)

    def client_models(self, running: Optional[str] = None, router: Optional[bool] = None) -> JSONDict:
        """The models as the client configs list them (carl.client_models): single-model mode, only the model the
        server runs (running; None: the model a start loads); router mode, every installed model."""
        doc: JSONDict = self._carl.client_models(None, running, router)
        return doc


class ModelList:
    """The model list, re-read from the store at most every 10 s (it lists the models
    folder and reads config.json), plus what "auto" means on this Mac (auto fit, cached
    with the list). error: why the last read failed (a broken catalogue, models.json or
    config.json), None when it worked."""

    def __init__(self, store: ModelStore, on_error: Callable[[str], None],
                 clock: Callable[[], float] = time.time) -> None:
        self.store = store
        self.on_error = on_error
        self.clock = clock
        self.items: List[ModelInfo] = []
        self.error: Optional[str] = None
        self.t = 0.0
        self._auto: Dict[Tuple[str, str], Tuple[float, str]] = {}            # (goal, scope) -> (list time, model)
        self._fit: Dict[Tuple[str, str], Tuple[float, Optional[AutoFit]]] = {}
        self._defaults: Tuple[float, Dict[str, str]] = (-1.0, {})
        self.fit_error: Optional[str] = None
        self._clients: Dict[Tuple[Optional[str], Optional[bool]], Tuple[float, Optional[JSONDict]]] = {}

    def get(self, refresh: bool = False) -> List[ModelInfo]:
        if refresh or self.clock() - self.t > 10:
            try:
                self.items = self.store.all_models()
                self.error = None
            except Exception as e:      # carl.py's errors vary (config, catalogue, disk): show, keep the old list
                if self.error != str(e):
                    self.on_error(f"models: {e}")
                self.error = str(e)
            self.t = self.clock()
        return self.items

    def defaults(self) -> Dict[str, str]:
        """For each model with several quantizations (by its name) the entry a start takes (decision A68), re-worked
        when the list is re-read; {} when it cannot be worked out."""
        ms = self.get()
        if self._defaults[0] != self.t:
            try:
                self._defaults = (self.t, self.store.default_quants(ms))
            except Exception:           # a broken config or an unreadable header: the first of the largest downloaded
                self._defaults = (self.t, {})
        return self._defaults[1]

    def by_name(self, name: Optional[str]) -> Optional[ModelInfo]:
        ms = self.get()
        return self.store.find(name, ms) if name and ms else None

    def choices(self) -> List[str]:
        """"auto", then the downloaded models, then the others."""
        ms = self.get()
        return (["auto"] + [m["name"] for m in ms if m["status"] == "downloaded"]
                + [m["name"] for m in ms if m["status"] != "downloaded"])

    def downloaded(self) -> List[ModelInfo]:
        return [m for m in self.get() if m["status"] == "downloaded"]

    def auto_fit(self, goal: str = "everyday", scope: str = "catalogue") -> Optional[AutoFit]:
        """Auto fit's answer for this Mac (None when it can't be worked out: fit_error says
        why), recomputed when the list is re-read."""
        self.get()
        key = (goal, scope)
        hit = self._fit.get(key)
        if hit is None or hit[0] != self.t:
            try:
                fit: Optional[AutoFit] = self.store.auto_fit(goal, scope)
                self.fit_error = None
            except Exception as e:      # a broken catalogue / config, an unreadable header: say why, no pick
                fit, self.fit_error = None, str(e)
            hit = self._fit[key] = (self.t, fit)
        return hit[1]

    def auto_model(self, goal: str = "everyday", scope: str = "catalogue") -> str:
        """The model "auto" starts on this Mac with this goal and scope (config.json's
        llama.model ignored): auto fit's pick, or the best downloaded model that fits while
        the pick isn't downloaded. Recomputed when the list is re-read."""
        self.get()
        key = (goal, scope)
        hit = self._auto.get(key)
        if hit is None or hit[0] != self.t:
            try:
                cfg = self.store.load_config()
                llama = cfg.setdefault("llama", {})
                llama.pop("model", None)
                llama.update(auto_goal=goal, auto_fit=scope)
                name = self.store.launch_model(cfg)
            except Exception:           # nothing downloaded / no stock model fits, bad config, ...
                fit = self.auto_fit(goal, scope)
                name = fit.name if fit and fit.name else self._catalog_default()
            hit = self._auto[key] = (self.t, name)
        return hit[1]

    def client_list(self, running: Optional[str] = None, router: Optional[bool] = None) -> Optional[JSONDict]:
        """The models for the client configs (re-read with the list; None when it can't be worked out). Single-model
        mode: only running (the model the server runs; None: the model a start loads); router mode: every installed
        model (router None: config.json's llama.mode)."""
        self.get()
        key = (running, router)
        hit = self._clients.get(key)
        if hit is None or hit[0] != self.t:
            try:
                doc: Optional[JSONDict] = self.store.client_models(running, router)
            except Exception:           # a broken config / catalogue: the pasted config lists the served model
                doc = None
            self._clients = {k: v for k, v in self._clients.items() if v[0] == self.t}
            hit = self._clients[key] = (self.t, doc)
        return hit[1]

    def _catalog_default(self) -> str:
        try:
            return self.store.catalog_default()
        except Exception:               # the catalogue itself can't be read (self.error says why)
            return "auto"
