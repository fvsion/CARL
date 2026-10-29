"""GGUF headers from local files and from Hugging Face (implements ShapeReader)."""
from __future__ import annotations

import os
import urllib.request
from typing import Dict, Tuple

from ..domain.gguf import LOCAL_HEADER_BYTES, META_VERSION, REMOTE_HEADER_BYTES, Meta, ModelShape, model_shape, parse_meta
from ..domain.hf import download_url, validate_file, validate_repo, validate_revision
from ..domain.types import HfRef
from .json_files import read_json, write_json

REMOTE_TIMEOUT = 60


def local_meta(path: str) -> Meta:
    with open(path, "rb") as f:
        return parse_meta(f.read(LOCAL_HEADER_BYTES))


def _cached_metas(cache_file: str) -> Dict[str, Meta]:
    """The header cache; a missing or damaged cache is just empty."""
    try:
        raw = read_json(cache_file)
    except ValueError:
        return {}
    out: Dict[str, Meta] = {}
    if isinstance(raw, dict):
        for key, meta in raw.items():
            if isinstance(meta, dict):
                out[key] = {k: v for k, v in meta.items() if isinstance(v, (int, float, bool, str))}
    return out


def remote_meta(ref: HfRef, cache_file: str) -> Meta:
    """Header of a not-yet-downloaded file (the first 24 MB via an HTTP range), cached:
    a pinned revision's header never changes."""
    repo, rev, file = validate_repo(ref.get("repo", "")), validate_revision(ref.get("revision") or "main"), \
        validate_file(ref.get("file", ""))
    key = f"{repo}@{rev}/{file}#{META_VERSION}"           # a new version: what parse_meta keeps changed
    db = _cached_metas(cache_file)
    if key not in db:
        req = urllib.request.Request(download_url(ref), headers={"Range": f"bytes=0-{REMOTE_HEADER_BYTES - 1}",
                                                                  "User-Agent": "carl"})
        with urllib.request.urlopen(req, timeout=REMOTE_TIMEOUT) as r:
            db[key] = parse_meta(r.read(REMOTE_HEADER_BYTES))
        write_json(cache_file, db)
    return db[key]


class GgufShapes:
    """Shapes by path, cached per (path, mtime): a local header read is 64 MB."""

    def __init__(self, remote_cache: str) -> None:
        self.remote_cache = remote_cache
        self._local: Dict[Tuple[str, float], ModelShape] = {}

    def local(self, path: str) -> ModelShape:
        key = (path, os.path.getmtime(path))
        if key not in self._local:
            self._local[key] = model_shape(local_meta(path))
        return self._local[key]

    def remote(self, ref: HfRef) -> ModelShape:
        return model_shape(remote_meta(ref, self.remote_cache))
