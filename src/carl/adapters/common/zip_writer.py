"""The client package's zip on disk (implements PackageWriter, contract C-030), and the name of this computer for
the zip's file name.
"""
from __future__ import annotations

import os
import socket
import time
import zipfile
from typing import Optional, Sequence

from ...domain.clients import package as pk
from ...ports.storage import PackageEntry
from .private_files import Note, Private, create_private

OWNER_ONLY = 0o600
EARLIEST = 315532800                      # 1980: a zip has no date before it


def host_name() -> str:
    """This computer's name for the file name of the zip ("" when unknown)."""
    try:
        return socket.gethostname()
    except OSError:
        return ""


class ZipWriter:
    """The package's zip: each entry goes into the folder pk.FOLDER with its mode (unzip and the Finder restore the
    modes). private, note: on Windows, where a mode is nothing, the function that leaves a file to its owner and where
    its warning goes (private_files.py); the zip holds the key, so it is private before it is written."""

    def __init__(self, private: Optional[Private] = None, note: Optional[Note] = None) -> None:
        self.private, self.note = private, note

    def write_zip(self, path: str, entries: Sequence[PackageEntry]) -> int:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        try:
            create_private(tmp, path, self.private, self.note)
        except OSError:
            pass                                    # the open below says it
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, OWNER_ONLY)
        try:
            if hasattr(os, "fchmod"):     # Windows: none before 3.13; then it only clears read-only, as 0600 asks
                os.fchmod(fd, OWNER_ONLY)
            with os.fdopen(fd, "wb") as raw, zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as z:
                for rel, data, mode, mtime in entries:
                    info = zipfile.ZipInfo(f"{pk.FOLDER}/{rel}", time.localtime(max(mtime, EARLIEST))[:6])
                    info.create_system = 3                  # Unix: the mode below is used
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
