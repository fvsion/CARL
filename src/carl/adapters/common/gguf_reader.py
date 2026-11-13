"""GGUF headers from local files and from Hugging Face (implements ShapeReader)."""
from __future__ import annotations

import os
import urllib.request
from typing import Dict, Optional, Tuple

from ...domain.memory.gguf import LOCAL_HEADER_BYTES, META_VERSION, REMOTE_HEADER_BYTES, Meta, ModelShape, model_shape, parse_meta
from ...domain.catalog.hf import download_url, validate_file, validate_repo, validate_revision
from ...domain.shared.types import HfRef
from .hf_auth import USER_AGENT, HfAuth
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


def _cache_key(ref: HfRef) -> str:
    repo, rev, file = validate_repo(ref.get("repo", "")), validate_revision(ref.get("revision") or "main"), \
        validate_file(ref.get("file", ""))
    return f"{repo}@{rev}/{file}#{META_VERSION}"          # a new version: what parse_meta keeps changed


def remote_meta(ref: HfRef, cache_file: str, auth: Optional[HfAuth] = None) -> Meta:
    """Header of a not-yet-downloaded file (the first 24 MB via an HTTP range), cached:
    a pinned revision's header never changes."""
    key = _cache_key(ref)
    db = _cached_metas(cache_file)
    if key not in db:
        db[key] = parse_meta(remote_head(ref, auth))
        write_json(cache_file, db)
    return db[key]


def remote_head(ref: HfRef, auth: Optional[HfAuth] = None) -> bytes:
    """The first 24 MB of a file on Hugging Face (an HTTP range read): its header and tensor table. With the user's
    token (auth) the request carries it, as every call to Hugging Face does, so a gated repo answers (decision A75).
    Raises OSError (a failed read; a 401 or 403 is an HTTPError) or ValueError (a reference that does not validate:
    download_url checks it)."""
    url = download_url(ref)
    extra = {"Range": f"bytes=0-{REMOTE_HEADER_BYTES - 1}"}
    if auth is not None and auth.gets_token(url) and auth.token():
        r = auth.open(url, REMOTE_TIMEOUT, extra)
    else:
        r = urllib.request.urlopen(urllib.request.Request(url, headers={**extra, "User-Agent": USER_AGENT}),
                                   timeout=REMOTE_TIMEOUT)
    with r:
        return bytes(r.read(REMOTE_HEADER_BYTES))


class GgufShapes:
    """Shapes by path, cached per (path, mtime): a local header read is 64 MB."""

    def __init__(self, remote_cache: str, auth: Optional[HfAuth] = None) -> None:
        self.remote_cache = remote_cache
        self.auth = auth
        self._local: Dict[Tuple[str, float], ModelShape] = {}
        self._cache: Tuple[float, Dict[str, Meta]] = (-1.0, {})     # the cache file read, by its mtime

    def local(self, path: str) -> ModelShape:
        key = (path, os.path.getmtime(path))
        if key not in self._local:
            self._local[key] = model_shape(local_meta(path))
        return self._local[key]

    def remote(self, ref: HfRef) -> ModelShape:
        return model_shape(remote_meta(ref, self.remote_cache, self.auth))

    def cached(self, ref: HfRef) -> Optional[ModelShape]:
        """The shape of a cached remote header, else None; no network. The cache file is read again when it changes."""
        try:
            stamp = os.path.getmtime(self.remote_cache)
            if self._cache[0] != stamp:
                self._cache = (stamp, _cached_metas(self.remote_cache))
            meta = self._cache[1].get(_cache_key(ref))
        except (OSError, ValueError):
            return None
        return model_shape(meta) if meta is not None else None

    def remote_head(self, ref: HfRef) -> bytes:
        return remote_head(ref, self.auth)
