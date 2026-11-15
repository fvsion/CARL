"""The dashboard application: builds the parts (create, the dashboard's composition over the host side that
composition/dashboard.py picks), draws frames and hands keys and clicks to the controller. The main loop is loop.py;
where the dashboard API runs is application/dashboard_api_host.py; what it tells about the clients on this computer
is application/client_status.py. This module reaches the computer only through the ports of
composition/dashboard.py (the disk cache through CacheWatch, the jobs through JobsHost); the controller and the
renderers import no adapter. A renderer never writes the UI state: what a screen it drew tells the dashboard (its keys,
its sections, a scroll kept in range) comes back in a Shown (shown.py), which frame() applies.

The jobs' notifier (dashboard/actions/notifier.py): App.drain_events, called by the loop before each frame, applies
what the jobs' threads reported to UIState on the loop's thread."""
from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..application.client_status import ClientStatus
from ..application.collector import Collector
from ..application.dashboard_api_host import ClientRegistry, DashboardApiHost
from ..application.model_list import ModelList
from ..application.server_jobs import Paths, ServerJobs
from ..application.settings_service import Pending, Schema, SettingsService, gib_pair, net_choices
from ..composition.dashboard import DashboardHost, environment, host as dashboard_host
from .clients import Drift, drift, serving
from .settings_texts import fit_sentence
from ..domain.llama.version import LlamaVersion, VersionCheck, check_version, tested_for
from ..domain.server.slot_files import short_id
from ..domain.server.snapshot import JSONDict, ServerData, flag, jdict
from ..domain.shared.units import duration, tokens
from ..ports import PluginHost
from ..ports.cache import CacheEvent
from ..ports.plugin import LIVE_SLOTS, SLOT_REQUESTS, CardView, HealthRow, InfoLine, SlotSession
from ..ui.fmt import (B, CYN, DIM, GRN, R, RED, YEL, CardLine, Key, Row, cwrap, draw_card, fit, footer_keys, indent,
                      pill, row, vlen, wwrap)
from ..ui.fmt import wall as clock_text
from ..ui.screen import LOGO_COLS
from ..ui.words import command, plural, uptime
from . import options as cli
from .act import Verb, act as make
from .actions.notifier import UiNotifier, launcher_errors
from .controller import Controller, Region
from .shown import Shown, apply as apply_shown
from .loop import progress_lines, progress_steps, run as run_loop  # noqa: F401  (progress_*: their names here too)
from .render.card_view import FORM_KEYS, TYPING_KEYS, draw_form
from .render.cards import STOPPED, View, status_of
from .render.settings import SettingsView
from .render.settings.common import subpanel_bar
from .render.settings.models import ModelsDir
from .render.tabs.connect import install_width
from .render.tabs.live import (body_connect, body_live, body_log, body_requests, drain_dialog, keys_card, quit_dialog,
                               unload_dialog)
from .sections import THREE_LEVELS, is_plugin_section
from .state import SP_AGENTS, SP_CACHE, SP_FIT, SP_MODELS, SP_ROUTER, SP_SERVER, SUBPANELS, TABS, UIState

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
LOGO_FILE = os.path.join(REPO, "assets", "carl-icon.png")
QUIT_LABEL = "[ Quit ]"
TAIL: List[Key] = [("D", "detail"), ("?", "all keys"), ("q", "quit")]      # the footer's keys on every screen
DEFAULT_SIZE = (120, 36)                # the terminal's size when it can't be read


@dataclass
class Machine:
    """Facts about this computer read once at start."""
    page: int           # VM page size
    total_mem: int
    llama_version: str = ""     # `llama-server --version` (its "version:" line; "" or "?" when not known)


