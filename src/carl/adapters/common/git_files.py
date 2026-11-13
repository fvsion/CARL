"""The files of the client package (implements ClientFiles, contract C-035): the client folder's file list from the
clone's repository, else from the folder itself; and the files read as the zip needs them (their content, whether
they run, their time). The rules for what goes in are domain/clients/package.py's select().
"""
from __future__ import annotations

import os
import subprocess
from typing import List, Optional

from ...ports.storage import PackageFile

FOLDER = "client"
SKIPPED_FOLDERS = ("__pycache__", "node_modules")


def _git(argv: List[str], timeout: float = 30) -> Optional[bytes]:
    """git's output, or None when git cannot run or fails (not a repository)."""
    try:
        p = subprocess.run(["git", *argv], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           stdin=subprocess.DEVNULL, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout if p.returncode == 0 else None


def tracked(repo: str) -> Optional[List[str]]:
    """`git ls-files client/`, relative to client/; None when git or the repository is not there."""
    out = _git(["-C", repo, "ls-files", "-z", "--", FOLDER + "/"])
    if out is None:
        return None
    prefix = (FOLDER + "/").encode()
    return [x.decode("utf-8", "replace")[len(prefix):] for x in out.split(b"\0") if x.startswith(prefix)]


def walked(folder: str) -> List[str]:
    """Every file of the client folder, relative to it, with "/": only when there is no repository (a copy of the share zip
    has no .git). The domain's select() then leaves out the caches and the backups."""
    out: List[str] = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIPPED_FOLDERS)
        out += [os.path.relpath(os.path.join(root, f), folder).replace(os.sep, "/") for f in files]
    return out


class ClientFolder:
    """The client folder of a clone and the server's files next to it."""

    def client_files(self, repo: str) -> List[str]:
        files = tracked(repo)
        return walked(os.path.join(repo, FOLDER)) if files is None else files

    def client_file(self, path: str) -> Optional[PackageFile]:
        if not os.path.isfile(path) or os.path.islink(path):
            return None
        try:
            with open(path, "rb") as f:
                data = f.read()
            return data, os.access(path, os.X_OK), os.path.getmtime(path)
        except OSError:
            return None

    def server_file(self, path: str) -> Optional[bytes]:
        try:
            with open(path, "rb") as f:
                return f.read()
        except OSError:
            return None
