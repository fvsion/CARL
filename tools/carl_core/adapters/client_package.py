"""The client package's I/O (the rules: carl_core/domain/package.py): the file list from git, the CARL
version, this Mac's host name, and the zip. ./carl.sh package (tools/carl.py) and the dashboard's Connect >
Setup both call make_package(), so they make the same zip."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import zipfile
from typing import List, Mapping, Optional, Tuple

from ..domain import package as pk

Entry = Tuple[str, bytes, int, float]           # (path in client/, content, mode, mtime)


def _run(argv: List[str], timeout: float = 30) -> Optional[bytes]:
    """A command's output, or None when it cannot run or fails."""
    try:
        p = subprocess.run(argv, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout if p.returncode == 0 else None


def tracked_client_files(repo: str) -> Optional[List[str]]:
    """`git ls-files client/`, relative to client/; None when git or the repository is not there."""
    out = _run(["git", "-C", repo, "ls-files", "-z", "--", "client/"])
    if out is None:
        return None
    return [x.decode("utf-8", "replace")[len("client/"):] for x in out.split(b"\0") if x.startswith(b"client/")]


def walked_client_files(client: str) -> List[str]:
    """Every file of the client folder, relative to it: only when there is no git repository (a copy of the
    share zip has no .git). pk.keep() then leaves out the caches and the backups."""
    out: List[str] = []
    for root, dirs, files in os.walk(client):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in ("__pycache__", "node_modules"))
        out += [os.path.relpath(os.path.join(root, f), client) for f in files]
    return out


def carl_version(repo: str) -> str:
    """The newest "## X.Y.Z" heading in CHANGELOG.md, else `git describe --tags`, else "unknown"."""
    try:
        with open(os.path.join(repo, "CHANGELOG.md"), encoding="utf-8") as f:
            v = pk.version_from_changelog(f.read())
        if v:
            return v
    except (OSError, UnicodeDecodeError):
        pass
    out = _run(["git", "-C", repo, "describe", "--tags"])
    v = pk.version_from_describe(out.decode("utf-8", "replace")) if out else None
    return v or "unknown"


def host_name() -> str:
    """This Mac's name for the file name of the zip ("" when unknown)."""
    try:
        return socket.gethostname()
    except OSError:
        return ""


def _read(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _load_json(path: str) -> object:
    raw = _read(path)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def write_zip(path: str, entries: List[Entry]) -> int:
    """The zip at path, readable by its owner only (0600: it holds the key), written atomically. Each entry goes
    into the folder pk.FOLDER with its mode (unzip and the Finder restore the modes). Returns the size."""
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as raw, zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as z:
            for rel, data, mode, mtime in entries:
                stamp = time.localtime(max(mtime, 315532800))[:6]      # a zip has no date before 1980
                info = zipfile.ZipInfo(f"{pk.FOLDER}/{rel}", stamp)
                info.create_system = 3                                  # Unix: the mode below is used
                info.external_attr = (0o100000 | mode) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return os.path.getsize(path)


def make_package(repo: str, client_dir: str, out_dir: str, models: object, cfg: Mapping[str, object],
                 cmd: str = "./carl.sh", anyway: bool = False) -> pk.Outcome:
    """Write dist/carl-client-VERSION-HOST.zip: the files of client/ in git, the entry points, and from the server
    remote.json, api-key, the installed models (models: what tools/carl.py client-models gives) and VERSION.
    client_dir holds the server's remote.json and api-key (normally repo/client). Nothing is written when the
    server serves only this Mac (unless anyway) or when the server never started."""
    remote_path, key_path = os.path.join(client_dir, "remote.json"), os.path.join(client_dir, "api-key")
    missing = [n for n, p in (("remote.json", remote_path), ("api-key", key_path)) if not (_read(p) or b"").strip()]
    if missing:
        return pk.missing_refusal(missing, client_dir, cmd)
    remote = pk.parse_remote(_load_json(remote_path))
    if remote is None:
        return pk.Outcome("", f"error: {remote_path} is not a valid connection file.",
                          (f"Start the server again ({cmd}): it writes the file again. Then run {cmd} package "
                           f"again.",))
    if pk.is_local(remote.host) and not anyway:
        return pk.local_refusal(remote, cfg, cmd)
    src = os.path.join(repo, "client")
    files = tracked_client_files(repo)
    rels = pk.select(walked_client_files(src) if files is None else files)
    now = time.time()
    entries: List[Entry] = []
    for rel in rels:
        path = os.path.join(src, rel)
        data = _read(path) if os.path.isfile(path) and not os.path.islink(path) else None
        if data is None:
            if rel in pk.ENTRY_POINTS:
                return pk.Outcome("", f"error: {path} is not there. The client folder is not complete.",
                                  ("Get the CARL folder again (git pull, or a new copy).",))
            continue                                    # in git, but deleted here: not in the package
        entries.append((rel, data, pk.mode_of(rel, os.access(path, os.X_OK)), os.path.getmtime(path)))
    version = carl_version(repo)
    entries += [("remote.json", _read(remote_path) or b"", 0o600, now),
                ("api-key", _read(key_path) or b"", 0o600, now),
                ("installed-models.json", (json.dumps(models, indent=2) + "\n").encode(), 0o644, now),
                ("VERSION", f"{version}\n".encode(), 0o644, now)]
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(os.path.abspath(out_dir), pk.zip_name(version, host_name() or remote.host))
    size = write_zip(out, entries)
    return pk.Outcome(out, "", pk.done_notes(out, remote, cfg, cmd), len(entries), size)
