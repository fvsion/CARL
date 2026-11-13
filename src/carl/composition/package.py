"""Composition of the client package: the service over the file, version and zip adapters, and the calls the CLI and
the dashboard make (carl package, Connect > Setup, the dashboard API's port). Nothing is built at import.
"""
from __future__ import annotations

import os
import time
from typing import Mapping, Optional, Sequence

from ..adapters.common.git_files import ClientFolder
from ..adapters.common.json_files import JsonFile
from ..adapters.common.private_files import Note, Private, keep_private
from ..adapters.common.version import ChangelogVersion
from ..adapters.common.zip_writer import ZipWriter, host_name
from ..application import package
from ..application.package import PackageService
from ..domain.clients.package import Outcome
from ..domain.shared.platform_words import MACOS, PlatformWords
from ..ports import PluginHost
from ..ports.plugin import ClientFile

__all__ = ["build_service", "carl_version", "host_name", "make_package", "plugin_files", "private_files",
           "publish_api_port"]


def private_files(os_name: str) -> Optional[Private]:
    """What leaves a copy of the key to its owner on this OS: on Windows the key file's own access-list fix
    (adapters/windows/secrets.py), where a file mode does nothing; None elsewhere (the 0600 mode is enough)."""
    if os_name != "windows":
        return None
    from ..adapters.windows.secrets import make_private
    return make_private


def build_service(repo: str, os_name: str = "", note: Optional[Note] = None) -> PackageService:
    """The package service of one clone (repo: the folder of its CHANGELOG.md and client/). os_name: the profile's, for
    the zip's privacy on Windows; note: where its warning goes (None: nowhere, as in the dashboard's screen)."""
    return PackageService(ClientFolder(), ZipWriter(private_files(os_name), note), ChangelogVersion(repo), time.time,
                          host_name)


def plugin_files(plugins: Optional[PluginHost]) -> Sequence[ClientFile]:
    """The files the plugins that are on add to the client package (none when there is no seam or it fails)."""
    try:
        return list(plugins.client_files()) if plugins is not None else []
    except Exception:  # noqa: BLE001  (a plugin never stops the package)
        return []


def make_package(repo: str, client_dir: str, out_dir: str, models: object, cfg: Mapping[str, object],
                 words: PlatformWords = MACOS, anyway: bool = False, os_name: str = "",
                 note: Optional[Note] = None, plugins: Optional[PluginHost] = None) -> Outcome:
    """The client package of a clone (PackageService.make), with the files of the plugins that are on (plugins)."""
    return build_service(repo, os_name, note).make(repo, client_dir, out_dir, models, cfg, words, anyway,
                                                   plugin_files(plugins))


def carl_version(repo: str) -> str:
    """CARL's version in a clone: the newest CHANGELOG entry, else git describe, else "unknown"."""
    return ChangelogVersion(repo).version()


def publish_api_port(repo: str, port: int, cache_api: str, client: Optional[str] = None, os_name: str = "",
                     note: Optional[Note] = None) -> bool:
    """The dashboard API's real address into remote.json (application/package.py). client: the client folder
    (CARL_CLIENT_DIR, which wiring.client_dir reads), else client/ in repo. os_name: on Windows the rewritten file
    (a new file with the folder's list) is left to its owner again, as the start left it."""
    path = os.path.join(client or os.path.join(repo, "client"), "remote.json")
    changed = package.publish_api_port(JsonFile(path), port, cache_api)
    if changed:
        keep_private(path, private_files(os_name), note)
    return changed
