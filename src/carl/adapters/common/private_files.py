"""A copy of a secret, left to its owner (fix1.6). A file mode does nothing on Windows, where a file's privacy is its
access list (adapters/windows/secrets.py make_private), so the files that carry the API key out of its own file
(client/api-key, client/remote.json, the package zip) are set private by a function that composition gives them. None:
the mode is enough (macOS, Linux). A file whose list cannot be set is kept, with one warning, as the key file is.
"""
from __future__ import annotations

import os
from typing import Callable, Optional

Private = Callable[[str], bool]                 # leaves the file to its owner; False when it could not
Note = Callable[[str], None]

NOT_PRIVATE = "warning: CARL could not make {path} readable by you alone. Check who may read it with icacls."


def keep_private(path: str, private: Optional[Private], note: Optional[Note], shown: str = "") -> bool:
    """Set the file private; one warning through note (naming shown, else path) when that fails. True without private
    (nothing to set)."""
    if private is None:
        return True
    try:
        ok = private(path)
    except Exception:                           # a list that cannot be set is the warning, never a failed write
        ok = False
    if not ok and note is not None:
        note(NOT_PRIVATE.format(path=shown or path))
    return ok


def create_private(path: str, final: str, private: Optional[Private], note: Optional[Note]) -> None:
    """Make the file empty and private before any secret goes into it. A file renamed over another keeps its own
    list, so the secret is never in a file that others may read. final: the name it is renamed to (the warning's).
    Nothing without private."""
    if private is None:
        return
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600))
    keep_private(path, private, note, final)
