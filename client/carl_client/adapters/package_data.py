"""The values that ship with the package: client/carl_client/data/*.json (the PackageData port), and the domain's
records built from them (read once per process). The server reads the same files through this package
(decision 12); the zip carries them.
"""
from __future__ import annotations

import functools
import json
import os
import sys
from typing import Any, Dict, Optional, Tuple

from ..domain import legacy as legacy_rules
from ..domain import merge, models, remote

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


class PackageFiles:
    """data/<name>.json in a folder (default: the package's own)."""

    def __init__(self, folder: str = DATA) -> None:
        self.folder = folder

    def load(self, name: str) -> Any:
        path = os.path.join(self.folder, f"{name}.json")
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except OSError as e:
            raise ValueError(f"{path}: cannot be read ({e.strerror or e}); copy the client folder again") from None
        except ValueError as e:
            raise ValueError(f"{path}: not JSON ({e})") from None


@functools.lru_cache(maxsize=None)
def thinking() -> models.ModelRules:
    """The thinking rules and the clients' entry values (data/thinking.json)."""
    return models.parse_rules(PackageFiles().load("thinking"))


@functools.lru_cache(maxsize=None)
def legacy() -> legacy_rules.Legacy:
    """What earlier versions left in the clients' configs (data/legacy.json)."""
    return legacy_rules.parse(PackageFiles().load("legacy"))


@functools.lru_cache(maxsize=None)
def prompt_lines() -> Tuple[merge.OsLine, ...]:
    """The agent prompts' Windows lines (data/prompts.json)."""
    return merge.parse_os_lines(PackageFiles().load("prompts"))


@functools.lru_cache(maxsize=None)
def server() -> remote.Server:
    """CARL's server as a client knows it before remote.json says more (data/server.json)."""
    return remote.parse_server(PackageFiles().load("server"))


SERVER_WORDS = ("machine", "server", "host")


def os_name(platform: str = sys.platform) -> str:
    """The OS of a sys.platform value, as data/words.json names it: windows, macos, linux."""
    return {"win32": "windows", "darwin": "macos"}.get(platform, "linux")


def server_words(platform: Optional[str] = None) -> Dict[str, str]:
    """The words of the server's computer in the client's texts (data/words.json): machine (this Mac), server (the
    server Mac), host (the provider name's Mac host). The package of a server on another OS has its words. A clone's
    file names no server: the clone's setup is the one of the computer it runs on (carl
    install, the dashboard's Install key), so it takes that OS's words from by_os (platform: default this one's)."""
    doc = PackageFiles().load("words")
    if isinstance(doc, dict) and not all(k in doc for k in SERVER_WORDS) and isinstance(doc.get("by_os"), dict):
        name = os_name(sys.platform if platform is None else platform)
        table = doc["by_os"].get(name)
        if not (isinstance(table, dict) and all(isinstance(table.get(k), str) and table[k] for k in SERVER_WORDS)):
            raise ValueError(f"words.json: by_os.{name} must give {', '.join(SERVER_WORDS)} as texts")
        return {k: table[k] for k in SERVER_WORDS}
    if not (isinstance(doc, dict) and all(isinstance(doc.get(k), str) and doc[k] for k in SERVER_WORDS)):
        raise ValueError(f"words.json: {', '.join(SERVER_WORDS)} must be texts")
    return {k: doc[k] for k in SERVER_WORDS}


SYNC_WORDS = ("installer", "service_managers", "setup_command")


def sync_words(platform: str = sys.platform) -> Dict[str, str]:
    """The words of the help of carl-sync.py for an OS (data/words.json, sync): the Windows ones on win32, the
    macOS ones on darwin, the Linux ones otherwise."""
    doc = PackageFiles().load("words")
    table = doc.get("sync") if isinstance(doc, dict) else None
    name = os_name(platform)
    words = table.get(name) if isinstance(table, dict) else None
    if not (isinstance(words, dict) and all(isinstance(words.get(k), str) and words[k] for k in SYNC_WORDS)):
        raise ValueError(f"words.json: sync.{name} must give {', '.join(SYNC_WORDS)} as texts")
    return {k: words[k] for k in SYNC_WORDS}
