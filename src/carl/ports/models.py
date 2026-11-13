"""Ports of the model files: the models folder and the GGUF header shapes."""
from __future__ import annotations

from typing import List, Optional, Protocol

from ..domain.memory.gguf import ModelShape
from ..domain.shared.types import HfRef


class ModelFolder(Protocol):
    """The model files on disk (the models folder and any custom paths). Contract C-001."""

    def exists(self, path: str) -> bool: ...

    def size(self, path: str) -> int: ...

    def gguf_names(self, directory: str) -> List[str]:
        """Names of the .gguf files in a folder ([] when it can't be read)."""
        ...

    def subdirs(self, directory: str) -> List[str]:
        """Names of the folders in a folder ([] when it can't be read)."""
        ...

    def free_bytes(self, directory: str) -> Optional[int]:
        """Free disk space of the folder's volume (None when the folder doesn't exist)."""
        ...

    def make_dir(self, directory: str) -> None: ...

    def sha256(self, path: str) -> str: ...

    def remove(self, path: str) -> None:
        """Delete a file if it exists."""
        ...

    def remove_empty_dir(self, directory: str) -> None:
        """Delete a folder if it is empty; a folder with anything in it, or a missing one, stays as it is."""
        ...

    def rename(self, src: str, dst: str) -> None: ...


class ShapeReader(Protocol):
    """GGUF header shapes: of a local file, or of a file on Hugging Face (HTTP range read). Contract C-002."""

    def local(self, path: str) -> ModelShape: ...

    def remote(self, ref: HfRef) -> ModelShape: ...

    def cached(self, ref: HfRef) -> Optional[ModelShape]:
        """The shape of a file on Hugging Face when its header is in the cache already (a read of remote kept it), else
        None. It never uses the network: the mark of a quantization that is not downloaded (A73)."""
        ...

    def remote_head(self, ref: HfRef) -> bytes:
        """The first bytes of a file on Hugging Face (the same range read as remote, not kept): its header and tensor
        table, for the stub that llama-server's own fit measures before a download (A67). Raises OSError or
        ValueError."""
        ...
