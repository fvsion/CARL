"""Model copies between the library (the network share, read only) and the models folder on the local disk:
fetch (copy in, SHA-256 checked) and drop (remove the local copy, only with a verified copy in the library)."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from typing import Any, Callable, List, Mapping, Optional, Sequence

from .models import DropDecision, ModelFile, drop_decision, model_files, parse_sums

CHUNK = 8 * 1024 * 1024
Say = Callable[[str], None]


def load_catalog(path: str) -> Mapping[str, Any]:
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: not a catalogue")
    return doc


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def copy_hashed(src: str, dst: str, progress: Optional[Callable[[int], None]] = None) -> str:
    """Copy src to dst and return the SHA-256 of what was read."""
    h = hashlib.sha256()
    done = 0
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        for block in iter(lambda: fi.read(CHUNK), b""):
            h.update(block)
            fo.write(block)
            done += len(block)
            if progress:
                progress(done)
        fo.flush()
        os.fsync(fo.fileno())
    return h.hexdigest()


class FetchError(Exception):
    pass


def fetch(catalog: Mapping[str, Any], model: str, library: str, models_dir: str, say: Say = print,
          attempts: int = 2, margin: int = 2 * 1024 ** 3) -> List[str]:
    """Copy the model's files (and its drafter) from the library to the models folder, each checked against the
    catalogue's SHA-256. A local copy with the right SHA-256 stays. Nothing is written to the library."""
    files = model_files(catalog, model)
    os.makedirs(models_dir, exist_ok=True)
    out: List[str] = []
    for f in files:
        dst = os.path.join(models_dir, f.name)
        if os.path.isfile(dst) and os.path.getsize(dst) == f.size:
            say(f"{f.name}: a local copy exists; checking its SHA-256")
            if sha256_file(dst) == f.sha256:
                say(f"{f.name}: OK (local)")
                out.append(dst)
                continue
            raise FetchError(f"{dst} exists but its SHA-256 is not the catalogue's. Move it away, then fetch again.")
        src = os.path.join(library, f.name)
        if not os.path.isfile(src):
            raise FetchError(f"the library has no {f.name} ({library})")
        if os.path.getsize(src) != f.size:
            raise FetchError(f"{src} has {os.path.getsize(src)} bytes; the catalogue says {f.size}")
        free = shutil.disk_usage(models_dir).free
        if free < f.size + margin:
            raise FetchError(f"not enough free disk space in {models_dir}: {free / 1e9:.1f} GB free, "
                             f"{(f.size + margin) / 1e9:.1f} GB needed")
        part = dst + ".part"
        for attempt in range(1, attempts + 1):
            say(f"{f.name}: copying {f.size / 1e9:.1f} GB from the library (attempt {attempt} of {attempts})")
            marks = {f.size * k // 10 for k in range(1, 10)}
            done_marks: set[int] = set()

            def progress(done: int, size: int = f.size, name: str = f.name) -> None:
                for m in sorted(marks - done_marks):
                    if done >= m:
                        done_marks.add(m)
                        say(f"{name}: {100 * m // size}%")
            got = copy_hashed(src, part, progress if f.size >= 1024 ** 3 else None)
            if got == f.sha256:
                os.replace(part, dst)
                say(f"{f.name}: OK (SHA-256 checked)")
                out.append(dst)
                break
            os.remove(part)
            say(f"{f.name}: the SHA-256 of the copy is wrong")
        else:
            raise FetchError(f"{f.name}: every copy had a wrong SHA-256")
    return out


def local_kind(path: str) -> str:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "missing"
    if stat.S_ISLNK(st.st_mode):
        return "link"
    return "file" if stat.S_ISREG(st.st_mode) else "other"


def read_sums(library: str) -> Mapping[str, str]:
    try:
        with open(os.path.join(library, "SHA256SUMS"), encoding="utf-8") as f:
            return parse_sums(f.read())
    except OSError:
        return {}


def plan_drop(catalog: Mapping[str, Any], model: str, library: str, models_dir: str,
              keep: Sequence[str]) -> List[DropDecision]:
    """For each catalogue file of the model: may its local copy go, and why (or why not)."""
    sums = read_sums(library)
    out: List[DropDecision] = []
    for f in model_files(catalog, model):
        lib = os.path.join(library, f.name)
        size = os.path.getsize(lib) if os.path.isfile(lib) else None
        out.append(drop_decision(f, keep, local_kind(os.path.join(models_dir, f.name)), size, sums.get(f.name)))
    return out


def drop(catalog: Mapping[str, Any], model: str, library: str, models_dir: str, keep: Sequence[str],
         say: Say = print, dry_run: bool = False) -> List[str]:
    """Remove the local copies of the model's catalogue files that have a verified copy in the library.
    Never a file that the catalogue does not name, never a file on the keep list. Returns the removed paths."""
    removed: List[str] = []
    for d in plan_drop(catalog, model, library, models_dir, keep):
        f: ModelFile = d.file
        local = os.path.join(models_dir, f.name)
        if not d.allowed:
            say(f"{f.name}: kept: {d.reason}")
            continue
        if d.needs_hash:
            say(f"{f.name}: the library has no SHA256SUMS line for it; checking the library copy (slow)")
            if sha256_file(os.path.join(library, f.name)) != f.sha256:
                say(f"{f.name}: kept: the library copy's SHA-256 is not the catalogue's")
                continue
        if dry_run:
            say(f"{f.name}: would remove {local} ({d.reason})")
            continue
        os.remove(local)
        say(f"{f.name}: removed the local copy ({d.reason})")
        removed.append(local)
    return removed
