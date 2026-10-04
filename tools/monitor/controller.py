"""Turns keys and clicks into actions on the UI state: tabs, card levels, scrolling, the quit and
turn dialogs, and the typed values of the Server panel. The Settings panels' actions are in
settings_actions.py (the card edit form: card_actions.py), the Connect tab's in connect_actions.py."""
from __future__ import annotations

import signal
import time
from typing import Callable, Dict, List, NamedTuple

from . import system
from .api import Endpoint
from .clients import LABELS
from .connect_actions import ConnectActions
from .jobs import ServerJobs
from .keys import (BACKSPACE, END, END_ALT, ENTER, ESC, LEFT, LEFTKEY, RIGHT, SCROLL_KEYS, WHEEL_DOWN, WHEEL_UP, Click,
                   split_mouse, strip_escapes)
from .logtail import LogTail
from .model import ServerData
from .settings import NUMERIC, Pending, SettingsService
from .settings_actions import SETTINGS_ACTIONS, SettingsActions
from .settings_view import SettingsView
from .state import CONNECT_SUBPANELS, SP_FIT, SP_SERVER, TABS, UIState


class Region(NamedTuple):
    """A clickable screen area: 1-based row and columns (x1 exclusive)."""
    y: int
    x0: int
    x1: int
    action: str


