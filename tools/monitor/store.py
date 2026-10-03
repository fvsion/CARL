"""The models CARL knows, config.json and GGUF shapes: the ModelStore port, its adapter
over tools/carl.py and tools/gguf_shape.py, and a short-lived cache of the model list."""
from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

from carl_core.domain.autofit import AutoFit

from . import gguf
from .model import JSONDict, ModelInfo, Shape

HFFile = Tuple[str, Optional[int], Optional[str]]     # (file name, bytes, sha256)


class ModelStore(Protocol):
    """Models, their tunes, config.json and GGUF memory shapes."""

    config_file: str
    conf_dir: str

    def all_models(self) -> List[ModelInfo]: ...
    def find(self, name: str, models: List[ModelInfo]) -> Optional[ModelInfo]: ...
    def builtin_tune(self) -> JSONDict: ...
    def effective_tune(self, m: ModelInfo, cfg: JSONDict) -> Tuple[JSONDict, Dict[str, str]]: ...
    def ctx_zones(self, m: ModelInfo) -> Tuple[int, int, int]: ...
    def ctx_zone(self, m: ModelInfo, ctx: int) -> str: ...
    def header_info(self, path: str) -> JSONDict: ...
    def shape_of(self, path: str) -> Shape: ...
    def model_shape(self, m: ModelInfo) -> Optional[Shape]: ...
    def file_size(self, path: str) -> int: ...
    def gpu_limit(self) -> Tuple[int, str]: ...
    def empty_config(self) -> JSONDict: ...
    def load_config(self) -> JSONDict: ...
    def save_config(self, cfg: JSONDict) -> None: ...
    def launch_model(self, cfg: JSONDict) -> str: ...
    def auto_fit(self, goal: str, scope: str) -> AutoFit: ...
    def catalog_default(self) -> str: ...
    def models_dir(self) -> str: ...
    def parse_hf(self, spec: str) -> Tuple[str, Optional[str], Optional[str]]: ...
    def hf_files(self, repo: str) -> List[HFFile]: ...
    def delete(self, m: ModelInfo) -> None: ...
    def save_card(self, name: str, card: JSONDict) -> None: ...
    def client_models(self) -> JSONDict: ...


class CarlStore:
    """ModelStore over tools/carl.py (untyped: its results are taken as the documented shapes)."""

    def __init__(self) -> None:
        import carl
        self._carl: Any = carl
        self.config_file: str = carl.CONFIG_FILE
        self.conf_dir: str = carl.CONF_DIR

    def all_models(self) -> List[ModelInfo]:
        models: List[ModelInfo] = self._carl.all_models()
        return models

    def find(self, name: str, models: List[ModelInfo]) -> Optional[ModelInfo]:
        if not models:          # carl.find would read the list again (and raise what made it empty)
            return None
        m: Optional[ModelInfo] = self._carl.find(name, models)
        return m

    def builtin_tune(self) -> JSONDict:
        return {k: s["default"] for k, s in self._carl.MODEL_KEYS.items()}

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
        shape: Optional[Shape] = self._carl.app().shape_of(m)
        return shape

    def file_size(self, path: str) -> int:
        return os.path.getsize(path)

    def gpu_limit(self) -> Tuple[int, str]:
        return gguf.gpu_limit()

    def empty_config(self) -> JSONDict:
        return {"schema": self._carl.SCHEMA}

    def load_config(self) -> JSONDict:
        cfg: JSONDict = self._carl.load_config()
        return cfg

    def save_config(self, cfg: JSONDict) -> None:
        self._carl.save_config(cfg)

    def launch_model(self, cfg: JSONDict) -> str:
        """The model a llama.cpp start with cfg loads (carl.resolve_launch)."""
        name: str = self._carl.resolve_launch(None, cfg)[0]["name"]
        return name

    def auto_fit(self, goal: str, scope: str) -> AutoFit:
        """Auto fit for this Mac with a goal (everyday / hard-code) and scope (catalogue / downloaded)."""
        fit: AutoFit = self._carl.auto_fit(goal, scope)
        return fit

    def catalog_default(self) -> str:
        name: str = self._carl.load_catalog()["default"]
        return name

    def models_dir(self) -> str:
        path: str = self._carl.models_dir()
        return path

    def parse_hf(self, spec: str) -> Tuple[str, Optional[str], Optional[str]]:
        repo, file, rev = self._carl.parse_hf(spec)
        return repo, file, rev

    def hf_files(self, repo: str) -> List[HFFile]:
        return [(str(f), b, sha) for f, b, sha in self._carl.hf_files(repo)]

    def delete(self, m: ModelInfo) -> None:
        self._carl.delete(m)

    def save_card(self, name: str, card: JSONDict) -> None:
        """Check and store a custom model's card (carl.ConfigError says what is wrong)."""
        self._carl.save_card(name, card)

    def client_models(self) -> JSONDict:
        """The installed models as the client configs list them (carl.client_models)."""
        doc: JSONDict = self._carl.client_models()
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
        self.fit_error: Optional[str] = None
        self._clients: Optional[Tuple[float, Optional[JSONDict]]] = None

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

    def client_list(self) -> Optional[JSONDict]:
        """The installed models for the client configs (re-read with the list; None when it
        can't be worked out)."""
        self.get()
        if self._clients is None or self._clients[0] != self.t:
            try:
                doc: Optional[JSONDict] = self.store.client_models()
            except Exception:           # a broken config / catalogue: the pasted config lists the served model
                doc = None
            self._clients = (self.t, doc)
        return self._clients[1]

    def _catalog_default(self) -> str:
        try:
            return self.store.catalog_default()
        except Exception:               # the catalogue itself can't be read (self.error says why)
            return "auto"
