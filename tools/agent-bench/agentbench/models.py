"""The model swap rules: which files a catalogue model has (the model and its MTP drafter), and when a local copy
may go. Pure: the I/O is in library.py."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import REPO

CATALOG = os.path.join(REPO, "host", "catalog.json")
DEFAULT_LIBRARY = "/Volumes/IP/AI_models/gguf"
DEFAULT_MODELS_DIR = os.path.join("~", "models", "gguf")
# The user's own models on the M3 Pro: never dropped.
DEFAULT_KEEP = ("Qwen3.6-35B-A3B-UD-Q4_K_M.gguf", "orcarouter_Qwen3.8-27B-Uncensored-Q4_K_M.gguf")
_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*\.gguf$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ModelFile:
    name: str           # the file name (no folder)
    sha256: str
    size: int
    role: str           # model | drafter


def model_files(catalog: Mapping[str, Any], model: str) -> List[ModelFile]:
    """The files of a catalogue model: its GGUF (hf.file) and its MTP drafter (draft.file) when it has one."""
    for m in catalog.get("models", []):
        if isinstance(m, dict) and m.get("name") == model:
            out: List[ModelFile] = []
            for key, role in (("hf", "model"), ("draft", "drafter")):
                ref = m.get(key)
                if not isinstance(ref, dict):
                    continue
                name, sha, size = ref.get("file"), ref.get("sha256"), ref.get("bytes")
                if not (isinstance(name, str) and _FILE.match(name) and isinstance(sha, str) and _SHA.match(sha)
                        and isinstance(size, int) and size > 0):
                    raise ValueError(f"catalogue: {model}: the {key} entry has no valid file, sha256 and bytes")
                out.append(ModelFile(name, sha, size, role))
            if not out:
                raise ValueError(f"catalogue: {model} has no file")
            return out
    raise ValueError(f"{model!r} is not in the catalogue")


def catalogue_files(catalog: Mapping[str, Any]) -> Dict[str, ModelFile]:
    """Every file that the catalogue names, by file name."""
    out: Dict[str, ModelFile] = {}
    for m in catalog.get("models", []):
        if isinstance(m, dict) and isinstance(m.get("name"), str):
            try:
                for f in model_files(catalog, m["name"]):
                    out[f.name] = f
            except ValueError:
                continue
    return out


def parse_sums(text: str) -> Dict[str, str]:
    """A SHA256SUMS file ("<sha256>  <file>" or "<sha256> *<file>" per line): file name -> sha256."""
    out: Dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and _SHA.match(parts[0].lower()):
            out[os.path.basename(parts[1].lstrip("*").strip())] = parts[0].lower()
    return out


@dataclass(frozen=True)
class DropDecision:
    file: ModelFile
    allowed: bool
    reason: str
    needs_hash: bool = False      # the library copy must be hashed first (no SHA256SUMS line for it)


def drop_decision(f: ModelFile, keep: Sequence[str], local_kind: str, library_size: Optional[int],
                  library_sum: Optional[str]) -> DropDecision:
    """May the local copy of f go? local_kind: file | missing | link | other (os.lstat of the local path).
    library_size: the size of the library copy (None: no copy). library_sum: its SHA256SUMS line (None: none)."""
    if f.name in keep:
        return DropDecision(f, False, "it is on the keep list (the user's own model)")
    if local_kind == "missing":
        return DropDecision(f, False, "there is no local copy")
    if local_kind != "file":
        return DropDecision(f, False, "the local path is not a regular file")
    if library_size is None:
        return DropDecision(f, False, "the library has no copy")
    if library_size != f.size:
        return DropDecision(f, False, f"the library copy has {library_size} bytes, the catalogue says {f.size}")
    if library_sum is not None and library_sum != f.sha256:
        return DropDecision(f, False, "the library's SHA256SUMS line does not match the catalogue")
    if library_sum is None:
        return DropDecision(f, True, "the library copy must be checked (no SHA256SUMS line)", needs_hash=True)
    return DropDecision(f, True, "the library has a verified copy")
