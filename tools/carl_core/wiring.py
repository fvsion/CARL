"""Composition root: where CARL's files are and which adapters implement the ports."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Mapping, Optional

from .adapters.console import StdConsole
from .adapters.downloader import Aria2OrCurl
from .adapters.filesystem import LocalModelFolder
from .adapters.gguf_reader import GgufShapes
from .adapters.huggingface import HfHttpClient
from .adapters.json_files import JsonFile, LegacyEnvFiles
from .adapters.system import MacGpuLimit, SystemClock
from .app import Carl, Stores
from .domain.confdir import conf_dir

TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(TOOLS_DIR)
SHAPE_CACHE = os.path.expanduser("~/models/.gguf-shapes.json")     # headers of files not downloaded
METAL_LIMIT_CACHE = os.path.expanduser("~/models/.metal-limit")
METAL_PROBE = os.path.join(TOOLS_DIR, "metal-limit.swift")

# Process-wide: header reads (64 MB each) and the GPU limit (a Swift probe) are cached.
SHAPES = GgufShapes(SHAPE_CACHE)
GPU = MacGpuLimit(METAL_PROBE, METAL_LIMIT_CACHE)


@dataclass(frozen=True)
class CarlPaths:
    catalog: str
    conf_dir: str

    @property
    def config(self) -> str:
        return os.path.join(self.conf_dir, "config.json")

    @property
    def local(self) -> str:
        return os.path.join(self.conf_dir, "models.json")

    @property
    def legacy_llama(self) -> str:            # before config.json (migrated once)
        return os.path.join(self.conf_dir, "llama.env")

    @classmethod
    def from_env(cls, env: Mapping[str, str], home: Optional[str] = None,
                 is_dir: Callable[[str], bool] = os.path.isdir) -> CarlPaths:
        """CARL_CATALOG and CARL_CONF_DIR override the defaults (tests use a temporary folder).
        Without CARL_CONF_DIR: ~/.config/carl, or its old name until it is moved (domain/confdir.py)."""
        return cls(catalog=env.get("CARL_CATALOG", os.path.join(REPO, "host", "catalog.json")),
                   conf_dir=conf_dir(home or os.path.expanduser("~"), env, is_dir))


def build_carl(paths: CarlPaths, env: Mapping[str, str]) -> Carl:
    stores = Stores(catalog=JsonFile(paths.catalog), catalog_path=paths.catalog, config=JsonFile(paths.config),
                    local=JsonFile(paths.local), local_path=paths.local,
                    legacy=LegacyEnvFiles(paths.legacy_llama))
    return Carl(stores, LocalModelFolder(), SHAPES, GPU, HfHttpClient(), Aria2OrCurl(), SystemClock(), StdConsole(),
                home=os.path.expanduser("~"), env_models_dir=env.get("MODELS_DIR") or None)
