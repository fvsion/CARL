"""The dashboard's own saved state (not config.json, which has a schema the launchers check): the
detail level (D), in dashboard.json next to config.json. A file that can't be read or written
is skipped: the dashboard then starts in simple detail."""
from __future__ import annotations

import json
import os

from . import fsio
from .state import DETAILS

FILE = "dashboard.json"


def path_for(config_file: str) -> str:
    """dashboard.json in the settings folder of config_file (CARL_CONF_DIR is honoured there)."""
    return os.path.join(os.path.dirname(config_file), FILE)


def load_detail(path: str) -> str:
    """The saved detail level (simple when there is none or the file is broken)."""
    try:
        with open(path, encoding="utf-8") as f:
            v = json.load(f).get("detail")
    except (OSError, ValueError, AttributeError):
        return DETAILS[0]
    return v if v in DETAILS else DETAILS[0]


def save_detail(path: str, detail: str) -> bool:
    """Save the detail level; False when the folder can't be written (the level still applies now)."""
    try:
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
            doc = doc if isinstance(doc, dict) else {}
        except (OSError, ValueError):
            doc = {}
        doc["detail"] = detail
        if not os.path.isdir(os.path.dirname(path)):
            return False
        fsio.write_private(path, json.dumps(doc, indent=2) + "\n")
        return True
    except OSError:
        return False
