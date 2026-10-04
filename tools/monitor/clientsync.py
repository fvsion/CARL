"""The client config OpenCode and Pi on other computers sync from (client/carl-sync.py): what the
server decides for them, published on purpose (the Connect tab's Push, or ./carl.sh push), not on
every change. Today: the installed models (each one's window, thinking, label) and the default model,
as tools/carl.py client-models lists them. The version is a hash of that content, so a push of the
same config doesn't make the clients apply it again.

~/.config/carl/client-config.json: {"version", "published", "models"}. The dashboard's API
(cacheapi.py) serves it and tells listening clients when the version changes. Pure, except
publish() and published().
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Optional

from .model import JSONDict, jdict

FILE = "client-config.json"


def version_of(models: JSONDict) -> str:
    return hashlib.sha256(json.dumps(models, sort_keys=True).encode()).hexdigest()[:12]


def publish(folder: str, models: JSONDict) -> str:
    """Write the config (atomically); its version."""
    version = version_of(models)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, FILE)
    doc = {"version": version, "published": time.strftime("%Y-%m-%dT%H:%M:%S"), "models": models}
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
    os.chmod(path + ".tmp", 0o600)
    os.replace(path + ".tmp", path)
    return version


def published(folder: str) -> Optional[JSONDict]:
    """The published config, or None (never pushed, or unreadable)."""
    try:
        with open(os.path.join(folder, FILE), encoding="utf-8") as f:
            doc = jdict(json.load(f))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc.get("version"), str) and isinstance(doc.get("models"), dict) else None
