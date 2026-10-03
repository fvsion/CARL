"""Hugging Face references: parsing user specs, validating repo/file/revision names, URLs.

Everything here is untrusted input (typed by the user, or read from the Hugging Face API)
that ends up in URLs and in a path inside the models folder, so it is validated strictly.
"""
from __future__ import annotations

import posixpath
import re
from typing import Optional, Tuple
from urllib.parse import quote, unquote

from .errors import ConfigError
from .types import HfFileList, HfRef

HF_BASE = "https://huggingface.co"
# Hugging Face repo ids: owner/name, letters, digits, '-', '_', '.', at most 96 characters each.
_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}")
# A branch, tag or commit; '/' allowed for refs like refs/pr/1.
_REVISION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_URL = re.compile(r"https?://huggingface\.co/([^/]+/[^/]+)/(?:blob|resolve)/([^/]+)/(.+?)(?:\?.*)?$")
# 2nd+ parts of split GGUFs (the first part loads the rest) and vision projectors are not models.
_SPLIT_PART = re.compile(r"-0000[2-9]-of-\d+\.gguf$|-000[1-9]\d-of-\d+\.gguf$")
_NAME_CHARS = re.compile(r"[^a-z0-9._-]+")


def is_extra_part(file_name: str) -> bool:
    """True for files that are not a model on their own: mmproj files and 2nd+ split parts."""
    return file_name.lower().startswith("mmproj") or bool(_SPLIT_PART.search(file_name))


def model_name(file_name: str) -> str:
    """The CARL name of a GGUF file: its base name without .gguf, lower case, safe characters."""
    base = posixpath.basename(file_name)
    stem = base[:-5] if base.lower().endswith(".gguf") else base
    return _NAME_CHARS.sub("-", stem.lower())


def validate_repo(repo: str) -> str:
    if not _REPO.fullmatch(repo) or ".." in repo:
        raise ConfigError(f"not a Hugging Face repo: {repo!r} (use OWNER/REPO)")
    return repo


def validate_revision(revision: str) -> str:
    if not _REVISION.fullmatch(revision) or ".." in revision.split("/"):
        raise ConfigError(f"not a Hugging Face revision: {revision!r}")
    return revision


def validate_file(file: str) -> str:
    """A file path inside a repo: relative, no '.'/'..' parts, no backslashes or control characters."""
    parts = file.split("/")
    if (not file or file.startswith("/") or "\\" in file or any(ord(c) < 32 for c in file)
            or any(p in ("", ".", "..") for p in parts)):
        raise ConfigError(f"not a file name in a Hugging Face repo: {file!r}")
    return file


def validate_sha256(sha: str) -> str:
    """A SHA-256 in hex, or "" when unknown."""
    if sha and not _SHA256.fullmatch(sha):
        raise ConfigError(f"not a SHA-256: {sha!r}")
    return sha


def local_file_name(file: str) -> str:
    """The name a repo file gets in the models folder (its base name, never a path)."""
    name = posixpath.basename(validate_file(file))
    if not name.endswith(".gguf"):
        raise ConfigError(f"not a GGUF file: {file!r}")
    return name


def parse_hf(spec: str) -> Tuple[str, Optional[str], str]:
    """hf:OWNER/REPO/FILE.gguf, OWNER/REPO/FILE.gguf, OWNER/REPO, or a huggingface.co
    URL -> (repo, file or None, revision)."""
    s = spec[3:] if spec.startswith("hf:") else spec
    m = _URL.match(s)
    if m:
        return validate_repo(m.group(1)), validate_file(unquote(m.group(3))), validate_revision(m.group(2))
    parts = s.split("/")
    if len(parts) >= 3 and s.endswith(".gguf"):
        return validate_repo("/".join(parts[:2])), validate_file("/".join(parts[2:])), "main"
    if len(parts) == 2:
        return validate_repo(s), None, "main"
    raise ConfigError(f"not a Hugging Face model: {spec} (use hf:OWNER/REPO/FILE.gguf)")


def tree_api_path(repo: str, revision: str) -> str:
    return f"models/{validate_repo(repo)}/tree/{quote(validate_revision(revision), safe='')}?recursive=true"


def revision_api_path(repo: str, revision: str) -> str:
    return f"models/{validate_repo(repo)}/revision/{quote(validate_revision(revision), safe='')}"


def download_url(ref: HfRef) -> str:
    repo = validate_repo(ref.get("repo", ""))
    rev = validate_revision(ref.get("revision") or "main")
    return f"{HF_BASE}/{repo}/resolve/{quote(rev, safe='')}/{quote(validate_file(ref.get('file', '')))}"


def gguf_files(tree: object) -> HfFileList:
    """[(file, bytes, sha256)] of the model GGUFs in a Hugging Face tree API response."""
    if not isinstance(tree, list):
        raise ConfigError("Hugging Face: unexpected answer (not a file list)")
    out: HfFileList = []
    for f in tree:
        if not isinstance(f, dict) or f.get("type") != "file":
            continue
        path, size, lfs = f.get("path"), f.get("size", 0), f.get("lfs")
        if not isinstance(path, str) or not path.endswith(".gguf") or is_extra_part(posixpath.basename(path)):
            continue
        oid = lfs.get("oid", "") if isinstance(lfs, dict) else ""
        out.append((validate_file(path), size if isinstance(size, int) else 0, oid if isinstance(oid, str) else ""))
    return out


def commit_sha(info: object, fallback: str) -> str:
    """The commit a revision points at (pins a download), from the revision API response."""
    sha = info.get("sha") if isinstance(info, dict) else None
    return validate_revision(sha) if isinstance(sha, str) and sha else fallback


def validate_ref(ref: object, where: str) -> HfRef:
    """An "hf" object from the catalogue or models.json, checked field by field."""
    if not isinstance(ref, dict):
        raise ConfigError(f"{where}: hf must be an object")

    def text(key: str) -> Optional[str]:
        v = ref.get(key)
        if v is not None and not isinstance(v, str):
            raise ConfigError(f"{where}: hf.{key} must be a string")
        return v

    out: HfRef = {}
    repo, revision, file, sha = text("repo"), text("revision"), text("file"), text("sha256")
    if repo is not None:
        out["repo"] = validate_repo(repo)
    if revision is not None:
        out["revision"] = validate_revision(revision)
    if file is not None:
        out["file"] = validate_file(file)
    if sha is not None:
        out["sha256"] = validate_sha256(sha)
    if "bytes" in ref:
        b = ref["bytes"]
        if not isinstance(b, int) or isinstance(b, bool) or b < 0:
            raise ConfigError(f"{where}: hf.bytes must be a byte count")
        out["bytes"] = b
    return out
