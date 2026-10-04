"""The Connect tab's actions: the client configs (shown and copied to the clipboard), the installer
for this Mac, the config push for the clients that sync, and forgetting old clients."""
from __future__ import annotations

import json
import os
from typing import Callable, Optional

from . import fsio, system
from .api import FETCH_ERRORS, Endpoint
from .clients import LABELS, config_text, fill_template, masked, model_list, served
from .collector import SHAPE_ERRORS
from .jobs import ServerJobs
from .model import ServerData, clean
from .state import UIState
from .store import ModelList

TEMPLATES = {"opencode": "opencode/opencode.json", "pi": "pi/models.json"}     # client config templates in client/
FORGET_AFTER_S = 7 * 86400          # the Clients panel's Forget: clients not seen for a week


class ConnectActions:
    """Acts on the Connect tab. snapshot() is the latest data the app collected."""

    def __init__(self, ui: UIState, models: ModelList, jobs: ServerJobs, endpoint: Endpoint, repo: str, home: str,
                 snapshot: Callable[[], ServerData]) -> None:
        self.ui = ui
        self.models = models
        self.jobs = jobs
        self.endpoint = endpoint
        self.repo = repo
        self.home = home
        self.snapshot = snapshot

    def preview_text(self, kind: str, d: ServerData, mask: bool = False) -> str:
        """Config text for the running server. mask=True hides the key (on-screen preview)."""
        if not d.up:
            return "(no connection to the server yet: the config shows after the model loads)"
        ep = self.endpoint
        rel = TEMPLATES.get(kind)
        templates = {}
        if rel:     # read each time: install.sh may update the bundle while the monitor runs
            templates[kind] = fill_template(fsio.read_text(os.path.join(self.repo, "client", rel)), ep.host, ep.port, self.home)
        s = served(d, self.alias_from_server)
        text = config_text(kind, s, templates, ep.base, ep.key, model_list(self.models.client_list(), s))
        return masked(text, ep.key) if mask else text

    def alias_from_server(self) -> Optional[str]:
        """The first model id of /v1/models."""
        try:
            return clean(str(json.loads(self.endpoint.get("/v1/models"))["data"][0]["id"]))
        except FETCH_ERRORS + SHAPE_ERRORS:
            return None

    def show_config(self, kind: str) -> None:
        """Show a config on the Connect tab and copy it to the clipboard."""
        ui, d = self.ui, self.snapshot()
        ui.preview, ui.prev_scroll, ui.tab = kind, 0, 1
        ui.install_shown = False                # the preview takes the installer's place
        if not d.up:
            ui.toast("no connection to the server: nothing copied")
            return
        ok = system.copy_to_clipboard(self.preview_text(kind, d))
        ui.copied = kind if ok else None
        ui.toast(LABELS[kind] + (" copied to the clipboard" if ok else ": no clipboard. Select the text on the screen."))

    def install_action(self, act: str) -> None:
        """The Connect tab's installer: insall / insconfig ask first, insyes runs it, insno cancels
        the question, insshow / insclose show / hide its output, inscancel stops it; inspush
        publishes the client config."""
        ui = self.ui
        ui.tab = 1
        running = bool(ui.install and not ui.install.done)
        if act == "inspush":
            self.jobs.push_client_config()
            return
        if act in ("insall", "insconfig"):
            if running:
                ui.install_shown = True
                ui.toast("the installer runs already: its output is below", 5)
            else:
                ui.install_ask = "all" if act == "insall" else "config"
        elif act == "insyes" and ui.install_ask:
            config_only, ui.install_ask = ui.install_ask == "config", None
            self.jobs.start_install(config_only)
        elif act == "insno":
            ui.install_ask = None
        elif act == "insshow":
            ui.install_shown = bool(ui.install)
        elif act == "insclose":
            ui.install_shown = False
        elif act == "inscancel" and running:
            self.jobs.cancel_install()
            ui.toast("installer stopped: you can safely run it again (the backups stay)", 8)

    def forget_clients(self) -> None:
        """The Clients panel's Forget: drop the clients not seen for a week."""
        n = self.jobs.forget_clients(FORGET_AFTER_S)
        self.ui.toast(f"forgot {n} client(s) not seen for a week", 6)
