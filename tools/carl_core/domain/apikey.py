"""Where the server's API key (the Bearer token clients send) lives.

Since 1.2.0 it is ~/.config/llm-deploy/api-key, next to config.json. Before that it was
in MTPLX's folder (~/.mtplx/api-key); host/common.sh copies it over on the first start,
and until then the readers fall back to it so nothing breaks in between.
"""
from __future__ import annotations

import os
from typing import Callable, Mapping

from .models import expand_home

KEY_FILE = os.path.join(".config", "llm-deploy", "api-key")        # under the home folder
LEGACY_KEY_FILE = os.path.join(".mtplx", "api-key")                  # before 1.2.0


def server_key_file(home: str, env: Mapping[str, str], has_key: Callable[[str], bool]) -> str:
    """The key file to read: API_KEY_FILE when set; else the CARL key file, or the
    pre-1.2.0 one while only that holds a key. has_key(path): the file exists and is not empty."""
    override = env.get("API_KEY_FILE", "").strip()
    if override:
        return expand_home(override, home)
    new, old = os.path.join(home, KEY_FILE), os.path.join(home, LEGACY_KEY_FILE)
    return old if not has_key(new) and has_key(old) else new
