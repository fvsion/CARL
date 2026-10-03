"""Shared test helpers for the dashboard: tools/ on the import path and an in-memory
ModelStore (no disk, no network, no tools/carl.py)."""
from __future__ import annotations

import copy
import os
import sys
from typing import Dict, List, Optional, Tuple

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools"))

from carl_core.domain.autofit import AutoFit, Budget, Candidate, Plan  # noqa: E402
from carl_core.domain.cards import apply_card  # noqa: E402
from carl_core.domain.errors import ConfigError  # noqa: E402
from carl_core.domain.records import parse_custom_card  # noqa: E402
from monitor.model import JSONDict, ModelInfo, Shape  # noqa: E402
from monitor.store import HFFile, ModelList  # noqa: E402

GIB = 2 ** 30
TUNE = {"kv": "q4_0", "ctx": 98304, "slots": "auto", "spec": "draft-mtp,ngram-mod", "spec_n": 1, "temp": 1.0,
        "presence": 0, "top_k": 20, "top_p": 0.95, "min_p": 0, "repeat": 1.0, "alias": None}


def shape(kv_elems: int = 10240, rs_bytes: int = 64 * 2**20, ctx_train: int = 262144, nextn: int = 1) -> Shape:
    """A model shape with round numbers: q4_0 KV costs kv_elems * 18/32 bytes per token."""
    return {"arch": "qwen35moe", "blocks": 41, "nextn": nextn, "attn_layers": 10, "rec_layers": 30,
            "kv_elems_per_token": kv_elems, "kv_elems_per_token_mtp": 1024, "rs_bytes": rs_bytes, "experts": 256,
            "experts_used": 8, "ctx_train": ctx_train, "ftype": "IQ3_S", "kvh": 2, "kl": 256, "vl": 256,
            "effort_levels": False, "thinking_switch": True}


def model(name: str, status: str = "downloaded", quant: str = "Q4_K_M", mtp: bool = True, size: int = 13 * GIB,
          tune: Optional[JSONDict] = None) -> ModelInfo:
    return {"name": name, "label": name.upper(), "source": "catalog", "path": f"/m/{name}.gguf", "bytes": size,
            "status": status, "summary": f"{name} summary", "description": f"{name} description", "arch": "moe",
            "quant": quant, "mtp": mtp, "tune": dict(TUNE, **(tune or {})), "local": {}, "why": {"kv": "q4_0 measured best"},
            "hf": {"repo": "o/r", "file": f"{name}.gguf"}}


def custom(name: str, size: int = 5 * GIB) -> ModelInfo:
    """A model from the models folder (no card, no catalogue fields)."""
    return {"name": name, "label": f"{name}.gguf", "source": "file", "path": f"/m/{name}.gguf", "bytes": size,
            "status": "downloaded", "summary": "Custom model (found in the models folder)", "description": "",
            "tune": {}, "local": {"source": "file"}, "why": {}, "hf": {}, "custom": True}


