"""Shared test helpers for the dashboard: tools/ on the import path and an in-memory
ModelStore (no disk, no network, no tools/carl.py)."""
from __future__ import annotations

import copy
import os
import sys
from typing import Dict, List, Optional, Tuple

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools"))

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


class FakeStore:
    """ModelStore in memory. config holds config.json; saved collects every save. broken:
    the catalogue / models.json can't be read (every model call raises it, like carl.py does)."""

    def __init__(self, models: Optional[List[ModelInfo]] = None, config: Optional[JSONDict] = None,
                 limit: int = 25 * GIB) -> None:
        self.models = models if models is not None else [model("big"), model("iq", quant="UD-IQ3_XXS"),
                                                        model("nomtp", mtp=False), model("remote", status="missing")]
        self.config: JSONDict = config if config is not None else {"schema": 1}
        self.saved: List[JSONDict] = []
        self.deleted: List[str] = []
        self.limit = limit
        self.config_file = "/home/u/.config/llm-deploy/config.json"
        self.conf_dir = "/home/u/.config/llm-deploy"
        self.broken: Optional[str] = None

    def _check(self) -> None:
        if self.broken:
            raise ValueError(self.broken)

    def all_models(self) -> List[ModelInfo]:
        self._check()
        return list(self.models)

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
        return {"mtp": m.get("mtp"), "quant": m.get("quant")}

    def shape_of(self, path: str) -> Shape:
        return shape()

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
