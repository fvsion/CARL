"""Files on this computer's disk (the Files port of carl_client.ports)."""
from __future__ import annotations

import os
import shutil
from typing import List, Optional


class LocalFiles:
    """Files on this computer's disk."""

    def read(self, path: str) -> Optional[str]:
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return f.read()

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def isfile(self, path: str) -> bool:
        return os.path.isfile(path)

    def isdir(self, path: str) -> bool:
        return os.path.isdir(path)

    def islink(self, path: str) -> bool:
        return os.path.islink(path)

    def realpath(self, path: str) -> str:
        return os.path.realpath(path)

    def listdir(self, path: str) -> List[str]:
        return os.listdir(path)

    def makedirs(self, path: str) -> None:
        os.makedirs(path, exist_ok=True)

    def write(self, path: str, text: str) -> None:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    def chmod(self, path: str, mode: int) -> None:
        os.chmod(path, mode)

    def copy(self, src: str, dest: str) -> None:
        shutil.copy2(src, dest)

    def copy_into(self, src: str, folder: str) -> None:
        shutil.copy(src, folder)

    def copytree(self, src: str, dest: str) -> None:
        shutil.copytree(src, dest)

    def remove(self, path: str) -> None:
        os.remove(path)

    def rmdir(self, path: str) -> None:
        os.rmdir(path)

    def rmtree(self, path: str, quiet: bool = True) -> None:
        shutil.rmtree(path, ignore_errors=quiet)
