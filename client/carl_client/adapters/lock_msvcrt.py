"""One process at a time (the Lock port on Windows): msvcrt's lock on the first byte of a lock file. Another process
that asks waits until the holder ends its block (or exits: Windows drops the lock with the file's handle).
msvcrt.LK_LOCK gives up after 10 tries, so the wait is a loop of LK_NBLCK tries."""
from __future__ import annotations

import contextlib
import os
import time
from typing import Any, Callable, Iterator, Optional

WAIT = 0.05                          # seconds between two tries


class MsvcrtLock:
    """msvcrt: the module (tests pass a fake; default: the real one, imported here, on Windows only); sleep: the wait
    between two tries."""

    def __init__(self, msvcrt: Optional[Any] = None, sleep: Callable[[float], None] = time.sleep) -> None:
        self._msvcrt = msvcrt
        self.sleep = sleep

    @property
    def msvcrt(self) -> Any:
        if self._msvcrt is None:
            import msvcrt
            self._msvcrt = msvcrt
        return self._msvcrt

    @contextlib.contextmanager
    def hold(self, path: str) -> Iterator[None]:
        m = self.msvcrt
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)     # not O_TRUNC: the holder's handle may be open
        try:
            while True:
                os.lseek(fd, 0, os.SEEK_SET)
                try:
                    m.locking(fd, m.LK_NBLCK, 1)              # the first byte (beyond the end is allowed)
                    break
                except OSError:                               # another process holds it
                    self.sleep(WAIT)
            try:
                yield
            finally:
                os.lseek(fd, 0, os.SEEK_SET)
                with contextlib.suppress(OSError):
                    m.locking(fd, m.LK_UNLCK, 1)
        finally:
            os.close(fd)                                      # closing the file ends the lock too
