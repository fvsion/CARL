"""Test helpers: puts tools/ on the import path and provides in-memory fakes of the ports
(no network, no subprocess, and no disk outside the adapter tests' temporary folders)."""
from __future__ import annotations

import copy
import os
import sys
from typing import Dict, List, Mapping, Optional, Tuple, cast

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

from carl_core.app import Carl, Stores  # noqa: E402
from carl_core.domain.gguf import ModelShape  # noqa: E402
from carl_core.domain.types import CatalogEntry, CtxZones, HfRef, JsonValue  # noqa: E402

GIB = 2 ** 30
HOME = "/home/u"
MDIR = "/home/u/models/gguf"
SHA_A = "a" * 64


def shape(experts: int = 0, nextn: int = 1, kv_elems: int = 8192, rs_bytes: int = 0, ctx_train: int = 262144,
          ftype: str = "Q4_K_M") -> ModelShape:
    """A model shape with round numbers: kv_elems per token, so q4_0 costs kv_elems * 18/32 bytes."""
    return {"arch": "qwen", "blocks": 48, "nextn": nextn, "attn_layers": 12, "rec_layers": 36,
            "kv_elems_per_token": kv_elems, "kv_elems_per_token_mtp": 0, "rs_bytes": rs_bytes, "experts": experts,
            "experts_used": 8 if experts else 0, "ctx_train": ctx_train, "ftype": ftype, "kvh": 4, "kl": 256,
            "vl": 256, "effort_levels": False, "thinking_switch": True}


def entry(name: str, file: str, size: int = 10 * GIB, arch: str = "moe", ctx: int = 98304,
          zones: Optional[CtxZones] = None, rank: Optional[int] = None, abliterated: bool = False) -> CatalogEntry:
    e: CatalogEntry = {
        "name": name, "label": name, "summary": f"{name} summary", "arch": arch,
        "abliterated": abliterated,
        "hf": {"repo": "owner/repo", "revision": "0" * 40, "file": file, "sha256": SHA_A, "bytes": size},
        "tune": {"kv": "q4_0", "ctx": ctx, "slots": "auto", "spec": "draft-mtp,ngram-mod", "spec_n": 2, "temp": 1.0},
        "ctx_zones": zones or {"good": 98304, "slow": 131072, "very_slow": 163840}}
    if rank is not None:
        e["rank"] = rank
    return e


def catalog(*entries: CatalogEntry, default: str = "big", default_small: str = "small") -> Dict[str, object]:
    return {"schema": 1, "default": default, "default_small": default_small,
            "models": [copy.deepcopy(e) for e in entries]}


class FakeFolder:
    """Files by path -> size."""

    def __init__(self, files: Optional[Dict[str, int]] = None, free: int = 100 * GIB) -> None:
        self.files: Dict[str, int] = dict(files or {})
        self.free = free
        self.hashes: Dict[str, str] = {}
        self.removed: List[str] = []

    def exists(self, path: str) -> bool:
        return path in self.files

    def size(self, path: str) -> int:
        return self.files[path]

    def gguf_names(self, directory: str) -> List[str]:
        return [os.path.basename(p) for p in self.files if os.path.dirname(p) == directory and p.endswith(".gguf")]

    def free_bytes(self, directory: str) -> Optional[int]:
        return self.free

    def make_dir(self, directory: str) -> None:
        pass

    def sha256(self, path: str) -> str:
        return self.hashes.get(path, SHA_A)

    def remove(self, path: str) -> None:
        if self.files.pop(path, None) is not None:
            self.removed.append(path)

    def rename(self, src: str, dst: str) -> None:
        self.files[dst] = self.files.pop(src)


class FakeShapes:
    def __init__(self, local: Optional[Dict[str, ModelShape]] = None, remote: Optional[Dict[str, ModelShape]] = None,
                 ) -> None:
        self.local_shapes = local or {}
        self.remote_shapes = remote or {}

    def local(self, path: str) -> ModelShape:
        if path not in self.local_shapes:
            raise OSError(f"no such file: {path}")
        return self.local_shapes[path]

    def remote(self, ref: HfRef) -> ModelShape:
        key = ref.get("file", "")
        if key not in self.remote_shapes:
            raise OSError("offline")
        return self.remote_shapes[key]


class FakeGpu:
    def __init__(self, limit: int) -> None:
        self.value = limit

    def limit(self) -> Tuple[int, str]:
        return self.value, "test"


class FakeHost:
    def __init__(self, ram: int = 32 * GIB, vm: bool = False) -> None:
        self.ram, self.vm = ram, vm

    def ram_bytes(self) -> int:
        return self.ram

    def vm_network_up(self) -> bool:
        return self.vm


class FakeDoc:
    """A JSON document in memory (copies in and out, like a file would)."""

    def __init__(self, doc: object = None) -> None:
        self.doc: object = copy.deepcopy(doc)
        self.saved: List[object] = []

    def load(self) -> Optional[JsonValue]:
        return cast(Optional[JsonValue], copy.deepcopy(self.doc))

    def save(self, doc: Mapping[str, object]) -> None:
        self.doc = copy.deepcopy(dict(doc))
        self.saved.append(self.doc)


class FakeLegacy:
    def __init__(self, llama: Optional[Dict[str, str]] = None) -> None:
        self.llama = llama or {}

    def read(self) -> Dict[str, str]:
        return dict(self.llama)


class FakeHub:
    def __init__(self, answers: Optional[Dict[str, JsonValue]] = None) -> None:
        self.answers = answers or {}
        self.asked: List[str] = []

    def get(self, api_path: str) -> JsonValue:
        self.asked.append(api_path)
        return copy.deepcopy(self.answers[api_path])


class FakeDownloader:
    def __init__(self, folder: FakeFolder, size: int, rc: int = 0) -> None:
        self.folder, self.size, self.rc = folder, size, rc
        self.calls: List[Tuple[str, str, str]] = []

    def fetch(self, url: str, directory: str, file_name: str) -> int:
        self.calls.append((url, directory, file_name))
        if self.rc == 0:
            self.folder.files[os.path.join(directory, file_name)] = self.size
        return self.rc


class FakeClock:
    def today(self) -> str:
        return "2026-10-03"


class FakeConsole:
    def __init__(self) -> None:
        self.lines: List[str] = []
        self.errors: List[str] = []

    def info(self, text: str) -> None:
        self.lines.append(text)

    def error(self, text: str) -> None:
        self.errors.append(text)


class World:
    """A Carl wired to fakes, with the fakes kept at hand for assertions."""

    def __init__(self, cat: object, files: Optional[Dict[str, int]] = None, config: object = None,
                 local: object = None, shapes: Optional[FakeShapes] = None, gpu: int = 24 * GIB,
                 legacy: Optional[FakeLegacy] = None, hub: Optional[FakeHub] = None, download_size: int = 0,
                 env_models_dir: Optional[str] = None, host: Optional[FakeHost] = None) -> None:
        self.folder = FakeFolder(files)
        self.config = FakeDoc(config)
        self.local = FakeDoc(local)
        self.console = FakeConsole()
        self.hub = hub or FakeHub()
        self.downloader = FakeDownloader(self.folder, download_size)
        stores = Stores(catalog=FakeDoc(cat), catalog_path="catalog.json", config=self.config, local=self.local,
                        local_path="models.json", legacy=legacy or FakeLegacy())
        self.host = host or FakeHost()
        self.carl = Carl(stores, self.folder, shapes or FakeShapes(), FakeGpu(gpu), self.host, self.hub,
                         self.downloader, FakeClock(), self.console, home=HOME, env_models_dir=env_models_dir)
