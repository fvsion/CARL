"""Where CARL's settings folder lives (config.json, models.json, the API key).

It is ~/.config/carl. Before the rename in 1.2.0 it was ~/.config/llm-deploy (CARL was
called LLM-Deploy); host/common.sh moves it once (migrate_conf_dir) and leaves a symlink
behind. Until that has happened the readers use the old folder, so nothing breaks in between.
"""
from __future__ import annotations

from typing import Callable, Mapping

from ..shared.paths import expand_home, policy_for

# Under the home folder, written with "/": home_path() joins them in the home folder's own flavour.
CONF_DIR = ".config/carl"
LEGACY_CONF_DIR = ".config/llm-deploy"          # before the rename (1.2.0)


def home_path(home: str, rel: str) -> str:
    """REL (a path under the home folder, written with "/") in HOME, with HOME's separator (PathPolicy)."""
    return policy_for(home).join(home, *rel.split("/"))


def conf_dir_override(home: str, env: Mapping[str, str]) -> str:
    """CARL_CONF_DIR as a path ("" when it is not set): the one place that reads the variable's value."""
    return expand_home(env.get("CARL_CONF_DIR", "").strip(), home)


def conf_dir(home: str, env: Mapping[str, str], is_dir: Callable[[str], bool]) -> str:
    """The settings folder: CARL_CONF_DIR when set; else ~/.config/carl, or the old
    ~/.config/llm-deploy while only that one exists."""
    override = conf_dir_override(home, env)
    if override:
        return override
    new, old = home_path(home, CONF_DIR), home_path(home, LEGACY_CONF_DIR)
    return old if not is_dir(new) and is_dir(old) else new