class FakeStore:
    """ModelStore in memory. config holds config.json; saved collects every save. broken:
    the catalogue / models.json can't be read (every model call raises it, like carl.py does)."""

    def __init__(self, models: Optional[List[ModelInfo]] = None, config: Optional[JSONDict] = None,
                 limit: int = 25 * GIB) -> None:
        self.models = models if models is not None else [model("big"), model("iq", quant="UD-IQ3_XXS"),
                                                        model("nomtp", mtp=False), model("remote", status="missing")]
        self.config: JSONDict = config if config is not None else {"schema": 1}
        self.saved: List[JSONDict] = []
        self.cards: Dict[str, JSONDict] = {}            # save_card: the user's cards, joined in by all_models
        self.deleted: List[str] = []
        self.limit = limit
        self.config_file = "/home/u/.config/carl/config.json"
        self.conf_dir = "/home/u/.config/carl"
        self.broken: Optional[str] = None
        self.fit_calls: List[Tuple[str, str]] = []
        # auto fit's answer: (pick, downloaded); None = nothing fits
        self.pick: Optional[Tuple[str, bool]] = ("big", True)

    def _check(self) -> None:
        if self.broken:
            raise ValueError(self.broken)

    def all_models(self) -> List[ModelInfo]:
        self._check()
        out: List[ModelInfo] = []
        names = {m["name"] for m in self.models}
        for m in self.models:
            if m["name"] in self.cards:
                m = copy.deepcopy(m)
                m["local"] = dict(m.get("local") or {}, card=self.cards[m["name"]])
                apply_card(m, self.cards[m["name"]], names)
            out.append(m)
        return out

    def save_card(self, name: str, card: JSONDict) -> None:
        """Like carl.save_card: catalogue models are refused, the card is checked by the domain."""
        m = next(x for x in self.models if x["name"] == name)
        if not m.get("custom"):
            raise ConfigError(f"{name} is a catalogue model: its card is read-only (host/catalog.json)")
        self.cards[name] = dict(parse_custom_card(copy.deepcopy(card), {x["name"] for x in self.models},
                                                  f"{name}: card", name))

    def find(self, name: str, models: List[ModelInfo]) -> Optional[ModelInfo]:
        return next((m for m in models if m["name"] == name or os.path.basename(m["path"]) == name), None)

    def builtin_tune(self) -> JSONDict:
        return dict(TUNE)

    def effective_tune(self, m: ModelInfo, cfg: JSONDict) -> Tuple[JSONDict, Dict[str, str]]:
        vals = dict(TUNE, **m.get("tune", {}))
        src = {k: "catalogue" for k in vals}
        for k, v in ((cfg.get("models") or {}).get(m["name"]) or {}).items():
            vals[k], src[k] = v, "config"
        return vals, src

    def ctx_zones(self, m: ModelInfo) -> Tuple[int, int, int]:
        return 65536, 98304, 131072

    def ctx_zone(self, m: ModelInfo, ctx: int) -> str:
        good, slow, _ = self.ctx_zones(m)
        return "good" if ctx <= good else "slow" if ctx <= slow else "very_slow"

    def header_info(self, path: str) -> JSONDict:
        m = next(x for x in self.models if x["path"] == path)
        if m.get("custom"):
            return {"arch": "moe", "mtp": False, "quant": "Q4_K_M"}
        return {"mtp": m.get("mtp"), "quant": m.get("quant")}

    def shape_of(self, path: str) -> Shape:
        return shape()

    def model_shape(self, m: ModelInfo) -> Optional[Shape]:
        return shape() if m["status"] == "downloaded" or m.get("source") == "catalog" else None

    def auto_fit(self, goal: str, scope: str) -> AutoFit:
        """A pick with 2 x 96K q4_0 (or nothing), one better model passed over."""
        self._check()
        self.fit_calls.append((goal, scope))
        budget = Budget(self.limit, 32 * GIB, 6 * GIB)
        g = "hard-code" if goal == "hard-code" else "everyday"
        sc = "downloaded" if scope == "downloaded" else "catalogue"
        if self.pick is None:
            return AutoFit(g, sc, budget, None, None, 2, False, ())
        name, here = self.pick
        pick = Candidate(name, "moe", 2, False, here, 13 * GIB, shape())
        from carl_core.domain.autofit import Rejection
        return AutoFit(g, sc, budget, pick, Plan(98304, 2, "q4_0", 20 * GIB), 0, False,
                       (Rejection("better", 1, "needs 30.0 GiB for 2 × 96K, this Mac allows 25.0 GiB"),))

    def file_size(self, path: str) -> int:
        return next(x["bytes"] for x in self.models if x["path"] == path)

    def gpu_limit(self) -> Tuple[int, str]:
        return self.limit, "test"

    def empty_config(self) -> JSONDict:
        return {"schema": 1}

    def load_config(self) -> JSONDict:
        return copy.deepcopy(self.config)

    def save_config(self, cfg: JSONDict) -> None:
        self.config = copy.deepcopy(cfg)
        self.saved.append(copy.deepcopy(cfg))

    def launch_model(self, cfg: JSONDict) -> str:
        self._check()
        return "big"

    def catalog_default(self) -> str:
        self._check()
        return "big"

    def models_dir(self) -> str:
        return "/m"

    def parse_hf(self, spec: str) -> Tuple[str, Optional[str], Optional[str]]:
        s = spec[3:] if spec.startswith("hf:") else spec
        parts = s.split("/")
        return "/".join(parts[:2]), ("/".join(parts[2:]) or None), "main"

    def hf_files(self, repo: str) -> List[HFFile]:
        return [("a.gguf", 100, "f" * 64)]

    def delete(self, m: ModelInfo) -> None:
        self.deleted.append(m["name"])


def model_list(store: Optional[FakeStore] = None) -> ModelList:
    errors: List[str] = []
    ml = ModelList(store or FakeStore(), errors.append, clock=lambda: 100.0)
    return ml
