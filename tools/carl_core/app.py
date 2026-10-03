"""Application services: the domain combined with the ports (stores, files, Hugging Face,
downloads). tools/carl.py builds one Carl with the real adapters (carl_core.wiring); tests
build it with fakes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .domain import models as dm
from .domain.errors import ConfigError
from .domain.fit import DEFAULT_CTX, choose_default, human_gb, need_bytes
from .domain.gguf import ModelShape
from .domain.hf import (commit_sha, download_url, gguf_files, local_file_name, model_name, parse_hf,
                        revision_api_path, tree_api_path, validate_repo)
from .domain.launch import launch_env as build_launch_env
from .domain.ports import (Clock, Console, Downloader, GpuLimit, HubClient, JsonDocument, LegacyEnv, ModelFolder,
                           ShapeReader)
from .domain.records import parse_catalog, parse_local_db
from .domain.settings import SCHEMA, Config, migrate_env, models_dir_setting, validate_config
from .domain.types import (Catalog, CustomInfo, HfFileList, HfRef, LocalDb, ModelInfo, SettingSource, SettingValue,
                           Settings)

DOWNLOAD_HEADROOM = 5e9                    # free disk space to keep beyond the file


@dataclass(frozen=True)
class Stores:
    """The JSON documents CARL reads and writes, and where they are (for messages)."""
    catalog: JsonDocument
    catalog_path: str
    config: JsonDocument
    local: JsonDocument
    local_path: str
    legacy: LegacyEnv


class Carl:
    """Models, settings and downloads for one user (one CONF_DIR and models folder)."""

    def __init__(self, stores: Stores, files: ModelFolder, shapes: ShapeReader, gpu: GpuLimit, hub: HubClient,
                 downloader: Downloader, clock: Clock, console: Console, home: str,
                 env_models_dir: Optional[str]) -> None:
        self.stores = stores
        self.files = files
        self.shapes = shapes
        self.gpu = gpu
        self.hub = hub
        self.downloader = downloader
        self.clock = clock
        self.console = console
        self.home = home
        self.env_models_dir = env_models_dir

    # ------------------------------------------------------------ config.json
    def load_config(self) -> Config:
        """config.json, validated; migrated once from llama.env when missing."""
        raw = self.stores.config.load()
        if raw is not None:
            return validate_config(raw)[0]
        migrated = migrate_env(self.stores.legacy.read())
        if migrated is None:
            return Config()
        return self.save_config(migrated)

    def config_warnings(self) -> List[str]:
        """What config.json holds that CARL ignores (unknown or removed keys)."""
        raw = self.stores.config.load()
        return validate_config(raw)[1] if raw is not None else []

    def save_config(self, cfg: Config) -> Config:
        self.stores.config.save(cfg.to_file())
        return cfg

    def parse_config(self, cfg: Optional[object]) -> Config:
        """A config passed in as a dict (the public API), or config.json when None."""
        return self.load_config() if cfg is None else validate_config(cfg)[0]

    def models_dir(self, cfg: Config) -> str:
        return self.expand(models_dir_setting(self.env_models_dir, cfg))

    def expand(self, path: str) -> str:
        return dm.expand_home(path, self.home)

    # ------------------------------------------------------------ catalogue + models.json
    def load_catalog(self) -> Catalog:
        return parse_catalog(self.stores.catalog.load(), self.stores.catalog_path)

    def load_local(self) -> LocalDb:
        return parse_local_db(self.stores.local.load(), self.stores.local_path)

    def save_local(self, db: LocalDb) -> None:
        db["schema"] = SCHEMA
        self.stores.local.save(db)

    # ------------------------------------------------------------ models
    def all_models(self, cfg: Config) -> List[ModelInfo]:
        return dm.build_models(self.load_catalog(), self.load_local(), self.models_dir(cfg), self.files, self.home)

    def find(self, name: str, models: List[ModelInfo]) -> Optional[ModelInfo]:
        return dm.find_model(models, name, self.expand(name))

    def local_shape(self, path: str) -> Optional[ModelShape]:
        """The header shape of a local file, None when it can't be read."""
        try:
            return self.shapes.local(path)
        except (OSError, ValueError):
            return None

    def custom_defaults(self, path: str) -> Tuple[Settings, CustomInfo]:
        return dm.custom_defaults(self.local_shape(path))

    def effective_tune(self, m: ModelInfo, cfg: Config) -> Tuple[Settings, Dict[str, SettingSource]]:
        header = None
        if m.get("custom") and m.get("status") == "downloaded":
            header = self.custom_defaults(m.get("path", ""))[0]
        return dm.effective_tune(m, cfg, header)

    def pick_default(self, models: List[ModelInfo], ctx: int = DEFAULT_CTX, limit: Optional[float] = None) -> str:
        """This Mac's default: the catalogue default, or default_small when the GPU can't
        hold one window of it (the header of a model not downloaded is read over HTTP)."""
        cat = self.load_catalog()
        default = cat.get("default", "")
        m = self.find(default, models)
        need: Optional[float] = None
        if m:
            try:
                hf: HfRef = m.get("hf") or {}
                shape = self.shapes.local(m.get("path", "")) if m.get("status") == "downloaded" else self.shapes.remote(hf)
                need = need_bytes(shape, m.get("bytes", 0), ctx)
            except (OSError, ValueError):
                need = None                   # header unavailable (offline): keep the default
        if need is None:
            return default
        return choose_default(default, cat.get("default_small"), need, self.gpu.limit()[0] if limit is None else limit)

    def resolve_launch(self, name: Optional[str], cfg: Config) -> Tuple[ModelInfo, List[ModelInfo], Optional[str]]:
        """The model a llama.cpp start uses: name, else config llama.model, else this Mac's
        default; a default that isn't downloaded falls back to the first downloaded model."""
        models = self.all_models(cfg)
        explicit = name or dm.configured_model(cfg)
        if explicit:
            return dm.select_named(models, explicit, self.expand(explicit)), models, None
        m, note = dm.select_default(models, self.pick_default(models))
        return m, models, note

    def model_file(self, name: Optional[str]) -> Optional[str]:
        """The absolute path when a start names a file rather than a model."""
        if not name or not ("/" in name or name.endswith(".gguf")):
            return None
        path = self.expand(name)
        return os.path.abspath(path) if self.files.exists(path) else None

    def launch_env(self, name: Optional[str], use_config: bool) -> Tuple[Dict[str, SettingValue], Optional[str]]:
        """Settings for serve-llama.sh (everything below flags and environment) and a note."""
        cfg = self.load_config() if use_config else Config()
        path = self.model_file(name)
        note: Optional[str] = None
        if path:
            m = self.find(path, self.all_models(self.load_config())) or dm.custom_entry(
                model_name(path), path, {"source": "file"}, self.files)
        else:
            m, _, note = self.resolve_launch(name, cfg)
        vals, src = self.effective_tune(m, cfg)
        advice = dm.tune_advice(m, vals, src)
        note = "\n".join(n for n in (note, advice) if n) or None
        return build_launch_env(m, vals, src, cfg), note

    # ------------------------------------------------------------ Hugging Face + downloads
    def hf_files(self, repo: str, revision: str = "main") -> HfFileList:
        """[(file, bytes, sha256)] of the GGUF files in a Hugging Face repo (first parts only)."""
        return gguf_files(self.hub.get(tree_api_path(validate_repo(repo), revision)))

    def verify(self, m: ModelInfo) -> bool:
        """Check a downloaded file against its pinned SHA-256."""
        name, path = m.get("name", ""), m.get("path", "")
        want = (m.get("hf") or {}).get("sha256")
        if m.get("status") != "downloaded" and not (path and self.files.exists(path)):
            self.console.error(f"  {name}: not downloaded")
            return False
        if not want:
            self.console.info(f"  {name}: no checksum known (a local file): skipped")
            return True
        self.console.info(f"  {name}: verifying sha256 ({human_gb(self.files.size(path))})...")
        got = self.files.sha256(path)
        if got != want:
            self.console.error(f"  {name}: MISMATCH got {got} want {want}")
            return False
        if m.get("custom"):                   # catalogue models are pinned in the repo already
            db = self.load_local()
            db["models"].setdefault(name, {})["verified"] = self.clock.today()
            self.save_local(db)
        self.console.info(f"  {name}: OK {got}")
        return True

    def download(self, m: ModelInfo, models_dir: str) -> bool:
        """Download a pinned file into the models folder (resumable), then verify it."""
        hf: HfRef = m.get("hf") or {}
        name, size = m.get("name", ""), hf.get("bytes", 0)
        file = local_file_name(hf.get("file", ""))
        path = os.path.join(models_dir, file)
        self.files.make_dir(models_dir)
        done: ModelInfo = {**m, "path": path, "status": "downloaded"}
        if dm.status_of(path, size, self.files) == "downloaded":
            self.console.info(f"== {name} already downloaded: {path}")
            return self.verify(done)
        free = self.files.free_bytes(models_dir) or 0
        have = self.files.size(path) if self.files.exists(path) else 0
        if free < size + DOWNLOAD_HEADROOM - have:
            self.console.error(f"error: {name} needs {human_gb(size)} + 5 GB headroom; only {human_gb(free)} free")
            return False
        self.console.info(f"== {name}: {human_gb(size)} -> {path}")
        rc = self.downloader.fetch(download_url(hf), models_dir, file)
        if rc != 0 or not self.files.exists(path):
            self.console.error(f"error: download failed (exit {rc}); run it again to resume")
            return False
        if size and self.files.size(path) != size:
            self.console.error(f"error: size mismatch for {path}")
            return False
        ok = self.verify(done)
        if not ok:
            self.files.rename(path, path + dm.BAD_SUFFIX)
            self.console.error(f"error: bad checksum; moved to {path}{dm.BAD_SUFFIX}")
        return ok

    def download_hf(self, spec: str, name: Optional[str] = None) -> bool:
        """Any GGUF from Hugging Face: pin the revision, size and sha256, record it in
        models.json, download. A repo without a file lists its GGUF files instead."""
        repo, file, rev = parse_hf(spec)
        if not file:
            self.console.info(f"{repo}: pick a file (./carl.sh download hf:{repo}/FILE.gguf):")
            for f, b, _ in self.hf_files(repo, rev):
                self.console.info(f"  {f:60} {human_gb(b)}")
            return False
        pinned = commit_sha(self.hub.get(revision_api_path(repo, rev)), rev)
        match = [x for x in self.hf_files(repo, pinned) if x[0] == file]
        if not match:
            raise ConfigError(f"{repo} has no file {file}")
        _, size, sha = match[0]
        name = name or model_name(file)
        mdir = self.models_dir(self.load_config())
        ref: HfRef = {"repo": repo, "revision": pinned, "file": file, "sha256": sha, "bytes": size}
        db = self.load_local()
        entry = db["models"].setdefault(name, {})
        entry.update({"source": "hf", "hf": ref, "path": os.path.join(mdir, local_file_name(file)),
                      "added": self.clock.today()})
        self.save_local(db)
        return self.download({"name": name, "hf": ref}, mdir)

    def delete(self, m: ModelInfo) -> None:
        """Delete a model's file (and its part/bad copies) and forget its verification."""
        path = m.get("path", "")
        if not path.endswith(".gguf"):
            raise ConfigError(f"{m.get('name')}: refusing to delete {path!r} (not a .gguf file)")
        for p in (path, path + dm.PART_SUFFIX, path + dm.BAD_SUFFIX):
            self.files.remove(p)
        db = self.load_local()
        name = m.get("name", "")
        if m.get("custom"):
            db["models"].pop(name, None)
        elif name in db["models"]:
            db["models"][name].pop("verified", None)
        self.save_local(db)
