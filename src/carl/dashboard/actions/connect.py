"""The Connect tab's actions: the client configs (shown and copied to the clipboard), the installer
for this computer, the client package for other computers, the config push for the clients that sync,
and forgetting old clients. They reach the computer through the jobs' host (JobsHost) and the settings through the
settings service."""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Optional

from ...application.collector import SHAPE_ERRORS
from ...application.model_list import ModelList
from ...application.server_jobs import FETCH_ERRORS, ServerJobs
from ...composition.dashboard import host as dashboard_host
from ...domain.server.snapshot import ServerData, clean
from ...ports import ServerEndpoint
from ...ui.fmt import R, RED
from ..act import Action, Verb
from ..clients import LABELS, config_text, fill_template, masked, model_list, served, serving
from ..state import UIState

TEMPLATES = {"opencode": "opencode/opencode.json", "pi": "pi/models.json"}     # client config templates in client/
FORGET_AFTER_S = 7 * 86400          # the Clients panel's Forget: clients not seen for a week
GATES = (0, 1, 2, 3, 5)             # the new-file gate's steps (g); 0 = off
PACKAGE_VERBS = (Verb.PKGMAKE, Verb.PKGSHOW, Verb.PKGCLOSE)


class ConnectActions:
    """Acts on the Connect tab. snapshot() is the latest data the app collected."""

    def __init__(self, ui: UIState, models: ModelList, jobs: ServerJobs, endpoint: ServerEndpoint, repo: str,
                 home: str, snapshot: Callable[[], ServerData]) -> None:
        self.ui = ui
        self.models = models
        self.jobs = jobs
        self.endpoint = endpoint
        self.repo = repo
        self.home = home
        self.snapshot = snapshot
        self._rules: Any = None             # the client package's model-entry rules, read at the first preview

    def rules(self) -> Any:
        """The client package's rules for the model entries (through the dashboard's composition)."""
        if self._rules is None:
            self._rules = dashboard_host().client_rules()
        return self._rules

    def cycle_gate(self) -> None:
        """g: the new-file gate of carl-delegation, the next of GATES (off, 1, 2, 3, 5): config.json delegation.gate.
        OpenCode and Pi read it from the dashboard within 10 s (on another computer through the dashboard API). A
        setting of the dashboard only (user, 2026-10-08); advanced, not recommended."""
        ui = self.ui
        try:
            nxt = self.jobs.svc.step_gate(GATES)
        except Exception as e:      # config.json unreadable or not writable: say so
            ui.toast(f"{RED}CARL cannot save config.json: {e}{R}", 10)
            return
        self.jobs.cache_conf(fresh=True)
        ui.toast("New-file gate: off. The main agent writes new files itself." if not nxt else
                 f"New-file gate: {nxt}. OpenCode and Pi stop the main agent at its new file number {nxt} in a turn "
                 f"and tell it to use the coder.", 8)

    def preview_text(self, kind: str, d: ServerData, mask: bool = False) -> str:
        """Config text for the running server. mask=True hides the key (on-screen preview)."""
        if not d.up:
            return "No server is running. The config shows when a model is loaded."
        ep = self.endpoint
        rel = TEMPLATES.get(kind)
        templates = {}
        if rel:     # read each time: the setup may update the bundle while the monitor runs
            templates[kind] = fill_template(self.jobs.host.read_text(os.path.join(self.repo, "client", rel)), ep.host,
                                            ep.port, self.home, self.jobs.host.words.host_label)
        s = served(d, self.alias_from_server)
        text = config_text(kind, s, templates, ep.base, ep.key,
                           model_list(self.models.client_list(*serving(d)), s, self.rules()))
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
        ui.install_shown = ui.package_shown = False     # the preview takes the place of the installer's output
        if not d.up:
            ui.toast("No server is running: CARL copied nothing.")
            return
        ok = self.jobs.host.desktop.copy(self.preview_text(kind, d))
        ui.copied = kind if ok else None
        ui.toast(LABELS[kind] + (" copied to the clipboard." if ok else ": CARL cannot use the clipboard here. Select the "
                                                                        "text on the screen."), error=not ok)

    def act(self, a: Action) -> None:
        """An action of the Connect tab that this module runs: a config shown and copied, the installer, the client
        package, Forget."""
        verb = a.verb
        if verb in (Verb.OPENCODE, Verb.PI, Verb.CURL):
            self.show_config(verb.value)
        elif verb is Verb.CLFORGET:
            self.forget_clients()
        elif verb in PACKAGE_VERBS:
            self.package_action(a)
        else:
            self.install_action(a)

    def install_action(self, a: Action) -> None:
        """The Connect tab's installer: insall / insconfig ask first, insyes runs it, insno cancels
        the question, insshow / insclose show / hide its output, inscancel stops it; inspush
        publishes the client config."""
        ui, verb = self.ui, a.verb
        ui.tab = 1
        running = bool(ui.install and not ui.install.done)
        if verb is Verb.INSPUSH:
            self.jobs.push_client_config(*serving(self.snapshot()), full=ui.full)
            return
        if verb in (Verb.INSALL, Verb.INSCONFIG):
            if running:
                ui.install_shown = True
                ui.toast("The installer is already running. Its output is below.", 5)
            else:
                ui.install_ask = "all" if verb is Verb.INSALL else "config"
        elif verb is Verb.INSYES and ui.install_ask:
            config_only, ui.install_ask = ui.install_ask == "config", None
            self.jobs.start_install(config_only)
        elif verb is Verb.INSNO:
            ui.install_ask = None
        elif verb is Verb.INSSHOW:
            ui.install_shown = bool(ui.install)
            ui.package_shown = ui.package_shown and not ui.install_shown
        elif verb is Verb.INSCLOSE:
            ui.install_shown = ui.package_shown = False
        elif verb is Verb.INSCANCEL and running:
            self.jobs.cancel_install()
            ui.toast("You stopped the installer. It is safe to run it again: the backups stay.", 8)

    def package_action(self, a: Action) -> None:
        """The client package: pkgmake makes it (z), pkgshow shows the zip in the Finder (f), pkgclose hides
        its card."""
        ui, verb = self.ui, a.verb
        ui.tab, ui.connect_sp = 1, 0
        if verb is Verb.PKGMAKE:
            self.jobs.make_package(*serving(self.snapshot()))
        elif verb is Verb.PKGSHOW:
            self.jobs.show_package()
        elif verb is Verb.PKGCLOSE:
            ui.package_shown = False

    def forget_clients(self) -> None:
        """The Clients panel's Forget: drop the clients not seen for a week."""
        n = self.jobs.forget_clients(FORGET_AFTER_S)
        self.ui.toast(f"CARL forgot {n} {'computer' if n == 1 else 'computers'} not seen for a week.", 6)
