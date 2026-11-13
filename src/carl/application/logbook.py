"""The log reader of the dashboard: follows the server's log file through the ServerFiles port (C-053) into a
LogBook (domain/server/logbook.py)."""
from __future__ import annotations

from typing import Optional

from ..domain.server.logbook import LogBook
from ..ports import ServerFiles


class LogTail:
    """Follows one log file through the ServerFiles port (C-053): reads what was appended since the last update into
    its LogBook. A new path or a cut file starts over."""

    def __init__(self, files: ServerFiles) -> None:
        self.files = files
        self.path: Optional[str] = None
        self.pos = 0
        self.buf = ""           # an unfinished last line
        self.book = LogBook()

    def update(self, path: Optional[str]) -> None:
        """Read what was added to path since the last call."""
        if path != self.path:
            self.path, self.pos, self.buf = path, 0, ""
            self.book.start = self.files.started(path) if path else None
            self.book.reset()
        if path is None:
            return
        got = self.files.read_from(path, self.pos)
        if got is None:
            return
        start, end, text = got
        if start < self.pos:                # the file was cut: its lines start again
            self.book.reset()
        self.pos = end
        parts = (self.buf + text).split("\n")
        self.buf = parts.pop()
        for line in parts:
            self.book.add(line)
