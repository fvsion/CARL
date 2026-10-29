"""The server's API key file on this Mac (paths: carl_core.domain.apikey)."""
from __future__ import annotations

import os
from typing import Mapping, Optional

from ..domain.apikey import server_key_file


def has_key(path: str) -> bool:
    """path is a file with something in it."""
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


def key_file(env: Optional[Mapping[str, str]] = None, home: Optional[str] = None) -> str:
    """The key file the server uses (API_KEY_FILE, the CARL path or the pre-1.2.0 one)."""
    return server_key_file(home or os.path.expanduser("~"), os.environ if env is None else env, has_key)


def read_key(path: str) -> str:
    """The key in path (whitespace stripped), "" when it can't be read."""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except (OSError, UnicodeDecodeError):
        return ""
