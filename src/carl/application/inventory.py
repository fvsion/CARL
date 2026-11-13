"""The models CARL knows: the catalogue, models.json and the models folder, with each model's header shape and its
effective settings. Reading the list adopts custom models a catalogue entry took over, once (it writes then).
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple, cast

from ..domain.catalog import inventory as dm, layout
from ..domain.catalog.drafters import matching_drafter
from ..domain.catalog.hf import local_file_name
from ..domain.catalog.records import parse_catalog, parse_local_db
from ..domain.config.settings import SCHEMA, Config, migrate_config
from ..domain.memory.gguf import ModelShape
from ..domain.memory.shape import has_head, not_language_model_arch
from ..domain.shared.contracts import require
from ..domain.shared.types import Catalog, CustomInfo, LocalDb, ModelInfo, SettingSource, Settings
from ..domain.shared.units import file_size
from ..ports import Console, ModelFolder, ShapeReader
from .config import ConfigService


class Inventory:
    """The model list of one user, and what the GGUF headers say about each model. Contract C-101."""

    def __init__(self, config: ConfigService, files: ModelFolder, shapes: ShapeReader, console: Console) -> None:
        self.config = config
        self.files = files
        self.shapes = shapes
        self.console = console

    # ------------------------------------------------------------ catalogue + models.json
    def load_catalog(self) -> Catalog:
        stores = self.config.stores
        return parse_catalog(stores.catalog.load(), stores.catalog_path, self.config.words)

    def load_local(self) -> LocalDb:
        stores = self.config.stores
        return parse_local_db(stores.local.load(), stores.local_path)

    def save_local(self, db: LocalDb) -> None:
        require(isinstance(db, dict) and isinstance(db.get("models", {}), dict), "C-101",
                "models.json is an object whose models are an object")
        db["schema"] = SCHEMA
        self.config.stores.local.save(db)

    # ------------------------------------------------------------ models
    def all_models(self, cfg: Config) -> List[ModelInfo]:
        home = self.config.home
        cat, db, mdir = self.load_catalog(), self.load_local(), self.config.models_dir(cfg)
        renames = dm.adoptions(cat, db, mdir, home)
        if renames:
            self.adopt(db, renames)
        models = [m for m in dm.build_models(cat, db, mdir, self.files, home) if not self.not_language_model(m)]
        for m in models:                    # a custom Gemma 4 file: offer the catalogue drafter of its size
            if m.get("custom") and m.get("status") == "downloaded" and not m.get("draft"):
                match = matching_drafter(self.local_shape(m.get("path", "")), cat)
                if match:
                    m["draft_for"], m["draft_offer"] = match
        return models

    def mark_heads(self, models: List[ModelInfo]) -> List[ModelInfo]:
        """Set ModelInfo.head of each model from its header (A73): the file's when it is downloaded, else the header
        that a read of Hugging Face cached (never a new read); a model whose header is not there keeps the catalogue's
        word (quants.mtp_kind). It reads the headers of the downloaded files (a few MB each, kept by path and mtime), so
        the lists a person reads call it, not every start. The models, marked."""
        for m in models:
            try:
                shape = (self.shapes.local(m.get("path", "")) if m.get("status") == "downloaded" else
                         self.shapes.cached(m.get("hf") or {}) if m.get("source") == "catalog" and m.get("hf")
                         else None)
            except (OSError, ValueError):
                shape = None
            if shape is not None:
                m["head"] = has_head(shape)
        return models

    def not_language_model(self, m: ModelInfo) -> bool:
        """A file that is not a catalogue model and whose GGUF architecture is not a language model's (bert,
        nomic-bert ...: it embeds or classifies text): never listed as a model (decision A68)."""
        if m.get("source") == "catalog" or m.get("status") != "downloaded":
            return False
        shape = self.local_shape(m.get("path", ""))
        return shape is not None and not_language_model_arch(str(shape.get("arch", "")))

    def add_drafter(self, m: ModelInfo) -> ModelInfo:
        """Record the offered drafter (draft_offer) in the custom model's models.json entry, so start,
        fit, verify, delete and Auto-tune use it; the model with its draft fields."""
        offer = m.get("draft_offer")
        if not offer:
            return m
        db = self.load_local()
        entry = db["models"].setdefault(m.get("name", ""), {})
        entry.setdefault("path", m.get("path", ""))
        entry.setdefault("source", m.get("source", "file"))
        entry["draft"] = offer
        self.save_local(db)
        dpath = layout.locate_beside(m.get("path", ""), local_file_name(offer.get("file", "")), self.files)
        out: ModelInfo = {**m, "draft": offer, "draft_path": dpath,
                          "draft_status": dm.status_of(dpath, offer.get("bytes"), self.files)}
        out.pop("draft_offer", None)
        self.console.info(f"{m.get('name')} is a Gemma 4 model of the same size as {m.get('draft_for')}. CARL uses "
                          f"the MTP drafter of {m.get('draft_for')} for it ({local_file_name(offer.get('file', ''))}, "
                          f"{file_size(offer.get('bytes'))}). Auto-tune measures if it makes this model faster.")
        return out

    def adopt(self, db: LocalDb, renames: Dict[str, str]) -> None:
        """A catalogue entry took over custom models: their records and config.json profiles
        (and llama.model) move to the catalogue name, once, with a note on stderr."""
        for note in dm.adopt(db, renames):
            self.console.error(f"note: {note}")
        self.save_local(db)
        raw = self.config.stores.config.load()
        if raw is None:
            return
        cfg = self.config.validate(migrate_config(raw, self.config.words)[0])[0]
        changed = False
        for old, new in renames.items():
            if old in cfg.models:
                prof = cfg.models.pop(old)
                cfg.models.setdefault(new, prof)
                changed = True
            if cfg.llama.get("model") == old:
                cfg.llama["model"] = new
                changed = True
        if changed:
            self.config.save_config(cfg)
            self.console.error("note: config.json now uses their catalogue names.")

    def find(self, name: str, models: List[ModelInfo]) -> Optional[ModelInfo]:
        return dm.find_model(models, name, self.config.expand(name))

    def model_file(self, name: Optional[str]) -> Optional[str]:
        """The absolute path when a start names a file rather than a model."""
        if not name or not ("/" in name or name.endswith(".gguf")):
            return None
        path = self.config.expand(name)
        return os.path.abspath(path) if self.files.exists(path) else None

    # ------------------------------------------------------------ headers and settings
    def local_shape(self, path: str) -> Optional[ModelShape]:
        """The header shape of a local file, None when it can't be read."""
        try:
            return self.shapes.local(path)
        except (OSError, ValueError):
            return None

    def shape_of(self, m: ModelInfo) -> Optional[ModelShape]:
        """A model's header shape: the local file's, or (a catalogue model not downloaded)
        the one on Hugging Face (read once, then cached); None when it can't be read."""
        try:
            if m.get("status") == "downloaded":
                return self.shapes.local(m.get("path", ""))
            if m.get("source") == "catalog" and m.get("hf"):
                return self.shapes.remote(m.get("hf") or {})
        except (OSError, ValueError):
            pass
        return None

    def remote_head(self, m: ModelInfo) -> bytes:
        """The first bytes of a catalogue model's file on Hugging Face (its header and tensor table: the stub of A67).
        Raises OSError or ValueError."""
        if not m.get("hf"):
            raise ValueError(f"{m.get('name', '')} has no file on Hugging Face")
        return self.shapes.remote_head(m.get("hf") or {})

    def custom_defaults(self, path: str, drafter: bool = False) -> Tuple[Settings, CustomInfo]:
        return dm.custom_defaults(self.local_shape(path), drafter)

    def effective_tune(self, m: ModelInfo, cfg: Config) -> Tuple[Settings, Dict[str, SettingSource]]:
        header = None
        if m.get("custom") and m.get("status") == "downloaded":
            header = self.custom_defaults(m.get("path", ""), bool(m.get("draft")))[0]
        return dm.effective_tune(m, cfg, header)

    def with_thinking(self, m: ModelInfo) -> ModelInfo:
        """A downloaded custom model without a card "thinking": its GGUF header says (a template
        with effort levels, or thinking on / off only)."""
        if m.get("thinking") or m.get("status") != "downloaded":
            return m
        shape = self.local_shape(m.get("path", ""))
        if shape is None:
            return m
        out = cast(ModelInfo, dict(m))
        out["thinking"] = "effort" if shape.get("effort_levels") else "on-off"
        return out
