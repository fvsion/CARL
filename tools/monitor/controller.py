"""Turns keys and clicks into actions on the UI state: tabs, the detail level (D), card levels,
scrolling, the quit and "an agent is working" dialogs. Input is handled key by key (keys typed
fast arrive in one read). The Settings tab's keys and actions are in settings_actions.py (the card
edit form: card_actions.py), the Connect tab's actions in connect_actions.py."""
from __future__ import annotations

import signal
import time
from typing import List, NamedTuple

from . import system, uiprefs
from .api import Endpoint
from .clients import LABELS
from .connect_actions import ConnectActions
from .jobs import ServerJobs
from .keys import END, END_ALT, ENTER, ESC, LEFT, SCROLL_KEYS, WHEEL_DOWN, WHEEL_UP, Click, split_keys, split_mouse
from .logtail import LogTail
from .model import ServerData
from .settings import Pending, SettingsService
from .settings_actions import SETTINGS_ACTIONS, SettingsActions
from .settings_view import SettingsView
from .cards import NO_COLLAPSE, THREE_LEVELS
from .state import (CONNECT_SUBPANELS, DETAILS, SP_AGENTS, SP_CACHE, SP_FIT, SP_MODELS, SP_ROUTER, SP_SERVER, SP_TUNE,
                    UIState)

