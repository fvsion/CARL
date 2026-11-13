"""One process at a time (the Lock port on macOS and Linux): an exclusive flock on a lock file. Another process that
asks waits until the holder ends its block (or exits)."""
from __future__ import annotations

import contextlib
import fcntl
import os
from typing import Iterator


class FcntlLock:
    @contextlib.contextmanager
    def hold(self, path: str) -> Iterator[None]:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o666)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)                     # closing the file ends the lock
