"""Where CARL's settings folder lives (config.json, models.json, the API key).

It is ~/.config/carl. Before the rename in 1.2.0 it was ~/.config/llm-deploy (CARL was
called LLM-Deploy); host/common.sh moves it once (migrate_conf_dir) and leaves a symlink
behind. Until that has happened the readers use the old folder, so nothing breaks in between.
"""
from __future__ import annotations

import os
from typing import Callable, Mapping

from .models import expand_home

CONF_DIR = os.path.join(".config", "carl")                       # under the home folder
LEGACY_CONF_DIR = os.path.join(".config", "llm-deploy")          # before the rename (1.2.0)


def conf_dir(home: str, env: Mapping[str, str], is_dir: Callable[[str], bool]) -> str:
    """The settings folder: CARL_CONF_DIR when set; else ~/.config/carl, or the old
    ~/.config/llm-deploy while only that one exists."""
    override = env.get("CARL_CONF_DIR", "").strip()
    if override:
        return expand_home(override, home)
    new, old = os.path.join(home, CONF_DIR), os.path.join(home, LEGACY_CONF_DIR)
    return old if not is_dir(new) and is_dir(old) else new
