"""A sparse stub of a GGUF file (decision A67 (2)): its header and tensor table, then a hole up to the real file size, so
llama-server's fit sizes a model that is not on disk (domain/memory/server_fit.py). Nothing past the header is written.

APFS and ext4 leave a hole for a truncate past the end (truncate, the default). On Windows Python's truncate (the C
runtime's _chsize) writes zeros, even on a sparse file: WESTc's first stubs took 30 GiB. The composition gives Windows
its own extend (adapters/windows/sparse_file.py: FSCTL_SET_SPARSE, then SetEndOfFile; 10 to 16 MiB on disk each,
results/WESTc/e-tests.md).
"""
from __future__ import annotations

from typing import BinaryIO, Callable

Extend = Callable[[BinaryIO, int], None]


def truncate(f: BinaryIO, size: int) -> None:
    """The file extended to size bytes by a truncate: a hole on APFS and ext4."""
    f.truncate(size)


def write_stub(head: bytes, size: int, path: str, extend: Extend = truncate) -> None:
    """path = head, then extended to size bytes as a sparse file. Raises OSError."""
    with open(path, "wb") as f:
        f.write(head)
        f.flush()
        extend(f, size)