class Controller:
    """Acts on input. data is the latest snapshot and regions the clickable areas of the
    last frame; the app keeps both current."""

    def __init__(self, ui: UIState, svc: SettingsService, jobs: ServerJobs, view: SettingsView, endpoint: Endpoint,
                 tail: LogTail, repo: str, home: str) -> None:
        self.ui = ui
        self.svc = svc
        self.jobs = jobs
        self.endpoint = endpoint
        self.tail = tail
        self.data = ServerData()
        self.regions: List[Region] = []
        self.connect = ConnectActions(ui, svc.models, jobs, endpoint, repo, home, lambda: self.data)
        self.settings = SettingsActions(ui, svc, jobs, view, endpoint, home, lambda: self.data, self.do, self.scroll)

    def pending_init(self, d: ServerData) -> Pending:
        """The Server panel's starting values (config.json and the running server)."""
        return self.settings.pending_init(d)

    def preview_text(self, kind: str, d: ServerData, mask: bool = False) -> str:
        """A client config for the running server (mask: the key hidden)."""
        return self.connect.preview_text(kind, d, mask)

    # ------------------------------------------------------------ actions
    def do(self, action: str) -> None:
        """Run an action: a clicked region's or button's, or one a key stands for."""
        ui, d = self.ui, self.data
        if action.startswith(("set", "sp:", "pick", "mrow:", "smodel:", "msortset:", "mfilterset:", "c2no", "card",
                              "fgoal:", "fscope:", "rmode", "rload:", "runload:", "tdepth:", "cache:")) \
                or action in SETTINGS_ACTIONS:
            self.settings.action(action)
        elif action.startswith("level:"):
            nm = action[6:]
            if nm in ui.levels:
                ui.levels[nm] = (ui.levels[nm] + 1) % 3
        elif action.startswith("tab:"):
            ui.tab = int(action[4:])
        elif action == "quit":
            ui.quit = True
        elif action == "cancel":
            ui.quit = False
        elif action == "key":
            ui.key_shown = not ui.key_shown
        elif action in LABELS:
            self.connect.show_config(action)
        elif action.startswith("ins"):
            self.connect.install_action(action)
        elif action.startswith("csp:"):
            ui.connect_sp = int(action[4:])
        elif action == "clforget":
            self.connect.forget_clients()
        elif action == "wrap":
            ui.wrap = not ui.wrap
        elif action == "errors":
            ui.errors_only = not ui.errors_only
            ui.log_scroll = 0
        elif action == "follow":
            ui.log_scroll = 0
        elif action == "detach":
            pid = d.target_pid
            if pid and not d.exited:
                ui.exit_msg = (f"Monitor closed. The server still runs (pid {pid}) at {self.endpoint.base}.\n"
                               f"  re-attach: ./carl.sh monitor --port {self.endpoint.port}\n  stop:      kill {pid}")
            raise SystemExit
        elif action == "stop":
            pid = d.target_pid
            if pid:
                def stop() -> None:
                    self.jobs.save_before_stop(self.data)   # the sessions in the slots (save = auto, switch, stop)
                    system.kill(pid, signal.SIGTERM)
                    ui.stopping = (pid, time.time() + 30)
                self.jobs.when_idle(d, "stop the server", stop)
            ui.quit = False
        elif action == "drain:now" and ui.drain:
            go, ui.drain = ui.drain.go, None
            go()
        elif action == "drain:wait" and ui.drain:
            ui.drain.waiting, ui.drain.since = True, time.time()
        elif action == "drain:cancel":
            ui.drain = None

    # ------------------------------------------------------------ input
    def scroll(self, step: int) -> None:
        """Up / down (step > 0 = up): moves the Settings row by one, else scrolls the tab's list."""
        ui = self.ui
        if ui.tab == 4 and ui.sp == SP_FIT:
            ui.fit_scroll = max(0, ui.fit_scroll - step)
        elif ui.tab == 4:
            if abs(step) != 1:                      # wheel / PgUp PgDn: scroll the Server panel (cards below the rows)
                ui.set_scroll = max(0, ui.set_scroll - step)
                return
            n = len(self.svc.rows(ui.pending)) if ui.pending else 1 + len(self.svc.schema.llama)
            ui.set_row = (ui.set_row - step) % n
            ui.set_scroll = 0                       # the rows are at the top: keep the selected one in view
        elif ui.tab == 3:
            ui.log_scroll = max(0, min(ui.log_scroll + step, max(len(self.tail.book.lines) - 5, 0)))
        elif ui.tab == 2:
            ui.req_scroll = max(0, ui.req_scroll - step)
        elif ui.tab == 1:
            ui.prev_scroll = max(0, ui.prev_scroll - step)
        else:
            ui.scroll = max(0, ui.scroll - step)

    def handle_click(self, c: Click) -> None:
        """A left click runs the action under it (only the dialog's, while one is open); the wheel scrolls."""
        ui = self.ui
        if c.button in (WHEEL_UP, WHEEL_DOWN):     # wheel: up = back in the log, up in lists
            self.scroll(3 if c.button == WHEEL_UP else -3)
            return
        if c.button != LEFT:
            return
        for r in self.regions:
            if r.y == c.y and r.x0 <= c.x < r.x1:
                if ui.drain and not r.action.startswith("drain:"):
                    return
                if ui.quit and r.action not in ("stop", "detach", "cancel", "quit"):
                    return
                if ui.confirm and r.action not in ("setyes", "setno"):
                    return
                self.do(r.action)
                return

    def handle_input(self, data: str) -> bool:
        """Act on keys and clicks. True = refresh the data now (space)."""
        ui = self.ui
        clicks, rest = split_mouse(data)
        for c in clicks:
            self.handle_click(c)
        if ui.drain:                              # a reply runs: y now, w wait, n / Esc cancel
            for ch in rest:
                if ch in "yYsS":
                    self.do("drain:now")
                    break
                if ch in "wW" and not ui.drain.waiting:
                    self.do("drain:wait")
                    break
                if ch in "nN" + ESC:
                    self.do("drain:cancel")
                    break
            return False
        if ui.confirm and not ui.quit:
            if rest == ESC:
                self.do("setno")
            for ch in rest:
                if ch in "yY":
                    self.do("setyes")
                elif ch in "nN":
                    self.do("setno")
            return False
        if ui.tab == 4 and not ui.quit and rest and ui.edit is None and self.settings.keys(rest):
            return False
        if ui.tab == 4 and ui.pending is not None and not ui.quit and ui.sp == SP_SERVER:
            key = self.settings.selected_key()
            if ui.edit is not None:               # typing a value: digits . k, Backspace, Enter, Esc
                for ch in rest:
                    if ch in "0123456789.kK":
                        ui.edit += ch.lower()
                    elif ch in BACKSPACE:
                        ui.edit = ui.edit[:-1]
                    elif ch in ENTER:
                        self.settings.commit_edit(key)
                        break
                    elif ch == ESC:
                        ui.edit = None
                        break
                return False
            if rest in ENTER and key in NUMERIC:
                ui.edit = ""
                return False
        if ui.quit:
            if rest == ESC:
                self.do("cancel")
            for ch in rest:
                if ch in "sS":
                    self.do("stop")
                elif ch in "dDqQ":
                    self.do("detach")
                elif ch in "nNcC":
                    self.do("cancel")
            return False
        for seq, step in SCROLL_KEYS.items():
            if seq in rest:
                self.scroll(step)
        if ui.tab == 4 and ui.pending is not None and ui.sp == SP_SERVER:
            if RIGHT in rest:
                self.do(f"setinc:{ui.set_row}")
            if LEFTKEY in rest:
                self.do(f"setdec:{ui.set_row}")
        if END in rest or END_ALT in rest:
            ui.log_scroll = 0
        if ui.tab == 1 and ui.install_ask and rest == ESC:
            self.do("insno")
            return False
        return self.keys(strip_escapes(rest))

    def keys(self, rest: str) -> bool:
        """Plain key presses. True = refresh now."""
        ui = self.ui
        simple: Dict[str, Callable[[], None]] = {
            "k": lambda: self.do("key"), "o": lambda: self.do("opencode"), "p": lambda: self.do("pi"),
            "t": lambda: self.do("curl"), "w": lambda: self.do("wrap"), "f": lambda: self.do("errors"),
            "e": lambda: ui.levels.update({x: 2 for x in ui.levels}), "c": lambda: ui.levels.update({x: 0 for x in ui.levels})}
        for ch in rest:
            if ch in "qQ\x03":
                self.do("quit")
            elif ch in "12345":
                ui.tab = int(ch) - 1
            elif ui.tab == 1 and ui.install_ask and ch in "yYnN":
                self.do("insyes" if ch in "yY" else "insno")
            elif ui.tab == 1 and ch in "iuxP":
                self.do({"i": "insall", "u": "insconfig", "x": "insclose", "P": "inspush"}[ch])
            elif ui.tab == 1 and ch in "[]":
                ui.connect_sp = (ui.connect_sp + 1) % len(CONNECT_SUBPANELS)
            elif ui.tab == 4 and ui.sp == SP_SERVER and ch in "arxA":
                self.do({"a": "setapply", "r": "setrevert", "x": "setdefaults", "A": "setautofit"}[ch])
            elif ch == "\t":
                ui.tab = (ui.tab + 1) % len(TABS)
            elif ch in simple:
                simple[ch]()
            elif ch in "+=":
                ui.lines = min(ui.lines + 2, 60)
            elif ch in "-_":
                ui.lines = max(ui.lines - 2, 2)
            elif ch == "?":
                ui.help = not ui.help
            elif ch == " ":
                return True
        return False