SHIFT_TAB = "\x1b[Z"


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
                 tail: LogTail, repo: str, home: str, prefs: str = "") -> None:
        self.ui = ui
        self.prefs = prefs              # the dashboard's own saved state (uiprefs.py): the detail level
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
                              "fgoal:", "fscope:", "rmode", "rload:", "runload:", "tdepth:", "cache:", "agents:")) \
                or action in SETTINGS_ACTIONS:
            self.settings.action(action)
        elif action.startswith("level:"):
            nm = action[6:]
            if nm in ui.levels:
                ui.section = nm
                self.cycle_level(nm)
        elif action == "detail":
            self.toggle_detail()
        elif action == "livestart":
            self.start_from_live()
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
        elif action.startswith("pkg"):
            self.connect.package_action(action)
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
                ui.exit_msg = (f"The dashboard closed. The server is still running at {self.endpoint.base}.\n"
                               f"  To open the dashboard again: ./carl.sh\n"
                               f"  To stop the server: ./carl.sh, then q, then s.")
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

    # ------------------------------------------------------------ the detail level, a start from Live
    def toggle_detail(self) -> None:
        """D: simple <-> full, everywhere: every section takes that level (a collapsed one opens); saved for the
        next start of the dashboard."""
        ui = self.ui
        ui.detail = DETAILS[(DETAILS.index(ui.detail) + 1) % len(DETAILS)] if ui.detail in DETAILS else DETAILS[0]
        lvl = 2 if ui.detail == "full" else 1
        for nm in ui.levels:
            ui.levels[nm] = lvl if nm in THREE_LEVELS else 1
        self.save_levels()

    def cycle_level(self, nm: str) -> None:
        """A section's next level: simple -> full -> collapsed -> simple (two-level sections: open <-> collapsed)."""
        ui = self.ui
        cur = ui.levels.get(nm, 1)
        if nm in NO_COLLAPSE:
            ui.levels[nm] = 1 if cur == 2 else 2
        else:
            ui.levels[nm] = {1: 2, 2: 0, 0: 1}[cur] if nm in THREE_LEVELS else (0 if cur else 1)
        self.save_levels()

    def select_section(self, step: int) -> None:
        """Tab / Shift-Tab: the next or previous section of the screen shown."""
        ui = self.ui
        if not ui.sections:
            return
        i = ui.sections.index(ui.section) if ui.section in ui.sections else -1 if step > 0 else 0
        ui.section = ui.sections[(i + step) % len(ui.sections)]

    def save_levels(self) -> None:
        if self.prefs:
            uiprefs.save_detail(self.prefs, self.ui.detail, self.ui.levels)

    def start_from_live(self) -> None:
        """a on the Live tab with no server: Settings > Server, and its question "start the server?"."""
        ui = self.ui
        ui.tab, ui.sp = 4, SP_SERVER
        if ui.pending is None:
            ui.pending, ui.set_run = self.settings.pending_init(self.data), self.svc.running(self.data)
        self.settings.action("setapply")

    # ------------------------------------------------------------ input
    def scroll(self, step: int) -> None:
        """The mouse wheel and the scroll keys (step > 0 = up): the list or panel of the screen shown."""
        ui = self.ui
        if ui.tab == 4:
            if ui.sp == SP_FIT:
                ui.fit_scroll = max(0, ui.fit_scroll - step)
            elif ui.sp == SP_MODELS:
                ui.mrow = max(ui.mrow - step, 0)
            elif ui.sp in (SP_AGENTS, SP_TUNE, SP_ROUTER, SP_CACHE):     # these pages scroll as one (Phase 23.2)
                ui.page_scroll = max(0, ui.page_scroll - step)
            else:
                ui.set_scroll = max(0, ui.set_scroll - step)
        elif ui.tab == 3:
            ui.log_scroll = max(0, min(ui.log_scroll + step, max(len(self.tail.book.lines) - 5, 0)))
        elif ui.tab == 2:
            ui.req_scroll = max(0, ui.req_scroll - step)
        elif ui.tab == 1:
            if ui.section == "cpreview":                 # the preview has the focus: it scrolls on its own
                ui.prev_scroll = max(0, ui.prev_scroll - step)
            else:
                ui.conn_scroll = max(0, ui.conn_scroll - step)
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

    def typing(self) -> bool:
        """A text is being typed in the Settings tab (a Hugging Face repo, a card field)."""
        ui = self.ui
        return ui.tab == 4 and not (ui.quit or ui.drain or ui.confirm) and bool(
            ui.text or (ui.card is not None and ui.card.typing is not None))

    def handle_input(self, data: str) -> bool:
        """Act on clicks, then on each key in order. True = read the server again now (space). A text
        being typed takes the whole input (a paste stays one text)."""
        clicks, rest = split_mouse(data)
        for c in clicks:
            self.handle_click(c)
        if not rest:
            return False
        if self.typing():
            self.settings.keys(rest)
            return False
        refresh = False
        for k in split_keys(rest):
            refresh = self.key(k) or refresh
        return refresh

    def key(self, k: str) -> bool:
        """One key: a dialog's first, then the Settings tab's, then the keys of every screen and of the
        tab shown. True = read the server again now."""
        ui = self.ui
        if ui.drain:                              # an agent is working: s / y stop now, w wait, n / Esc cancel
            if k in ("s", "S", "y", "Y"):
                self.do("drain:now")
            elif k in ("w", "W") and not ui.drain.waiting:
                self.do("drain:wait")
            elif k in ("n", "N", ESC):
                self.do("drain:cancel")
            return False
        if ui.quit:                               # s stop, l / q / d leave it running, n / c / Esc cancel
            if k in ("s", "S"):
                self.do("stop")
            elif k in ("l", "L", "d", "q", "Q"):
                self.do("detach")
            elif k in ("n", "N", "c", "C", ESC):
                self.do("cancel")
            return False
        if ui.confirm:                            # Apply's question: y / Enter yes, n / Esc no
            if k in ("y", "Y") or k in ENTER:
                self.do("setyes")
            elif k in ("n", "N", ESC):
                self.do("setno")
            return False
        if ui.tab == 4 and self.settings.key(k):
            return False
        if ui.tab == 1 and ui.install_ask:        # the installer's question
            if k in ("y", "Y") or k in ENTER:
                self.do("insyes")
                return False
            if k in ("n", "N", ESC):
                self.do("insno")
                return False
        if k in ("q", "Q", "\x03"):
            self.do("quit")
        elif k in ("1", "2", "3", "4", "5"):
            ui.tab = int(k) - 1
        elif k == "\t":
            self.select_section(1)
        elif k == SHIFT_TAB:
            self.select_section(-1)
        elif k == "L" and ui.section in ui.sections:
            self.cycle_level(ui.section)
        elif k == "?":
            ui.help = not ui.help
        elif k == "D":
            self.toggle_detail()
        elif k == " ":
            return True
        elif k in SCROLL_KEYS:
            self.scroll(SCROLL_KEYS[k])
        elif k in (END, END_ALT):
            ui.log_scroll = 0
        elif ui.tab in (0, 1) and k in ("k", "o", "p", "c", "t"):
            self.do({"k": "key", "o": "opencode", "p": "pi", "c": "curl", "t": "curl"}[k])
        elif ui.tab == 0 and k == "a" and (self.data.exited or not (self.data.up or self.data.pid)):
            self.do("livestart")
        elif ui.tab == 0 and k in ("+", "="):
            ui.lines = min((ui.lines or 3) + 2, 60)
        elif ui.tab == 0 and k in ("-", "_"):
            ui.lines = max((ui.lines or 5) - 2, 2)
        elif ui.tab == 1 and k in ("i", "u", "x", "P", "z", "f"):
            self.do({"i": "insall", "u": "insconfig", "x": "insclose", "P": "inspush", "z": "pkgmake",
                     "f": "pkgshow"}[k])
        elif ui.tab == 1 and k == "g" and ui.connect_sp == 0:
            self.connect.cycle_gate()
        elif ui.tab == 1 and k in ("[", "]"):
            ui.connect_sp = (ui.connect_sp + 1) % len(CONNECT_SUBPANELS)
        elif ui.tab == 3 and k in ("w", "f"):
            self.do("wrap" if k == "w" else "errors")
        return False
