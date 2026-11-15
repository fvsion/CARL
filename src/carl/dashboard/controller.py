"""Turns keys and clicks into actions on the UI state: tabs, the detail level (D), card levels,
scrolling, the quit and "an agent is working" dialogs. Input is handled key by key (keys typed
fast arrive in one read). An action is a typed Action (act.py): the controller sends it to the part that owns its verb,
never by the start of its text. The Settings tab's keys and actions are in actions/settings/ (one module for each
panel; the card edit form: actions/card.py), the Connect tab's actions in actions/connect.py."""
from __future__ import annotations

import time
from typing import Callable, Dict, List, NamedTuple, Optional

from ..application.logbook import LogTail
from ..application.server_jobs import ServerJobs
from ..application.settings_service import Pending, SettingsService
from ..domain.server.snapshot import ServerData
from ..ports import PluginHost, ServerEndpoint
from ..ports.plugin import SLOTS
from ..ui.words import command
from .act import Action, DrainOp, Owner, Verb, act, parse
from .actions.connect import ConnectActions
from .actions.settings import SettingsActions
from .keys import END, END_ALT, ENTER, ESC, LEFT, SCROLL_KEYS, WHEEL_DOWN, WHEEL_UP, Click, split_keys, split_mouse
from .render.settings import SettingsView
from .sections import NO_COLLAPSE, THREE_LEVELS, is_plugin_section
from .state import (CONNECT_SUBPANELS, DETAILS, SP_AGENTS, SP_CACHE, SP_FIT, SP_MODELS, SP_ROUTER, SP_SERVER, SP_TUNE,
                    SUBPANELS, UIState)

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

    def __init__(self, ui: UIState, svc: SettingsService, jobs: ServerJobs, view: SettingsView,
                 endpoint: ServerEndpoint, tail: LogTail, repo: str, home: str, prefs: str = "",
                 plugins: Callable[[], Optional[PluginHost]] = lambda: None,
                 save_prefs: Callable[[str, str, Dict[str, int]], bool] = lambda path, detail, levels: False) -> None:
        self.ui = ui
        self.plugins = plugins          # the plugins' cards have buttons and keys (WP)
        self.prefs = prefs              # the dashboard's own saved state (the file of the detail and the levels)
        self.save_prefs = save_prefs    # writes it: (path, detail, levels) -> saved
        self.svc = svc
        self.jobs = jobs
        self.endpoint = endpoint
        self.tail = tail
        self.data = ServerData()
        self.regions: List[Region] = []
        self.connect = ConnectActions(ui, svc.models, jobs, endpoint, repo, home, lambda: self.data)
        self.settings = SettingsActions(ui, svc, jobs, view, endpoint, home, lambda: self.data, self.do, self.scroll,
                                        plugins)

    def pending_init(self, d: ServerData) -> Pending:
        """The Server panel's starting values (config.json and the running server)."""
        return self.settings.pending_init(d)

    def preview_text(self, kind: str, d: ServerData, mask: bool = False) -> str:
        """A client config for the running server (mask: the key hidden)."""
        return self.connect.preview_text(kind, d, mask)

    # ------------------------------------------------------------ actions
    def do(self, action: str) -> None:
        """Run an action: a clicked region's or button's, or one a key stands for. The part that owns its verb runs
        it; text that is no verb of the vocabulary (act.py) does nothing."""
        a = parse(action)
        if a is None:
            return
        owner = a.owner
        if owner is Owner.APP:
            self.app_action(a)
        elif owner is Owner.CONNECT:
            self.connect.act(a)
        else:
            self.settings.action(a)

    def app_action(self, a: Action) -> None:
        """The actions of the dashboard itself: the tabs, the levels, the dialogs, the Log tab, a plugin's card."""
        ui, d, verb = self.ui, self.data, a.verb
        if verb is Verb.PLUGACT:
            self.plugin_action(*a.arg.split(":", 2))
        elif verb is Verb.LEVEL:
            if a.arg in ui.levels:
                ui.section = a.arg
                self.cycle_level(a.arg)
        elif verb is Verb.DETAIL:
            self.toggle_detail()
        elif verb is Verb.LIVESTART:
            self.start_from_live()
        elif verb is Verb.LIVEUNLOAD:
            self.unload_from_live(a.arg or "ask")
        elif verb is Verb.LIVELOAD:
            name = self.svc.resolved_model(ui.pending or self.settings.pending_init(d))
            self.jobs.router_load(name, d)
        elif verb is Verb.TAB:
            ui.tab = int(a.arg)
        elif verb is Verb.QUIT:
            ui.quit = True
        elif verb is Verb.CANCEL:
            ui.quit = False
        elif verb is Verb.KEY:
            ui.key_shown = not ui.key_shown
        elif verb is Verb.CSP:
            ui.connect_sp = int(a.arg)
        elif verb is Verb.WRAP:
            ui.wrap = not ui.wrap
        elif verb is Verb.ERRORS:
            ui.errors_only = not ui.errors_only
            ui.log_scroll = 0
        elif verb is Verb.FOLLOW:
            ui.log_scroll = 0
        elif verb is Verb.DETACH:
            pid = d.target_pid
            if pid and not d.exited:
                ui.exit_msg = (f"The dashboard closed. The server is still running at {self.endpoint.base}.\n"
                               f"  To open the dashboard again: {command()}\n"
                               f"  To stop the server: {command()}, then q, then s.")
            raise SystemExit
        elif verb is Verb.STOP:
            pid = d.target_pid
            if pid:
                def stop() -> None:
                    self.jobs.save_before_stop(self.data)   # the sessions in the slots (save = auto, switch, stop)
                    self.jobs.host.end(pid)
                    ui.stopping = (pid, time.time() + 30)
                self.jobs.when_idle(d, "stop the server", stop)
            ui.quit = False
        elif verb is Verb.DRAIN and ui.drain:
            if a.arg == DrainOp.NOW:
                go, ui.drain = ui.drain.go, None
                go()
            elif a.arg == DrainOp.WAIT:
                ui.drain.waiting, ui.drain.since = True, time.time()
            elif a.arg == DrainOp.CANCEL:
                ui.drain = None

    def plugin_action(self, plugin: str, card: str, act: str) -> None:
        """A button of a plugin's card (Live tab): the plugin runs it and answers the message to show."""
        host = self.plugins()
        if host is not None:
            self.ui.toast(host.run_action(plugin, card, act) or "The plugin gave no message.", 8)

    def plugin_key(self, k: str) -> bool:
        """A key of a button of the selected plugin card (Live tab): True when a button took it."""
        host, ui = self.plugins(), self.ui
        if host is None or not is_plugin_section(ui.section):
            return False
        for slot in SLOTS:
            for c in host.cards(slot):
                for a in c.actions:
                    if c.name == ui.section and a.key == k:
                        self.plugin_action(c.plugin, c.id, a.id)
                        return True
        return False

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
        ui.opened.add(nm)
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
            self.save_prefs(self.prefs, self.ui.detail, self.ui.levels)

    def unload_from_live(self, step: str) -> None:
        """u on the Live tab (W1.6): ask, then unload the model with the stop's save of the sessions in the slots
        (save_before_stop, the slot handoff's too). Router mode unloads the loaded model and the router keeps running;
        else the server stops and the dashboard stays (a, or the Live tab's Start, starts it again)."""
        ui, d = self.ui, self.data
        if step == "no":
            ui.unload_ask = False
            return
        if step == "ask":
            ui.unload_ask = bool(d.target_pid and not d.exited and not (d.router is not None and not d.alias))
            return
        ui.unload_ask = False
        if d.router is not None:
            if d.alias:
                self.jobs.router_load(d.alias, d, unload=True)
            return
        pid = d.target_pid
        if not pid or d.exited:
            return

        def stop() -> None:
            self.jobs.save_before_stop(self.data)       # the sessions in the slots, as the stop saves them
            self.jobs.host.end(pid)
            ui.unloading = (pid, time.time() + 30)
        self.jobs.when_idle(d, "unload the model", stop)

    def start_from_live(self) -> None:
        """a on the Live tab with no server: Settings > Server, and its question "start the server?"."""
        ui = self.ui
        ui.tab, ui.sp = 4, SP_SERVER
        if ui.pending is None:
            ui.pending, ui.set_run = self.settings.pending_init(self.data), self.svc.running(self.data)
        self.settings.action(act(Verb.SETAPPLY))

    # ------------------------------------------------------------ input
    def scroll(self, step: int) -> None:
        """The mouse wheel and the scroll keys (step > 0 = up): the list or panel of the screen shown."""
        ui = self.ui
        if ui.tab == 4:
            if ui.sp == SP_FIT:
                ui.fit_scroll = max(0, ui.fit_scroll - step)
            elif ui.sp == SP_MODELS:
                ui.mrow = max(ui.mrow - step, 0)
            elif ui.sp in (SP_AGENTS, SP_TUNE, SP_ROUTER, SP_CACHE) or ui.sp >= len(SUBPANELS):   # these pages scroll as
                # one (Phase 23.2); a plugin's panel is one too
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
                a = parse(r.action)
                verb = a.verb if a else None
                if ui.drain and verb is not Verb.DRAIN:
                    return
                if ui.quit and verb not in (Verb.STOP, Verb.DETACH, Verb.CANCEL, Verb.QUIT):
                    return
                if ui.confirm and verb not in (Verb.SETYES, Verb.SETNO):
                    return
                if ui.unload_ask and verb is not Verb.LIVEUNLOAD:
                    return
                self.do(r.action)
                return

    def typing(self) -> bool:
        """A text is being typed in the Settings tab (a Hugging Face repo, a card field)."""
        ui = self.ui
        return ui.tab == 4 and not (ui.quit or ui.drain or ui.confirm or ui.unload_ask) and bool(
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
                self.do(act(Verb.DRAIN, DrainOp.NOW))
            elif k in ("w", "W") and not ui.drain.waiting:
                self.do(act(Verb.DRAIN, DrainOp.WAIT))
            elif k in ("n", "N", ESC):
                self.do(act(Verb.DRAIN, DrainOp.CANCEL))
            return False
        if ui.unload_ask:                         # Unload's question: y / Enter yes, n / Esc no
            if k in ("y", "Y") or k in ENTER:   # not u: a double press of u would not read the question
                self.do(act(Verb.LIVEUNLOAD, "yes"))
            elif k in ("n", "N", ESC):
                self.do(act(Verb.LIVEUNLOAD, "no"))
            return False
        if ui.quit:                               # s stop, l / q / d leave it running, n / c / Esc cancel
            if k in ("s", "S"):
                self.do(act(Verb.STOP))
            elif k in ("l", "L", "d", "q", "Q"):
                self.do(act(Verb.DETACH))
            elif k in ("n", "N", "c", "C", ESC):
                self.do(act(Verb.CANCEL))
            return False
        if ui.confirm:                            # Apply's question: y / Enter yes, n / Esc no
            if k in ("y", "Y") or k in ENTER:
                self.do(act(Verb.SETYES))
            elif k in ("n", "N", ESC):
                self.do(act(Verb.SETNO))
            return False
        if ui.tab == 4 and self.settings.key(k):
            return False
        if ui.tab == 1 and ui.install_ask:        # the installer's question
            if k in ("y", "Y") or k in ENTER:
                self.do(act(Verb.INSYES))
                return False
            if k in ("n", "N", ESC):
                self.do(act(Verb.INSNO))
                return False
        if ui.tab == 0 and self.plugin_key(k):
            return False
        if k in ("q", "Q", "\x03"):
            self.do(act(Verb.QUIT))
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
            self.do(act({"k": Verb.KEY, "o": Verb.OPENCODE, "p": Verb.PI, "c": Verb.CURL, "t": Verb.CURL}[k]))
        elif ui.tab == 0 and k == "a" and (self.data.exited or not (self.data.up or self.data.pid)):
            self.do(act(Verb.LIVESTART))
        elif ui.tab == 0 and k == "a" and self.data.router is not None and not self.data.alias:
            self.do(act(Verb.LIVELOAD))
        elif ui.tab == 0 and k == "u" and self.data.target_pid and not self.data.exited:
            self.do(act(Verb.LIVEUNLOAD, "ask"))
        elif ui.tab == 0 and k in ("+", "="):
            ui.lines = min((ui.lines or 3) + 2, 60)
        elif ui.tab == 0 and k in ("-", "_"):
            ui.lines = max((ui.lines or 5) - 2, 2)
        elif ui.tab == 1 and k in ("i", "u", "x", "P", "z", "f"):
            self.do(act({"i": Verb.INSALL, "u": Verb.INSCONFIG, "x": Verb.INSCLOSE, "P": Verb.INSPUSH,
                         "z": Verb.PKGMAKE, "f": Verb.PKGSHOW}[k]))
        elif ui.tab == 1 and k == "g" and ui.connect_sp == 0:
            self.connect.cycle_gate()
        elif ui.tab == 1 and k in ("[", "]"):
            ui.connect_sp = (ui.connect_sp + 1) % len(CONNECT_SUBPANELS)
        elif ui.tab == 3 and k in ("w", "f"):
            self.do(act(Verb.WRAP if k == "w" else Verb.ERRORS))
        return False
