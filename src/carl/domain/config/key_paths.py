"""Where the server's API key (the Bearer token clients send) lives.

It is ~/.config/carl/api-key, next to config.json; with CARL_CONF_DIR set it is api-key in that folder
(decision A32), and nothing falls back to an earlier place then. Earlier places, newest first:
~/.config/llm-deploy/api-key (before the rename in 1.2.0) and ~/.mtplx/api-key (MTPLX's
folder, before 1.2.0). The first start moves or copies the key over (macOS), and until then the
readers fall back to the old files so nothing breaks in between.
"""
from __future__ import annotations

from typing import Callable, Mapping

from .paths import CONF_DIR, LEGACY_CONF_DIR, conf_dir_override, home_path
from ..shared.paths import expand_home, policy_for

KEY_NAME = "api-key"
KEY_FILE = f"{CONF_DIR}/{KEY_NAME}"                                   # under the home folder, written with "/"
LEGACY_KEY_FILES = (f"{LEGACY_CONF_DIR}/api-key",                     # before the rename
                    ".mtplx/api-key")                                 # before 1.2.0 (MTPLX)


def carl_key_file(home: str, env: Mapping[str, str]) -> str:
    """CARL's own key file: api-key in CARL_CONF_DIR when that is set, else ~/.config/carl/api-key (C-203)."""
    conf = conf_dir_override(home, env)
    return policy_for(conf).join(conf, KEY_NAME) if conf else home_path(home, KEY_FILE)


def server_key_file(home: str, env: Mapping[str, str], has_key: Callable[[str], bool]) -> str:
    """The key file to read: API_KEY_FILE when set; else the CARL key file (carl_key_file), or, without
    CARL_CONF_DIR, the newest earlier one while only that holds a key. has_key(path): the file exists and is
    not empty."""
    override = env.get("API_KEY_FILE", "").strip()
    if override:
        return expand_home(override, home)
    if conf_dir_override(home, env):
        return carl_key_file(home, env)
    candidates = [carl_key_file(home, env), *(home_path(home, rel) for rel in LEGACY_KEY_FILES)]
    return next((p for p in candidates if has_key(p)), candidates[0])
