"""The sessions' own folders (contract C-067): ~/.config/carl/sessions/SESSION/, a folder for each client session, made
private (0700) when it is first needed and kept when the session's cache expires. A folder's time on the disk is when
it was last made or used; past KEEP folders the oldest go whole, at the next make. The names and the rule of what goes
are the domain's (domain/server/session_folders.py)."""
from __future__ import annotations

import os
import shutil
import time
from typing import Callable, List, Tuple

from ...domain.server.session_folders import KEEP, folder_name, past_keep
from .private_dirs import make_dirs


class SessionFolderStore:
    """SessionFolders over ROOT (the settings folder's sessions folder, made at the first make)."""

    def __init__(self, root: str, keep: int = KEEP, now: Callable[[], float] = time.time) -> None:
        self.root, self.keep, self._now = root, keep, now

    def path(self, session: str) -> str:
        name = folder_name(session)
        return os.path.join(self.root, name) if name else ""

    def make(self, session: str) -> str:
        path = self.path(session)
        if not path:
            return ""
        try:
            made = not os.path.isdir(path)
            make_dirs(path, 0o700)
            t = self._now()
            os.utime(path, (t, t))
        except OSError:
            return ""
        if made:
            self._tidy()
        return path

    def _tidy(self) -> None:
        """The folders past the newest `keep` removed (a folder that cannot be removed stays)."""
        try:
            names = os.listdir(self.root)
        except OSError:
            return
        folders: List[Tuple[str, float]] = []
        for name in names:
            p = os.path.join(self.root, name)
            try:
                if os.path.isdir(p) and not os.path.islink(p):
                    folders.append((name, os.path.getmtime(p)))
            except OSError:
                continue
        for name in past_keep(folders, self.keep):
            shutil.rmtree(os.path.join(self.root, name), ignore_errors=True)
