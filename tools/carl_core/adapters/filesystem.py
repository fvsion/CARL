"""The model files on this Mac (implements the ModelFolder port)."""
from __future__ import annotations

import hashlib
import os
import shutil
from typing import List, Optional

HASH_CHUNK = 16 * 2 ** 20


class LocalModelFolder:
    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def size(self, path: str) -> int:
        return os.path.getsize(path)

    def gguf_names(self, directory: str) -> List[str]:
        try:
            return [f for f in os.listdir(directory) if f.endswith(".gguf") and os.path.isfile(os.path.join(directory, f))]
        except OSError:
            return []

    def free_bytes(self, directory: str) -> Optional[int]:
        return shutil.disk_usage(directory).free if os.path.isdir(directory) else None

    def make_dir(self, directory: str) -> None:
        os.makedirs(directory, exist_ok=True)

    def sha256(self, path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(HASH_CHUNK), b""):
                h.update(block)
        return h.hexdigest()

    def remove(self, path: str) -> None:
        if os.path.lexists(path):
            os.remove(path)

    def rename(self, src: str, dst: str) -> None:
        os.replace(src, dst)
