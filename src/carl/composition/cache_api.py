"""The dashboard API assembled: the HTTP server (adapters/http/dashboard_api_server.py) over the routes
(application/cache_api.py), the slots folder's claims (adapters/common/slot_claims.py), the clients' registry
(adapters/common/client_registry.py) and the settings folder's files (the published client config, the cache events,
the server files). CacheApi keeps the form the dashboard (composition/dashboard.py), the contract tests and the old
name tools/monitor/cacheapi.py (an alias of this module until W9.4) build it in: host, port, key, the slots folder
and the Caching settings, then the settings folder and this CARL's version and ID.

Nothing is built at import time.
"""
from __future__ import annotations

import os
from typing import Callable, Dict, Optional

from ..adapters.common.client_registry import CLIENT_ID, Client, Registry, parse_client  # noqa: F401  (their names here)
from ..adapters.common.slot_claims import CLAIM_STALE_S, TURN_STALE_S, SlotClaims, safe_name  # noqa: F401
from ..adapters.http.dashboard_api_server import HttpApi
from ..application.cache_api import CacheApiFiles, CacheApiService, CacheSettingsView, Reply
from ..domain.server.snapshot import JSONDict

CacheState = SlotClaims                 # the claims' name before W5.3 (the contract tests and the benches use it)


def _command() -> str:
    """The command word of the dashboard's platform (a message names the push command)."""
    from .entries import build_dashboard
    return build_dashboard().words.command


def published(carl_dir: str) -> Callable[[], Optional[JSONDict]]:
    """The published client config of a settings folder (None: none yet, or no folder)."""
    from ..adapters.common import client_config_store
    return (lambda: client_config_store.published(carl_dir)) if carl_dir else (lambda: None)


def service(claims: SlotClaims, conf: Callable[[], CacheSettingsView],
            save_recorded: Callable[[str], int] = lambda model: 0, carl_dir: str = "") -> CacheApiService:
    """The routes over the claims of a slots folder; carl_dir: the settings folder ("": none, so no client config, no
    events and no server files)."""
    from ..adapters.common import cache_events, runtime_files, zstd_patch
    folder = claims.folder
    files = CacheApiFiles(published=published(carl_dir),
                          append_events=lambda docs, host: int(cache_events.append(carl_dir, docs, host, folder)),
                          server_file=lambda port: runtime_files.read(carl_dir, port)) if carl_dir else None
    return CacheApiService(claims, conf, lambda name: bool(zstd_patch.unpack(folder, name)), files, save_recorded,
                           _command)


def handle(state: SlotClaims, conf: Callable[[], CacheSettingsView], method: str, path: str, query: Dict[str, str],
           body: JSONDict, save_recorded: Callable[[str], int] = lambda model: 0, carl_dir: str = "",
           etag: str = "", server_port: int = 0, status: Optional[JSONDict] = None, host: str = "") -> Reply:
    """One API call in one step (the tests): service(...).handle(...)."""
    return service(state, conf, save_recorded, carl_dir).handle(method, path, query, body, etag, server_port, status,
                                                                host)


class CacheApi(HttpApi):
    """The dashboard API for the slots folder `folder`: started by start(), which returns why it could not start (None
    when it runs). carl_dir: the settings folder (clients.json, the published client config, the events, the server
    files); server_port(): the server's port now; status(): the dashboard's last snapshot of the speeds."""

    def __init__(self, host: str, port: int, key: Callable[[], str], folder: str, conf: Callable[[], CacheSettingsView],
                 save_recorded: Callable[[str], int] = lambda model: 0, carl_dir: str = "", carl_version: str = "",
                 carl_id: str = "", server_port: Callable[[], int] = lambda: 0,
                 status: Callable[[], JSONDict] = dict) -> None:
        self.state = SlotClaims(folder)
        self.service = service(self.state, conf, save_recorded, carl_dir)
        registry = Registry(os.path.join(carl_dir, "clients.json") if carl_dir else os.devnull)

        def route(method: str, path: str, query: Dict[str, str], body: JSONDict, etag: str, caller: str) -> Reply:
            return self.service.handle(method, path, query, body, etag, server_port(), status(), caller)
        super().__init__(host, port, key, route, published(carl_dir), registry, carl_version, carl_id)
