"""The server's files as the dashboard reads them (the ServerFiles port, C-053): the log a piece at a time, the
console output, the log filter's pointer and the model file."""
from __future__ import annotations

import json
import os
import re
import time
from typing import Optional, Tuple

from ...domain.server.snapshot import clean
from ...domain.shared.ansi import ANSI
from ...domain.shared.types import JsonObject
from .log_filter import pointer_path


def log_start(path: str) -> Optional[float]:
    """Epoch seconds a log started, from its (link target's) name: llama-server-YYYYMMDD-HHMMSS.log."""
    m = re.search(r"(\d{8}-\d{6})", os.path.basename(os.path.realpath(path)))
    return time.mktime(time.strptime(m.group(1), "%Y%m%d-%H%M%S")) if m else None


class LocalServerFiles:
    """The ServerFiles port over this computer's files (every OS)."""

    def size(self, path: str) -> Optional[int]:
        try:
            return os.path.getsize(path) if os.path.isfile(path) else None
        except OSError:
            return None

    def read_from(self, path: str, pos: int) -> Optional[Tuple[int, int, str]]:
        try:
            with open(path, "rb") as f:
                f.seek(0, 2)
                start = pos if f.tell() >= pos else 0      # a cut file is read from its start
                f.seek(start)
                data = f.read()
                end = f.tell()
        except OSError:
            return None
        return start, end, clean(ANSI.sub("", data.decode(errors="replace")))

    def started(self, path: str) -> Optional[float]:
        return log_start(path)

    def pointer(self, home: str, port: int) -> JsonObject:
        try:
            with open(pointer_path(home, port), encoding="utf-8") as f:
                doc = json.load(f)
            return doc if isinstance(doc, dict) else {}
        except (OSError, ValueError):
            return {}

