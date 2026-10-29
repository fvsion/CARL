"""Where the server's API key (the Bearer token clients send) lives.

It is ~/.config/carl/api-key, next to config.json. Earlier places, newest first:
~/.config/llm-deploy/api-key (before the rename in 1.2.0) and ~/.mtplx/api-key (MTPLX's
folder, before 1.2.0). host/common.sh moves or copies the key over on the first start,
and until then the readers fall back to the old files so nothing breaks in between.
"""
from __future__ import annotations

import os
from typing import Callable, Mapping

from .confdir import CONF_DIR, LEGACY_CONF_DIR
from .models import expand_home

KEY_FILE = os.path.join(CONF_DIR, "api-key")                          # under the home folder
LEGACY_KEY_FILES = (os.path.join(LEGACY_CONF_DIR, "api-key"),         # before the rename
                    os.path.join(".mtplx", "api-key"))                 # before 1.2.0 (MTPLX)


def server_key_file(home: str, env: Mapping[str, str], has_key: Callable[[str], bool]) -> str:
    """The key file to read: API_KEY_FILE when set; else the CARL key file, or the newest
    earlier one while only that holds a key. has_key(path): the file exists and is not empty."""
    override = env.get("API_KEY_FILE", "").strip()
    if override:
        return expand_home(override, home)
    candidates = [os.path.join(home, rel) for rel in (KEY_FILE, *LEGACY_KEY_FILES)]
    return next((p for p in candidates if has_key(p)), candidates[0])