class App:
    """The dashboard: draws frames from the collector's snapshots and the UI state, and runs
    the main loop; input goes to the Controller."""

    def __init__(self, opts: cli.Options, machine: Machine, collector: Collector, ui: UIState, svc: SettingsService,
                 jobs: ServerJobs, view: SettingsView, logo: Optional[bytes], logo_kind: Optional[str],
                 host: Optional[DashboardHost] = None) -> None:
        self.opts = opts
        self.host = host or dashboard_host()
        self.watch = collector.watch
        self.machine = machine
        self.endpoint = collector.endpoint
        self.collector = collector
        self.ui = ui
        self.svc = svc
        self.store = svc.store
        self.jobs = jobs
        self.view = view
        self.logo = logo
        self.logo_kind = logo_kind
        self.logo_cols = LOGO_COLS if logo else 0
        self.prefs = self.host.prefs_path(jobs.paths.config_file)
        self._plugins: Optional[PluginHost] = None          # the plugins (WP): none unless create() or a test gives them
        self._plugins_make: Optional[Callable[[], PluginHost]] = None
        self.ctl = Controller(ui, svc, jobs, view, collector.endpoint, collector.tail, REPO, opts.home, self.prefs,
                              lambda: self.plugins, self.host.save_detail)
        # the dashboard API for clients on other computers (application/dashboard_api_host.py)
        self.api_host = DashboardApiHost(self.make_api, self.publish_api, self.store.load_config, self.watch)
        self.clients = ClientStatus(self.host.clients(), svc.models, self.store.load_config, opts.home, drift, serving)
        self.size: Callable[[], Tuple[int, int]] = default_size          # the loop gives the terminal's own
        jobs.install_columns = lambda: install_width(self.size()[0])       # the installer wraps its output to the card
        self._names: Tuple[float, Tuple[str, ...], Dict[str, str]] = (0.0, (), {})
        self._slot_names: Tuple[float, Tuple[str, ...], Dict[str, str]] = (0.0, (), {})
        # the slots' sessions and each finished request's, for the plugins' hooks: (read at, key, slots, by request)
        self._sessions: Tuple[float, Tuple[object, ...], Tuple[SlotSession, ...], Dict[int, SlotSession]] = \
            (0.0, (), (), {})
        self._req_lines: Tuple[float, Tuple[object, ...], Dict[int, Tuple[InfoLine, ...]]] = (0.0, (), {})
        # what the disk cache did (Phase 23.5.1.1): OpenCode's and Pi's events, read as the day files grow
        self.cache = self.host.cache()
        self._events: Tuple[float, str, List[CacheEvent]] = (0.0, "", [])     # (read at, settings folder, events)
        self._status: Dict[str, object] = {"state": "idle"}  # /carl/status: made in the loop, read by the API's thread
        self._clients_file = os.path.join(os.path.dirname(jobs.paths.config_file), "clients.json")
        self._errors: Tuple[str, float, List[str]] = ("", 0.0, [])    # the launcher's output: (path, mtime, error lines)
        self._keep: Tuple[float, Optional[int]] = (0.0, None)            # the GPU memory kept free: (read at, bytes)
        # The installed llama.cpp (HEALTH): read at the dashboard's start (Machine) and again when the server process
        # changes (a restart after brew upgrade llama.cpp); create() sets the reader, tests keep the start value.
        self.read_llama_version: Callable[[], str] = lambda: machine.llama_version
        self._llama: Tuple[Optional[int], VersionCheck] = (None, check_version(machine.llama_version, self.tested()))

    @property
    def plugins(self) -> Optional[PluginHost]:
        """The plugins that are installed: their cards, panels and checks (WP). create() gives the host's, built on
        first use; an App made by hand has none."""
        if self._plugins is None and self._plugins_make is not None:
            self._plugins, self._plugins_make = self._plugins_make(), None
        return self._plugins

    @plugins.setter
    def plugins(self, host: Optional[PluginHost]) -> None:
        self._plugins, self._plugins_make = host, None

    def plugin_cards(self, slots: Sequence[SlotSession] = (), where: Sequence[str] = LIVE_SLOTS) -> Tuple[CardView, ...]:
        """The plugins' cards for the slots of a tab (the Live tab's by default), made now; none when a plugin fails (it
        says so in HEALTH). slots: the server's slots and their sessions (the hooks' PluginContext.slots)."""
        try:
            host = self.plugins
            return tuple(c for slot in where for c in host.cards(slot, slots)) if host is not None else ()
        except Exception:
            return ()

    def plugin_health(self) -> Tuple[HealthRow, ...]:
        """The plugins' checks and problems for the HEALTH card."""
        try:
            host = self.plugins
            return tuple(host.health()) if host is not None else ()
        except Exception:
            return ()

    @classmethod
    def create(cls, opts: cli.Options, host: Optional[DashboardHost] = None) -> "App":
        """Wire the parts together over the host side of this computer (composition/dashboard.py)."""
        host = host or dashboard_host()
        page = host.page_size()
        machine = Machine(page=page, total_mem=host.total_memory(), llama_version=host.llama_version())
        watch = host.watch(page)
        where = opts.host or watch.sockets().listen_host(opts.port) or opts.env_host or "127.0.0.1"
        endpoint = host.endpoint(where, opts.port, opts.key_file)
        collector = Collector(endpoint, fixed_host=bool(opts.host), key_file=opts.key_file,
                              server_pid=opts.server_pid, console=opts.console, log_arg=opts.log, home=opts.home,
                              watch=watch, gpu=host.gpu(), shapes=host.shapes())
        ui = UIState(lines=opts.lines)
        store = host.store()
        prefs = host.prefs_path(store.config_file)
        ui.detail = "full" if opts.expand else host.saved_detail(prefs)
        ui.levels.update({k: x for k, x in host.saved_levels(prefs).items() if k in ui.levels or is_plugin_section(k)})
        if opts.expand:                          # --expand: every section at full for this run (not saved)
            ui.levels.update({k: 2 if k in THREE_LEVELS else 1 for k in ui.levels})
        models = ModelList(store, on_error=lambda msg: ui.toast(f"{RED}{msg}{R}", 10))
        schema = Schema(net_choices(host.local_addresses(opts.vm_addr)), host.profile.os)
        svc = SettingsService(models, schema, opts.vm_addr, gpu_limit=lambda: (collector.gpu_limit or store.gpu_limit())[0],
                              ram=machine.total_mem)
        paths = Paths(REPO, logs=os.path.join(opts.home, "models", "logs"), config_file=store.config_file)
        jobs = ServerJobs(UiNotifier(ui), collector, svc, paths, opts.vm_addr, host.jobs_host())
        view = SettingsView(svc, store.config_file, opts.home)
        kind = host.logo_kind() if not opts.once else None
        png = None
        if kind:
            try:
                with open(LOGO_FILE, "rb") as f:
                    png = f.read()
            except OSError:
                kind = None
        app = cls(opts, machine, collector, ui, svc, jobs, view, png, kind, host)
        app._plugins_make = host.plugins
        app.read_llama_version = host.llama_version
        return app

    # ------------------------------------------------------------ drawing
    def view_ctx(self) -> View:
        """What the cards need besides the snapshot (the latest one: ctl.data)."""
        c, ep, ui = self.collector, self.endpoint, self.ui
        d = self.ctl.data
        api = self.api_host.api
        md = flag(d.cmd, "-md")
        m = self.svc.models.by_name(d.alias) if d.alias else None
        return View(levels=ui.levels, host=ep.host, port=ep.port, base=ep.base, key=ep.key, key_file=self.opts.key_file,
                    key_shown=ui.key_shown, server_pid=c.server_pid, log=c.tail.book, log_path=c.tail.path,
                    model_path=c.model_path, model_size=c.model_size, gpu_limit=c.gpu_limit, slow=c.slow,
                    total_mem=self.machine.total_mem, home=self.opts.home, wrap=ui.wrap, errors_only=ui.errors_only,
                    log_scroll=ui.log_scroll, cache=self.cache_line(),
                    api=f"{api.host}:{api.port}" if api else "",
                    listeners=api.listeners if api else 0, pushed=self.jobs.pushed(),
                    unsent=self.config_unsent(d), single=self.clients.single_model(d),
                    clients=tuple(self.registry().list()),
                    detail=ui.detail, drafter_size=self.host.clients().file_size(md) if md else 0,
                    model_quant=str(m.get("quant") or "") if m else "", here=self.here_text(),
                    coder_here=self.coder_here(), cache_slots=self.cache_slots(d),
                    client_versions=self.client_versions() if ui.tab == 1 else (),
                    gate=self.jobs.cache_conf().gate, llama=self.llama_check(d.pid),
                    plugin_cards=self.plugin_cards(self.plugin_sessions(d)[0]) if ui.tab == 0 else (),
                    plugin_health=self.plugin_health() if ui.tab == 0 else (), vram_keep=self.vram_keep(),
                    own_load=self.svc.own_load() if ui.tab == 0 and ui.detail == "full" else "",
                    **self.requests_parts(d))

    def vram_keep(self) -> Optional[int]:
        """The memory kept free on each GPU (a PC's MEMORY card; None on a Mac), read again every 10 s."""
        at, keep = self._keep
        if self.host.profile.os != "macos" and time.time() - at > 10:
            try:
                keep = self.host.vram_keep()
            except Exception:        # a broken config.json: the card leaves the row out
                keep = None
            self._keep = (time.time(), keep)
        return keep

    def registry(self) -> ClientRegistry:
        """The computers that synced: the running API's list, else clients.json."""
        api = self.api_host.api
        return api.registry if api else self.host.clients_registry(self._clients_file)

    def client_versions(self) -> Tuple[Tuple[str, str, str, str], ...]:
        """OpenCode and Pi on this computer against the versions CARL is tested with (the Connect tab; read again
        every 5 min and after an install: runs `opencode --version`)."""
        return self.clients.versions(self.jobs.paths.repo, self.ui.install)

    def cache_events(self) -> List[CacheEvent]:
        """The disk cache's events of the last two days (read again at most every 2 s, and at once in another
        settings folder, set after the start)."""
        carl_dir = os.path.dirname(self.jobs.paths.config_file)
        at, folder, events = self._events
        if folder != carl_dir or time.time() - at > 2:
            self._events = (time.time(), carl_dir, self.cache.events(carl_dir))
        return self._events[2]

    def cache_slots(self, d: ServerData) -> Dict[int, Tuple[str, str, str]]:
        """The SLOTS card's Cache column: for each busy slot, what the cache did for the session in it."""
        busy = [x.id for x in d.slot_list if x.busy]
        size = {x.id: x.prompt for x in d.slot_list}
        if not busy:
            return {}
        events = self.cache_events()
        ids = tuple(sorted({str(e.get("session", "")) for e in events if e.get("e") == "place" and e.get("slot") in busy}))
        if ids != self._slot_names[1] or time.time() - self._slot_names[0] > 10:
            self._slot_names = (time.time(), ids, self.host.session_names(self.opts.home, ids))
        out = {}
        for s in busy:
            col = self.cache.slot_column(events, s, d.alias or "", self._slot_names[2], tokens=size.get(s) or 0)
            if col:
                out[s] = col
        return out

    def plugins_on(self) -> bool:
        """A plugin is installed and on (the seam's hooks are asked only then)."""
        try:
            host = self.plugins
            return host is not None and any(p.enabled for p in host.plugins())
        except Exception:
            return False

    def plugin_sessions(self, d: ServerData, requests: bool = False) -> Tuple[Tuple[SlotSession, ...],
                                                                           Dict[int, SlotSession]]:
        """What the plugins' hooks get of the sessions: the slots of the loaded model, each with the session the latest
        placement put there, and (requests) each finished request's slot with the session the latest placement on it
        before it started, by the request's task (the disk cache's events, CacheWatch.slot_session). A session's name
        is the clients' name for it, else its ID cut short. Read again at most every 5 s, or when the events, the
        requests or the slots change; none without a plugin that is on."""
        if not self.plugins_on():
            return (), {}
        book = self.collector.tail.book
        events = self.cache_events()
        model = d.alias or ""
        slots = [x.id for x in d.slot_list if x.id is not None]
        key = (len(events), len(book.requests) if requests else -1, model, tuple(slots),
               os.path.dirname(self.jobs.paths.config_file))
        at, was, by_slot, by_req = self._sessions
        if was != key or time.time() - at > 5:
            placed = [(s, self.cache.slot_session(events, s, model)) for s in slots]
            ran = ({r.task: (r.slot, self.cache.slot_session(events, r.slot, model, book.epoch(r.t0)))
                    for r in book.requests if r.task is not None and book.epoch(r.t0) is not None} if requests else {})
            ids = sorted(({sid for _, sid in placed} | {sid for _, sid in ran.values()}) - {""})
            try:
                names = self.host.session_names(self.opts.home, ids) if ids else {}
            except Exception:       # a store that cannot be read: the IDs cut short
                names = {}

            def one(slot: int, sid: str) -> SlotSession:
                return SlotSession(slot, sid, (names.get(sid) or short_id(sid)) if sid else "")
            by_slot = tuple(one(s, sid) for s, sid in placed)
            by_req = {t: one(slot, sid) for t, (slot, sid) in ran.items() if slot is not None}
            self._sessions = (time.time(), key, by_slot, by_req)
        return by_slot, by_req

    def requests_parts(self, d: ServerData) -> Dict[str, Any]:
        """What the plugins add to the Requests tab: their cards (SLOT_REQUESTS, made now) and the lines under each
        finished request (by its task; asked again at most every 5 s, or when the sessions change). None on the other
        tabs."""
        if self.ui.tab != 2 or not self.plugins_on():
            return {}
        slots, by_req = self.plugin_sessions(d, requests=True)
        key = self._sessions[1]
        at, was, lines = self._req_lines
        if was != key or time.time() - at > 5:
            tasks = sorted(by_req)
            try:
                host = self.plugins
                got = host.request_lines([by_req[t] for t in tasks], slots) if host is not None and tasks else []
                lines = {t: tuple(x) for t, x in zip(tasks, got) if x}
            except Exception:       # a host that fails: no lines (HEALTH says why)
                lines = {}
            self._req_lines = (time.time(), key, lines)
        return {"request_cards": self.plugin_cards(slots, (SLOT_REQUESTS,)), "request_lines": dict(lines)}

    def cache_did(self) -> Tuple[List[Any], str]:
        """WHAT THE CACHE DID (the Caching panel): its rows for the chosen time, and that time in words."""
        windows = self.cache.windows
        when = windows[self.ui.cache_window % len(windows)]
        book = self.collector.tail.book
        since = self.cache.window_start(when, time.time(), book.start)
        return self.cache.table(self.cache_events(), book.tally(), since), when

    def tested(self) -> LlamaVersion:
        """The llama.cpp version that this computer is checked against (a Mac's Homebrew build, else the pin)."""
        return tested_for(self.host.profile.os)

    def llama_check(self, pid: Optional[int]) -> VersionCheck:
        """The installed llama.cpp against the tested version; read again when a new server process runs."""
        if pid and pid != self._llama[0]:
            self._llama = (pid, check_version(self.read_llama_version(), self.tested()))
        return self._llama[1]

    def here_text(self) -> str:
        """OpenCode and Pi on this computer, for the CONNECT card."""
        return self.clients.here_text(self.endpoint.base, self.ctl.data, self.endpoint.port)

    def coder_here(self) -> str:
        """The coder's model of OpenCode and Pi on this computer, for the Clients list (Phase 23.4.5)."""
        return self.clients.coder_here(self.endpoint.base)

    def next_start(self, d: ServerData) -> List[CardLine]:
        """The SERVER card when no server runs, one reading per row: what a start uses, whether it fits, how to
        start it, the last log; the launcher's error lines after a start that failed."""
        L: List[CardLine] = []
        try:
            p = self.ensure_pending(d)
            f = self.svc.fit_cached(p)
            name = self.svc.resolved_model(p)
            L.append(row("next start", f"{name}   {DIM}{'auto' if p.get('model') == 'auto' else 'your choice'}{R}"))
            if f.fits:
                need, limit = gib_pair(f.need, f.limit)
                L.append(row("with", f"{plural(f.slots, 'slot')} × {tokens(f.ctx)} tokens"))
                L.append(row("fits", f"{GRN}✓{R} {need.replace(' GiB', '')} of {limit}"))
            else:
                L.append(fit_sentence(f))
        except Exception as e:      # a broken catalogue or config: the Settings tab says why
            L.append(f"{YEL}CARL cannot read the settings: {e}{R}")
        L.append(row("to start", f"Press a, or run {command()} in a terminal."))
        if d.log_path and self.collector.files.size(d.log_path) is not None:
            L.append(row("last log", f"{os.path.basename(d.log_path)}   {DIM}tab 4{R}"))
        errs = self.ui.start_error or (self.exited_errors() if d.exited else [])
        if errs:
            L += ["", f"{RED}{B}The last start failed. The launcher said:{R}", *[f"{RED}{e}{R}" for e in errs]]
        return L

    def exited_errors(self) -> List[str]:
        """The launcher's error lines in the console output of a server that exited (cached by mtime)."""
        path = self.collector.console or os.path.join(self.opts.home, "models", "logs",
                                                      f".console-{self.endpoint.port}.out")
        try:
            mt = os.path.getmtime(path)
        except OSError:
            return []
        if self._errors[:2] != (path, mt):
            # a normal stop has no error line
            self._errors = (path, mt, launcher_errors(self.jobs.host.read_text(path), tail=False))
        return self._errors[2]

    def cache_api(self) -> None:
        """The dashboard API next to the server (application/dashboard_api_host.py): started, moved or tried again;
        the API Port row and the jobs follow it."""
        ep, ui = self.endpoint, self.ui
        placed = self.api_host.place(ep.host, ep.port)
        if placed is None:
            return
        ui.api_port, ui.api_note = placed.port, placed.note
        if self.api_host.api:
            self.jobs.registry = self.api_host.api.registry       # type: ignore[assignment]
        elif placed.moved:
            ui.toast(f"The dashboard API cannot start: {ui.api_note}. Other computers cannot sync now (the Caching "
                     f"panel says so too).", 12, error=True)

    def make_api(self, host: str, port: int) -> object:
        """The dashboard API's server for host:port, over this dashboard's jobs and state (not started)."""
        conf_dir = os.path.dirname(self.jobs.paths.config_file)
        return self.host.api_server(host, port, lambda: self.endpoint.key, self.jobs.paths.slots, self.jobs.cache_conf,
                                    lambda model: self.jobs.save_recorded(model, self.ctl.data.router is not None),
                                    conf_dir, self.jobs.paths.repo, lambda: self.endpoint.port,
                                    lambda: self._status)  # type: ignore[arg-type,return-value]

    def publish_api(self, server_port: int, api_port: int, url: str) -> None:
        """The API's port into the server's file and remote.json, where the clients look."""
        self.host.publish_api(self.jobs.paths.repo, os.path.dirname(self.jobs.paths.config_file), server_port,
                              api_port, url)

    def drain_events(self) -> None:
        """What the jobs' threads reported (their notifier's queue), applied to UIState here, on the loop's thread,
        before each frame."""
        if isinstance(self.jobs.notify, UiNotifier):
            self.jobs.notify.drain()

    def session_names(self, files: Sequence[Any]) -> Dict[str, str]:
        """The saved sessions' current names (OpenCode's and Pi's stores on this computer; read again every 10 s)."""
        ids = tuple(sorted(self.host.session_id(f.name) for f in files if f.name.startswith("carl-session+")))
        if ids != self._names[1] or time.time() - self._names[0] > 10:
            self._names = (time.time(), ids, self.host.session_names(self.opts.home, ids))
        return self._names[2]

    def start_record(self, d: ServerData) -> JSONDict:
        """The running model's start record (the launcher's server file: what it resolved; W1.6), {} when none."""
        doc = self.host.server_file(os.path.dirname(self.jobs.paths.config_file), self.endpoint.port)
        models = jdict(doc.get("models"))
        name = d.alias or (next(iter(models)) if len(models) == 1 else "")
        return jdict(jdict(models.get(name)).get("start"))

    def window_model(self, d: ServerData) -> str:
        """The running model when it has the window cache (the launcher's server file), else ""."""
        doc = self.host.server_file(os.path.dirname(self.jobs.paths.config_file), self.endpoint.port)
        models = jdict(doc.get("models"))
        name = d.alias or (next(iter(models)) if len(models) == 1 else "")
        return name if jdict(models.get(name)).get("swa") == "window" else ""

    def cache_line(self) -> str:
        """The CONNECT card's disk cache line: what OpenCode and Pi have saved, of the limit."""
        files = self.cache.files(self.jobs.paths.slots)
        if not files:
            return ""
        n = sum(1 for f in files if f.name.startswith("carl-prefix+"))           # a saved prompt (else a session)
        used = sum(f.bytes for f in files)
        return (f"{used / 10 ** 9:.1f} of {self.jobs.cache_conf().disk_gb} GB   "
                f"{DIM}{plural(n, 'prompt')}, {plural(len(files) - n, 'session')}{R}")

    def header(self, d: ServerData, cols: int) -> Tuple[str, str]:
        """The two header lines: CARL, the state word, the model and its uptime, the clock, Quit; the tabs and
        the detail level. Their clickable parts go to the controller."""
        ui, once, regions = self.ui, self.opts.once, self.ctl.regions
        label, bg = status_of(d, self.collector.server_pid)
        dot = "○" if label == STOPPED else "●"
        up = uptime(d.etime) if d.up else ""
        kind = f"   router mode ({plural(len(d.router.models), 'model')})" if d.up and d.router is not None else ""
        name = d.alias or ("no model loaded" if d.router is not None else "no server" if label == STOPPED else "")
        left = (f"{'' if self.logo else '😎 '}{B}CARL{R}  {pill(f'{dot} {label}', bg)}  {B}{name}{R}{DIM}{kind}"
                f"{f'   up {up}' if up else ''}{R}")
        right = f"{DIM}{time.strftime('%H:%M')}{R}  " + ("" if once else f"{B}{RED}{QUIT_LABEL}{R}")
        head = fit(left, cols - self.logo_cols - vlen(right) - 1) + " " + right
        regions.append(Region(1, cols - len(QUIT_LABEL) + 1, cols + 1, make(Verb.QUIT)))
        tabs, x = " ", 2 + self.logo_cols
        stale = self.configs_stale()
        for i, t in enumerate(TABS):
            lab = f"[{i + 1} {t}]" if i == ui.tab else f" {i + 1} {t}{' ⚠' if i == 1 and stale else ''} "
            tabs += (f"\x1b[1;7m{lab}{R}" if i == ui.tab else f"{YEL}{lab}{R}" if i == 1 and stale else f"{DIM}{lab}{R}") + " "
            regions.append(Region(2, x, x + len(lab), make(Verb.TAB, i)))
            x += len(lab) + 1
        shown = {ui.levels.get(nm, 1) for nm in ui.sections if nm in THREE_LEVELS}     # an open two-level one is 1
        if any(ui.levels.get(nm, 1) == 0 for nm in ui.sections if nm not in THREE_LEVELS):
            shown.add(0)
        level = "mixed" if len(shown) > 1 else "full" if shown == {2} else "simple" if shown else ui.detail
        det = f"detail: {level} (D)"
        room = cols - self.logo_cols - vlen(tabs) - 1
        if room > len(det):
            tabs += " " * (room - len(det)) + f"{DIM}detail:{R} {B}{level}{R} {DIM}(D){R}"
            regions.append(Region(2, cols - len(det) + 1, cols + 1, make(Verb.DETAIL)))
        return head, fit(tabs, cols - self.logo_cols)

    def dialog(self) -> bool:
        """A dialog, a drop-down or a text being typed has the keys: the footer shows only its keys."""
        ui = self.ui
        return bool(ui.drain or ui.quit or ui.confirm or (ui.tab == 4 and (ui.picker or ui.confirm2 or ui.text
                                                                            or ui.edit is not None))
                    or (ui.tab == 1 and ui.install_ask))

    def frame(self, d: ServerData) -> List[str]:
        """The screen's lines; the clickable regions go to the controller."""
        ui, once = self.ui, self.opts.once
        cols, rows = self.size()
        regions = self.ctl.regions
        regions.clear()
        self.svc.full = ui.full
        d.start = self.start_record(d) if d.pid or d.up else {}
        height = (rows - 6) if not once else 10**4
        shown = Shown()                                   # the screen drawn next tells its keys, sections and scrolls
        if ui.drain:
            body = drain_dialog(ui.drain, cols, height, ui, shown)
        elif ui.quit:
            body = quit_dialog(d, ui, cols, height, shown)
        elif ui.unload_ask:
            body = unload_dialog(d, cols, height, shown)
        else:
            body = self.body(d, cols, height, shown)
            apply_shown(ui, shown)
            if ui.help:                          # ?: every key of this screen, then the keys of every screen
                if cols >= 160:                  # an overlay on the right half: the page stays where it is
                    cut = 2 + (cols - 3) // 2                 # the overlay covers the right column, not the left
                    cw = min(cols - cut - 1, 96)
                    card = keys_card(ui.keys, ui.keys_more, ui.messages, cw)
                    body = [(fit(t, cut).replace("…", " ") + " " + card[i][0], [s for s in sp if s[1] <= cut] +
                             [(a + cut + 1, b + cut + 1, act) for a, b, act in card[i][1]]) if i < len(card) else (t, sp)
                            for i, (t, sp) in enumerate(body)]
                    body += [(" " * (cut + 1) + c[0], []) for c in card[len(body):]]
                else:
                    w = min(cols - 4, 96)
                    card = indent(keys_card(ui.keys, ui.keys_more, ui.messages, w), (cols - w) // 2)
                    shown = Shown()
                    body = card + self.body(d, cols, max(height - len(card), 4), shown)
        apply_shown(ui, shown)                     # the keys, the sections and the corrected positions of the screen
        head, tabs = self.header(d, cols)          # after the body: its sections set the detail shown
        out = [head, tabs, fit(DIM + "─" * cols, cols)]
        for text, spans in body:
            y = len(out) + 1
            regions.extend(Region(y, a + 1, b + 1, act) for a, b, act in spans)
            out.append(fit(text, cols))
        if not once:
            msg = cwrap(self.message(), cols - 2)[:2] if self.message() else [""]
            out = out[:rows - 2 - len(msg)]
            while len(out) < rows - 2 - len(msg):
                out.append(" " * cols)
            out += [fit(" " + m, cols) for m in msg] + [fit(DIM + "─" * cols, cols), fit(" " + self.footer(cols - 2), cols)]
        if self.opts.demo:                       # screenshots: no key characters, no home path
            key = self.endpoint.key
            out = [ln.replace(self.opts.home, "~").replace(key[-4:] if key else "\0", "••••") for ln in out]
        return out

    def message(self) -> str:
        """The message line above the keys: a stop or restart in progress, the newest message (for its
        time), else a download or a run in the background; empty when nothing happens."""
        ui = self.ui
        msg, until = ui.toast_msg
        if ui.stopping:
            return f"{YEL}The server is stopping…{R}"
        if ui.restart:
            return f"{YEL}{ui.restart}{R}" + (f" {DIM}({duration(time.time() - ui.restart_t)}){R}" if ui.restart_t else "")
        if msg and time.time() < until:
            return f"{RED if ui.toast_error else GRN}{msg.replace(R, R + (RED if ui.toast_error else GRN))}{R}"
        dl, tn, ins = ui.dl, ui.tune, ui.install
        if dl and not dl.done and not (ui.tab == 4 and ui.sp in (SP_MODELS, SP_FIT)):   # those panels show it
            frac = f": {dl.have / dl.total * 100:.0f}%" if dl.total else ""
            return f"{CYN}CARL is downloading {dl.name}{frac}.{R} {DIM}It is in Settings > Models.{R}"
        if tn and not tn.done:
            return f"{CYN}Auto-tune is running for {tn.model}.{R} {DIM}It is in Settings > Auto-tune.{R}"
        if ins and not ins.done:
            return f"{CYN}The installer is running.{R}" + ("" if ui.tab == 1 else f" {DIM}It is in the Connect tab.{R}")
        if ui.package and not ui.package.done:
            return f"{CYN}CARL is making the client package.{R}" + ("" if ui.tab == 1 else f" {DIM}It is in the Connect "
                                                                                    f"tab.{R}")
        return ""

    def footer(self, w: int) -> str:
        """The keys of the screen shown (its dialog's alone while one is open); "D detail · ? all keys ·
        q quit" stay at the end of every screen."""
        ui = self.ui
        if ui.help:
            return footer_keys([("?", "close this list")], [("q", "quit")], w)
        if self.dialog():
            return footer_keys(ui.keys, [], w)
        return footer_keys(([("↓", "more")] if ui.more else []) + ui.keys, TAIL, w)

    def body(self, d: ServerData, cols: int, height: int, shown: Shown) -> List[Row]:
        """The selected tab's rows; what the screen tells the dashboard goes to shown."""
        ui = self.ui
        v = self.view_ctx()
        if ui.tab == 0:
            if status_of(d, self.collector.server_pid)[0] == STOPPED:
                v.next_start = self.next_start(d)
            return body_live(v, ui, d, cols, height, shown)
        if ui.tab == 1:
            here = self.clients.here(self.endpoint.base)
            stale, installed = self.client_drift(here)
            return body_connect(v, ui, d, cols, height, here, self.ctl.preview_text(ui.preview, d, mask=not ui.key_shown),
                                stale, installed, shown)
        if ui.tab == 2:
            return body_requests(v, ui, cols, height, shown)
        if ui.tab == 3:
            n = max(height - 4, 3) if not self.opts.once else max(self.size()[1] - 8, 10)
            return body_log(v, ui, cols, n, shown)
        return self.body_settings(d, cols, height, shown)

    def client_drift(self, here: List[Tuple[str, str]]) -> Tuple[List[Drift], int]:
        """The client configs on this computer (for this server) that list other models than the configs get, and how
        many they get (application/client_status.py)."""
        return self.clients.drift(here, self.endpoint.base, self.ctl.data, self.endpoint.port)

    def config_unsent(self, d: ServerData) -> bool:
        """The config sent to the other computers is out of date: the list they would get now differs."""
        return self.clients.config_unsent(d, self.jobs.pushed())

    def configs_stale(self) -> bool:
        """The client configs on this computer need carl install --config-only (the Connect tab's ⚠)."""
        return self.clients.configs_stale(self.endpoint.base, self.ctl.data, self.endpoint.port)

    def saved_mode(self) -> str:
        """llama.mode in config.json (single when unset or unreadable)."""
        return self.clients.saved_mode()

    def body_settings(self, d: ServerData, cols: int, height: int, shown: Shown) -> List[Row]:
        """The Settings tab: the panel bar, then the picker, a question, or the selected panel."""
        ui = self.ui
        panels = self.plugin_panels()
        if ui.sp >= len(SUBPANELS) + len(panels):          # a plugin's panel is gone (the plugin was turned off)
            ui.sp = SP_CACHE
        out: List[Row] = [subpanel_bar(ui.sp, [p.title for p in panels], cols, [p.group for p in panels]), ("", [])]
        if ui.sp != getattr(self, "_last_sp", ui.sp):     # another panel: its page starts at the top, and a value
            ui.page_scroll, ui.edit = 0, None             # typed on the one left is dropped
        self._last_sp = ui.sp
        if ui.picker:
            return out + self.view.picker(ui.picker, cols, height - 2, ui, shown)
        if ui.confirm2:
            return out + self.view.confirm(ui.confirm2, cols, ui, shown)
        try:
            if ui.sp == SP_SERVER:
                body = self.view.server(ui, self.ensure_pending(d), d, cols, height - 2, self.endpoint.port,
                                        self.collector.gpu_limit[1] if self.collector.gpu_limit else "", shown)
            elif ui.sp == SP_MODELS and ui.card:
                shown.keys = list(TYPING_KEYS if ui.card.typing is not None else FORM_KEYS)
                body = draw_form(ui.card, cols, height - 2)
            elif ui.sp == SP_AGENTS:
                body = self.view.agents(ui, self.ctl.settings.agents_info(), cols, height - 2,
                                        self.svc.models.error or "", shown)
            elif ui.sp == SP_FIT:
                body = self.view.autofit(ui, self.ensure_pending(d), cols, height - 2, shown)
            elif ui.sp == SP_ROUTER:
                book = self.collector.tail.book
                stale, n = self.client_drift(self.clients.here(self.endpoint.base))
                here = bool(self.clients.here(self.endpoint.base))
                body = self.view.router(ui, d, self.saved_mode(), [(clock_text(book.epoch(t)), name) for t, name in book.switches],
                                        stale, n, cols, here, height - 2, shown)
            elif ui.sp == SP_CACHE:
                folder = self.jobs.paths.slots
                api = f"{self.api_host.api.host}:{self.api_host.api.port}" if self.api_host.api else ""
                files = self.cache.files(folder)
                did, when = self.cache_did()
                body = self.view.caching(ui, self.jobs.cache_conf(), files, folder, cols, api, height - 2,
                                         self.session_names(files), self.window_model(d), did, when, shown,
                                         disk_free(folder))
            elif ui.sp >= len(SUBPANELS):
                body = self.view.plugin(ui, panels[ui.sp - len(SUBPANELS)], cols, height - 2, shown)
            elif ui.sp == SP_MODELS:
                mdir = self.store.models_dir()
                body = self.view.models(ui, cols, height - 2, ModelsDir(mdir, disk_free(mdir)), d.alias, shown)
            else:
                body = self.view.tune(ui, cols, d.up, height - 2, shown)
        except Exception as e:      # the catalogue, models.json or config.json can't be read: say so, keep running
            body = panel_error(e, cols)
        if not shown.keys:
            shown.keys = [("[ ]", "panels")]
        return (out + body)[:height]

    def plugin_panels(self) -> List[Any]:
        """The plugins' configuration panels (after Caching); none when a plugin fails."""
        try:
            host = self.plugins
            return list(host.panels()) if host is not None else []
        except Exception:
            return []

    def ensure_pending(self, d: ServerData) -> Pending:
        """The Server panel's values, from config.json and the running server the first time."""
        ui = self.ui
        if ui.pending is None:
            ui.pending, ui.set_run = self.ctl.pending_init(d), self.svc.running(d)
        return ui.pending

    # ------------------------------------------------------------ main loop
    def run(self) -> None:
        """Print one snapshot (--once), or run the interactive dashboard until quit (loop.py)."""
        run_loop(self)


def default_size() -> Tuple[int, int]:
    """The terminal's size as the environment and stdout tell it (--once, and the tests' frames)."""
    cols, rows = shutil.get_terminal_size(DEFAULT_SIZE)
    return cols, rows


def panel_error(e: Exception, cols: int) -> List[Row]:
    """A Settings panel that could not be drawn: the reason instead of a crash."""
    lines = [f"{RED}{x}{R}" for x in wwrap(f"{type(e).__name__}: {e}", cols - 6)[:6]]
    lines += ["", *[f"{DIM}{x}{R}" for x in wwrap("CARL cannot read the catalogue, "
                                                  f"models.json or config.json. Correct the file: {command()} models "
                                                  f"and {command()} config show tell the problem. This panel tries "
                                                  "again.", cols - 6)]]
    return indent(draw_card("seterror", "SETTINGS UNAVAILABLE", "", lines, cols - 1, 2))


def disk_free(path: str) -> int:
    """Free bytes on the disk holding path (0 if unknown)."""
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0


def main(argv: Optional[Sequence[str]], description: str) -> None:
    """Run the dashboard with these arguments (None: the command line); description is the --help text."""
    App.create(cli.parse(argv, description, environment())).run()
