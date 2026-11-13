"""The claims and records of the disk cache's slots, as files in the slots folder (~/.config/carl/slots): the same
files the clients on this computer write (client/shared/carl-cache.js: .claim+MODEL+SLOT, .resident+MODEL+SLOT.json,
.turn+MODEL+SLOT, .ram+FILE.json), so every client sees the same state. The dashboard API (application/cache_api.py)
writes them for the clients on other computers, the dashboard's jobs read the turns. The state files are written
0600, each through a temporary file of its own (fsio.write_private); both sides call a claim or a turn stale after
the same time (a contract test checks both)."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import List, Optional, Tuple

from ...domain.server.snapshot import JSONDict, jdict
from . import installed_clients as fsio

CLAIM_STALE_S = 120
TURN_STALE_S = 600       # a turn mark this old is a client that went away (carl-cache.js TURN_STALE_MS)
SAFE = re.compile(r"[^A-Za-z0-9._-]")


def safe_name(s: str, n: int) -> str:
    """The client's safeName (carl-cache.js): the same file names on both sides."""
    return SAFE.sub("_", s)[:n]


class SlotClaims:
    """The claim and record files of one slots folder (shared with the local clients)."""

    def __init__(self, folder: str) -> None:
        self.folder = folder
        self.lock = threading.Lock()

    def _claim_path(self, model: str, slot: int) -> str:
        return os.path.join(self.folder, f".claim+{safe_name(model, 60)}+{slot}")

    def _record_path(self, model: str, slot: int) -> str:
        return os.path.join(self.folder, f".resident+{safe_name(model, 60)}+{slot}.json")

    def claim(self, model: str, slot: int) -> bool:
        path = self._claim_path(model, slot)
        with self.lock:
            for _ in range(2):
                try:
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    os.write(fd, b"api")
                    os.close(fd)
                    return True
                except FileExistsError:
                    try:
                        if time.time() - os.path.getmtime(path) <= CLAIM_STALE_S:
                            return False
                        os.remove(path)
                    except OSError:
                        pass
                except OSError:
                    return True                 # a folder it can't write: no claims possible
            return False

    def release(self, model: str, slot: int) -> None:
        try:
            os.remove(self._claim_path(model, slot))
        except OSError:
            pass

    def record(self, model: str, slot: int, file: str, task: int, base: int) -> None:
        fsio.write_private(self._record_path(model, slot),
                           json.dumps({"model": model, "slot": slot, "file": file, "task": task, "base": base}))

    def recorded(self, model: str, file: str) -> Optional[JSONDict]:
        head = f".resident+{safe_name(model, 60)}+"
        try:
            names = os.listdir(self.folder)
        except OSError:
            return None
        for n in names:
            if not (n.startswith(head) and n.endswith(".json")):
                continue
            try:
                with open(os.path.join(self.folder, n), encoding="utf-8") as f:
                    doc = jdict(json.load(f))
            except (OSError, ValueError):
                continue
            if doc.get("model") == model and doc.get("file") == file:
                return {"slot": doc.get("slot"), "task": doc.get("task"), "base": doc.get("base") or 0}
        return None

    def _ram_path(self, file: str) -> str:
        return os.path.join(self.folder, f".ram+{file}.json")

    def mark_ram(self, model: str, file: str, task: int, at: float) -> None:
        fsio.write_private(self._ram_path(file), json.dumps({"model": model, "file": file, "task": task, "at": at}))

    def ram_marked(self, model: str, file: str) -> Optional[JSONDict]:
        try:
            with open(self._ram_path(file), encoding="utf-8") as f:
                doc = jdict(json.load(f))
        except (OSError, ValueError):
            return None
        return doc if doc.get("model") == model and doc.get("file") == file else None

    def unmark_ram(self, file: str) -> None:
        try:
            os.remove(self._ram_path(file))
        except OSError:
            pass

    def unrecord(self, model: str, slot: int) -> None:
        try:
            os.remove(self._record_path(model, slot))
        except OSError:
            pass

    def retire(self, file: str) -> int:
        """A saved state removed with its packed form and its patch's notes (as carl-cache.js removeState): the files
        that were there."""
        gone = 0
        with self.lock:
            for name in (file, f"{file}.zst", f"{file}.json"):
                try:
                    os.unlink(os.path.join(self.folder, name))
                    gone += 1
                except OSError:
                    pass
        return gone

    def _turn_path(self, model: str, slot: int) -> str:
        return os.path.join(self.folder, f".turn+{safe_name(model, 60)}+{slot}")

    def turn(self, model: str, slot: int, session: str, running: bool) -> None:
        """A turn runs in the slot (each request refreshes it), or it ended (only its own mark goes)."""
        path = self._turn_path(model, slot)
        if running:
            fsio.write_private(path, json.dumps({"model": model, "slot": slot, "session": session}))
            return
        try:
            with open(path, encoding="utf-8") as f:
                own = jdict(json.load(f)).get("session") == session
            if own:                                      # closed first: Windows cannot remove an open file
                os.remove(path)
        except (OSError, ValueError):
            pass

    def turns(self, model: str, now: Optional[float] = None) -> List[int]:
        """The slots of a model where a client's turn runs (marks newer than TURN_STALE_S)."""
        return sorted(slot for slot, _ in self.turn_marks(model, now))

    def turn_marks(self, model: str, now: Optional[float] = None) -> List[Tuple[int, str]]:
        """(slot, session) of each running turn of a model."""
        now = time.time() if now is None else now
        head = f".turn+{safe_name(model, 60)}+"
        out: List[Tuple[int, str]] = []
        try:
            names = os.listdir(self.folder)
        except OSError:
            return out
        for n in names:
            if n.startswith(head) and n[len(head):].isdigit():
                path = os.path.join(self.folder, n)
                try:
                    if now - os.path.getmtime(path) <= TURN_STALE_S:
                        with open(path, encoding="utf-8") as f:
                            out.append((int(n[len(head):]), str(jdict(json.load(f)).get("session") or "")))
                except (OSError, ValueError):
                    continue
        return out
