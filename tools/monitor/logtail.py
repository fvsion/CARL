"""Reads the server log incrementally into a LogBook."""
from __future__ import annotations

import os
import re
import time
from typing import Optional

from .fmt import ANSI
from .logbook import LogBook
from .model import clean


def log_start(path: str) -> Optional[float]:
    """Epoch seconds a log started, from its (link target's) name: llama-server-YYYYMMDD-HHMMSS.log."""
    m = re.search(r"(\d{8}-\d{6})", os.path.basename(os.path.realpath(path)))
    return time.mktime(time.strptime(m.group(1), "%Y%m%d-%H%M%S")) if m else None


class LogTail:
    """Follows one log file: reads what was appended since the last update. A new path
    or a truncated file starts over."""

    def __init__(self) -> None:
        self.path: Optional[str] = None
        self.pos = 0
        self.buf = ""           # an unfinished last line
        self.book = LogBook()

    def update(self, path: Optional[str]) -> None:
        """Read what was added to path since the last call."""
        if path != self.path:
            self.path, self.pos, self.buf = path, 0, ""
            self.book.start = log_start(path) if path else None
            self.book.reset()
        if path is None:
            return
        try:
            with open(path, "rb") as f:
                f.seek(0, 2)
                if f.tell() < self.pos:
                    self.pos = 0
                    self.book.reset()
                f.seek(self.pos)
                data = f.read()
                self.pos = f.tell()
        except OSError:
            return
        parts = (self.buf + clean(ANSI.sub("", data.decode(errors="replace")))).split("\n")
        self.buf = parts.pop()
        for line in parts:
            self.book.add(line)
