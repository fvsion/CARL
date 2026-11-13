"""The sessions' own folders (W1.5, the user, 2026-11-13: "B storage per session folder kept when cache expires"): a
folder for each client session in the settings folder's sessions folder, for what belongs to a session and outlives
its cache (a saved state may expire or be read again; the session's folder stays). CARL keeps the newest KEEP; a
plugin keeps its files for a session there (contract C-067). The folder is read and written by
adapters/common/session_folders.py. Pure.
"""
from __future__ import annotations

import re
from typing import List, Sequence, Tuple

FOLDER = "sessions"                     # in the settings folder
KEEP = 200                              # sessions kept: the newest (by the time each was last made or used)
NAME_MAX = 120                          # characters of a folder's name
UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def folder_name(session: str) -> str:
    """A session's folder name: its ID with any character but letters, digits, `.`, `_` and `-` as `_`, at most
    NAME_MAX of them; "" for an empty ID, and for "." and ".." (never a folder of their own)."""
    name = UNSAFE.sub("_", session)[:NAME_MAX]
    return "" if name in ("", ".", "..") else name


def past_keep(folders: Sequence[Tuple[str, float]], keep: int = KEEP) -> List[str]:
    """The folders (name, time) past the newest `keep`, oldest first: the ones that go."""
    ordered = sorted(folders, key=lambda f: (f[1], f[0]))
    return [name for name, _t in ordered[:max(len(ordered) - keep, 0)]]
