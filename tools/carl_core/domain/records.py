"""Validation of the catalogue (host/catalog.json) and models.json as they are loaded.

The documents are kept as they are (unknown fields survive a load/save round trip); only
the fields CARL acts on are checked: names, Hugging Face references (they become URLs and
paths), file paths, tune settings and context zones.
"""
from __future__ import annotations

import re
from typing import Dict, cast

from .errors import ConfigError
from .hf import local_file_name, validate_ref
from .settings import MODEL_KEYS, SCHEMA, coerce
from .types import Catalog, JsonValue, LocalDb

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def validate_name(name: object, where: str) -> str:
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ConfigError(f"{where}: {name!r} is not a model name (letters, digits, '.', '_', '-')")
    return name


def _settings(raw: object, where: str) -> None:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: must be an object")
    for k, v in raw.items():
        if k in MODEL_KEYS:
            coerce(MODEL_KEYS[k], v, f"{where}.{k}")


def _zones(raw: object, where: str) -> None:
    if raw is None:
        return
    if not isinstance(raw, dict) or not all(isinstance(raw.get(k), int) for k in ("good", "slow", "very_slow")):
        raise ConfigError(f"{where}: needs good, slow and very_slow token counts")


def _object(raw: object, where: str) -> Dict[str, JsonValue]:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: must be an object")
    return raw


def parse_catalog(raw: object, where: str) -> Catalog:
    """The catalogue, checked. Raises ConfigError naming the bad entry."""
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        raise ConfigError(f"{where}: missing or wrong schema")
    doc: Dict[str, JsonValue] = raw
    models = doc.get("models")
    if not isinstance(models, list) or not isinstance(doc.get("default"), str):
        raise ConfigError(f"{where}: needs a models list and a default")
    for i, m in enumerate(models):
        entry = _object(m, f"{where}: models[{i}]")
        name = validate_name(entry.get("name"), f"{where}: models[{i}].name")
        at = f"{where}: {name}"
        ref = validate_ref(entry.get("hf"), at)
        if "repo" not in ref or "file" not in ref or "bytes" not in ref:
            raise ConfigError(f"{at}: hf needs repo, file and bytes")
        local_file_name(ref["file"])
        _settings(entry.get("tune", {}), f"{at}: tune")
        _zones(entry.get("ctx_zones"), f"{at}: ctx_zones")
    return cast(Catalog, doc)


def parse_local_db(raw: object, where: str) -> LocalDb:
    """models.json, checked ({"schema": 1, "models": {}} when there is none yet)."""
    if raw is None:
        return {"schema": SCHEMA, "models": {}}
    doc = _object(raw, where)
    models = _object(doc.setdefault("models", {}), f"{where}: models")
    for name, e in models.items():
        at = f"{where}: {name}"
        entry = _object(e, at)
        path = entry.get("path")
        if path is not None and (not isinstance(path, str) or "\x00" in path):
            raise ConfigError(f"{at}: path must be a file path")
        if "hf" in entry:
            validate_ref(entry["hf"], at)
        tune = entry.get("tune")
        if tune is not None:
            t = _object(tune, f"{at}: tune")
            _settings(t.get("settings", {}), f"{at}: tune.settings")
            _zones(t.get("ctx_zones"), f"{at}: tune.ctx_zones")
    return cast(LocalDb, doc)

