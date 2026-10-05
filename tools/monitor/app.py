"""The dashboard application: builds the parts (composition root), draws frames, turns
keys and clicks into actions, and runs the main loop."""
from __future__ import annotations

import os
import select
import shutil
import signal
import threading
import time
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from carl_core.domain.units import duration, tokens

from . import cli, diskcache, fsio, system, uiprefs
from .cacheapi import CacheApi, Registry
from .api import Endpoint
from .card_view import FORM_KEYS, TYPING_KEYS, draw_form
from .cards import STOPPED, View, status_of
from .clients import Drift, drift
from .collector import Collector
from .controller import Controller, Region
from .fmt import (B, CYN, DIM, GRN, R, RED, YEL, CardLine, Key, Row, cwrap, draw_card, fit, footer_keys, indent, pill, vlen,
                  wwrap)
from .jobs import Paths, ServerJobs, launcher_errors
from .keys import InputBuffer
from .model import ServerData, flag, jdict
from .settings import Pending, Schema, SettingsService, fit_sentence, gib_pair, net_choices
from .settings_panels.common import subpanel_bar
from .settings_panels.models import ModelsDir
from .settings_view import SettingsView
from .state import SP_CACHE, SP_FIT, SP_MODELS, SP_ROUTER, SP_SERVER, TABS, UIState
from .store import CarlStore, ModelList
from .terminal import LOGO_COLS, Terminal, logo_escape, logo_mode, place_lines
from .views import body_connect, body_live, body_log, body_requests, drain_dialog, keys_card, quit_dialog
from .words import plural, uptime

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOGO_FILE = os.path.join(REPO, "assets", "carl-icon.png")
QUIT_LABEL = "[ Quit ]"
TAIL: List[Key] = [("D", "detail"), ("?", "all keys"), ("q", "quit")]      # the footer's keys on every screen


@dataclass
class Machine:
    """Facts about this Mac read once at start."""
    page: int           # VM page size
    total_mem: int


class App:
    """The dashboard: draws frames from the collector's snapshots and the UI state, and runs
    the main loop; input goes to the Controller."""

    def __init__(self, opts: cli.Options, machine: Machine, collector: Collector, ui: UIState, svc: SettingsService,
                 jobs: ServerJobs, view: SettingsView, logo: Optional[bytes], logo_kind: Optional[str]) -> None:
        self.opts = opts
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
        self.prefs = uiprefs.path_for(jobs.paths.config_file)
        self.ctl = Controller(ui, svc, jobs, view, collector.endpoint, collector.tail, REPO, opts.home, self.prefs)
        self._api: Optional[CacheApi] = None              # the cache API for clients on other computers
        self._api_where: Optional[Tuple[str, int]] = None
        self._clients_file = os.path.join(os.path.dirname(jobs.paths.config_file), "clients.json")
        self._errors: Tuple[str, float, List[str]] = ("", 0.0, [])    # the launcher's output: (path, mtime, error lines)

    @classmethod
    def create(cls, opts: cli.Options) -> "App":
        """Wire the parts together (the composition root)."""
        machine = Machine(page=system.sysctl_int("hw.pagesize", 16384), total_mem=system.sysctl_int("hw.memsize", 1))
        host = opts.host or system.listen_host(system.netstat_tcp(), opts.port) or opts.env_host or "127.0.0.1"
        endpoint = Endpoint(host, opts.port, fsio.read_key(opts.key_file))
        collector = Collector(endpoint, fixed_host=bool(opts.host), key_file=opts.key_file, server_pid=opts.server_pid,
                              console=opts.console, log_arg=opts.log, home=opts.home, page=machine.page)
        ui = UIState(lines=opts.lines)
        store = CarlStore()
        ui.detail = "full" if opts.expand else uiprefs.load_detail(uiprefs.path_for(store.config_file))
        models = ModelList(store, on_error=lambda msg: ui.toast(f"{RED}{msg}{R}", 10))
        schema = Schema(net_choices(system.local_addrs(opts.vm_addr)))
        svc = SettingsService(models, schema, opts.vm_addr, gpu_limit=lambda: (collector.gpu_limit or store.gpu_limit())[0],
                              ram=machine.total_mem)
        paths = Paths(REPO, logs=os.path.join(opts.home, "models", "logs"), config_file=store.config_file)
        jobs = ServerJobs(ui, collector, svc, paths, opts.vm_addr)
        view = SettingsView(svc, store.config_file, opts.home)
        kind = logo_mode(os.environ) if not opts.once else None
        png = None
        if kind:
            try:
                with open(LOGO_FILE, "rb") as f:
                    png = f.read()
            except OSError:
                kind = None
        return cls(opts, machine, collector, ui, svc, jobs, view, png, kind)

    # ------------------------------------------------------------ drawing
    def view_ctx(self) -> View:
        """What the cards need besides the snapshot (the latest one: ctl.data)."""
        c, ep, ui = self.collector, self.endpoint, self.ui
        d = self.ctl.data
        md = flag(d.cmd, "-md")
        m = self.svc.models.by_name(d.alias) if d.alias else None
        return View(levels=ui.levels, host=ep.host, port=ep.port, base=ep.base, key=ep.key, key_file=self.opts.key_file,
                    key_shown=ui.key_shown, server_pid=c.server_pid, log=c.tail.book, log_path=c.tail.path,
                    model_path=c.model_path, model_size=c.model_size, gpu_limit=c.gpu_limit, slow=c.slow,
                    total_mem=self.machine.total_mem, home=self.opts.home, wrap=ui.wrap, errors_only=ui.errors_only,
                    log_scroll=ui.log_scroll, cache=self.cache_line(),
                    api=f"{self._api.host}:{self._api.port}" if self._api else "",
                    listeners=self._api.listeners if self._api else 0, pushed=self.jobs.pushed(),
                    clients=tuple((self._api.registry if self._api else Registry(self._clients_file)).list()),
                    detail=ui.detail, drafter_size=fsio.file_size(md) if md else 0,
                    model_quant=str(m.get("quant") or "") if m else "", here=self.here_text())

    def here_text(self) -> str:
        """OpenCode and Pi on this Mac, for the CONNECT card."""
        try:
            here = fsio.installed_here(self.endpoint.base, self.opts.home)
            stale = self.client_drift(here)[0] if here else []
        except Exception:           # a model list that can't be read: say nothing
            return ""
        return ("need an update (tab 2, u)" if stale else "set up (tab 2)") if here else "not set up here (tab 2)"

    def next_start(self, d: ServerData) -> List[CardLine]:
        """The SERVER card when no server runs: how to start it, what a start uses, the last error."""
        L: List[CardLine] = ["To start it: press a (Settings > Server, Apply), or run ./carl.sh in a terminal."]
        try:
            p = self.ensure_pending(d)
            f = self.svc.fit_cached(p)
            name = self.svc.resolved_model(p)
            whose = "auto: Auto fit's choice" if p.get("model") == "auto" else "your choice"
            if f.fits:
                need, limit = gib_pair(f.need, f.limit)
                L.append(f"It will start {name} ({whose}) with {plural(f.slots, 'slot')} of {tokens(f.ctx)} tokens. "
                         f"It fits: {need.replace(' GiB', '')} of {limit}.")
            else:
                L.append(fit_sentence(f))
        except Exception as e:      # a broken catalogue or config: the Settings tab says why
            L.append(f"{YEL}CARL cannot read the settings: {e}{R}")
        errs = self.ui.start_error or (self.exited_errors() if d.exited else [])
        if errs:
            L += ["", f"{RED}{B}The last start failed. The launcher said:{R}", *[f"{RED}  {e}{R}" for e in errs]]
        if d.log_path and os.path.exists(d.log_path):
            L += ["", f"The log of the last run: tab 4 ({os.path.basename(d.log_path)})."]
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
            self._errors = (path, mt, launcher_errors(path))
        return self._errors[2]

    def cache_api(self) -> None:
        """The cache API (cacheapi.py) next to the server: its address, port + 1; moved when the
        server moves. A port that is taken is said once."""
        ep = self.endpoint
        where = (ep.host, ep.port + 1)
        if self._api_where == where:
            return
        if self._api:
            self._api.stop()
        self._api, self._api_where = None, where
        api = CacheApi(ep.host, ep.port + 1, lambda: self.endpoint.key, self.jobs.paths.slots, self.jobs.cache_conf,
                       lambda model: self.jobs.save_recorded(model, self.ctl.data.router is not None),
                       os.path.dirname(self.jobs.paths.config_file))
        err = api.start()
        if err:
            self.ui.toast(f"The dashboard API cannot start: {err}. Other computers cannot sync now (the Caching panel "
                          f"says so too).", 12)
        else:
            self._api = api
            self.jobs.registry = api.registry

    def cache_line(self) -> str:
        """The CONNECT card's disk cache line: what OpenCode and Pi have saved, of the limit."""
        files = diskcache.listing(self.jobs.paths.slots)
        if not files:
            return ""
        n = sum(1 for f in files if f.kind == diskcache.PROMPT)
        return (f"{plural(n, 'saved prompt')}, {plural(len(files) - n, 'saved session')}, "
                f"{diskcache.gb(diskcache.used(files)).replace(' GB', '')} of {self.jobs.cache_conf().disk_gb} GB")

    def header(self, d: ServerData, cols: int) -> Tuple[str, str]:
        """The two header lines: CARL, the state word, the model and its uptime, the clock, Quit; the tabs and
        the detail level. Their clickable parts go to the controller."""
        ui, once, regions = self.ui, self.opts.once, self.ctl.regions
        label, bg = status_of(d, self.collector.server_pid)
        dot = "○" if label == STOPPED else "●"
        up = uptime(d.etime) if d.up else ""
        kind = f" · router mode ({plural(len(d.router.models), 'model')})" if d.up and d.router is not None else ""
        name = d.alias or ("no model loaded" if d.router is not None else "no server" if label == STOPPED else "")
        left = (f"{'' if self.logo else '😎 '}{B}CARL{R}  {pill(f'{dot} {label}', bg)}  {B}{name}{R}{DIM}{kind}"
                f"{f' · up {up}' if up else ''}{R}")
        right = f"{DIM}{time.strftime('%H:%M')}{R}  " + ("" if once else f"{B}{RED}{QUIT_LABEL}{R}")
        head = fit(left, cols - self.logo_cols - vlen(right) - 1) + " " + right
        regions.append(Region(1, cols - len(QUIT_LABEL) + 1, cols + 1, "quit"))
        tabs, x = " ", 2 + self.logo_cols
        stale = self.configs_stale()
        for i, t in enumerate(TABS):
            lab = f"[{i + 1} {t}]" if i == ui.tab else f" {i + 1} {t}{' ⚠' if i == 1 and stale else ''} "
            tabs += (f"\x1b[1;7m{lab}{R}" if i == ui.tab else f"{YEL}{lab}{R}" if i == 1 and stale else f"{DIM}{lab}{R}") + " "
            regions.append(Region(2, x, x + len(lab), f"tab:{i}"))
            x += len(lab) + 1
        det = f"detail: {ui.detail} (D)"
        room = cols - self.logo_cols - vlen(tabs) - 1
        if room > len(det):
            tabs += " " * (room - len(det)) + f"{DIM}detail:{R} {B}{ui.detail}{R} {DIM}(D){R}"
            regions.append(Region(2, cols - len(det) + 1, cols + 1, "detail"))
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
        cols, rows = shutil.get_terminal_size((120, 36))
        regions = self.ctl.regions
        regions.clear()
        self.svc.full = ui.full
        head, tabs = self.header(d, cols)
        out = [head, tabs, fit(DIM + "─" * cols, cols)]
        height = (rows - 6) if not once else 10**4
        ui.keys, ui.keys_more = [], []           # the screen drawn next sets its own
        if ui.drain:
            body = drain_dialog(ui.drain, cols, height, ui)
        elif ui.quit:
            body = quit_dialog(d, ui, cols, height)
        else:
            body = self.body(d, cols, height)
            if ui.help:                          # ?: every key of this screen, then the keys of every screen
                card = indent(keys_card(ui.keys, ui.keys_more, ui.messages, min(cols - 2, 110)))
                body = card + self.body(d, cols, max(height - len(card), 4))
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
            return f"{YEL}The server stops…{R}"
        if ui.restart:
            return f"{YEL}{ui.restart}{R}" + (f" {DIM}({duration(time.time() - ui.restart_t)}){R}" if ui.restart_t else "")
        if msg and time.time() < until:
            return f"{GRN}{msg}{R}"
        dl, tn, ins = ui.dl, ui.tune, ui.install
        if dl and not dl.done:
            frac = f" {dl.have / dl.total * 100:.0f}%" if dl.total else ""
            return f"{CYN}Downloading {dl.name}{frac}{R} {DIM}(Settings > Models){R}"
        if tn and not tn.done:
            return f"{CYN}Auto-tune runs for {tn.model}{R} {DIM}(Settings > Auto-tune){R}"
        if ins and not ins.done:
            return f"{CYN}The installer runs{R} {DIM}(the Connect tab){R}"
        return ""

    def footer(self, w: int) -> str:
        """The keys of the screen shown (its dialog's alone while one is open); "D detail · ? all keys ·
        q quit" stay at the end of every screen."""
        ui = self.ui
        if ui.help:
            return footer_keys([("?", "close this list")], [("q", "quit")], w)
        if self.dialog():
            return footer_keys(ui.keys, [], w)
        return footer_keys(ui.keys, TAIL, w)

    def body(self, d: ServerData, cols: int, height: int) -> List[Row]:
        """The selected tab's rows."""
        ui = self.ui
        v = self.view_ctx()
        if ui.tab == 0:
            if status_of(d, self.collector.server_pid)[0] == STOPPED:
                v.next_start = self.next_start(d)
            return body_live(v, ui, d, cols, height)
        if ui.tab == 1:
            here = fsio.installed_here(self.endpoint.base, self.opts.home)
            stale, installed = self.client_drift(here)
            return body_connect(v, ui, d, cols, height, here, self.ctl.preview_text(ui.preview, d, mask=not ui.key_shown),
                                stale, installed)
        if ui.tab == 2:
            return body_requests(v, ui, cols, height)
        if ui.tab == 3:
            n = max(height - 4, 3) if not self.opts.once else max(shutil.get_terminal_size((120, 36))[1] - 8, 10)
            return body_log(v, ui, cols, n)
        return self.body_settings(d, cols, height)

    def client_drift(self, here: List[Tuple[str, str]]) -> Tuple[List[Drift], int]:
        """The client configs on this Mac (for this server) that list other models than are
        installed, and how many are installed."""
        if not here:                # no client set up here for this server: nothing to compare
            return [], 0
        installed = [m["name"] for m in self.svc.models.downloaded()]
        d = self.ctl.data
        running = (d.alias, d.n_ctx) if d.up and d.alias and d.n_ctx else None
        return (drift({c: fsio.listed_models(self.opts.home, c, pid) for c, pid in here}, installed, running),
                len(installed))

    def configs_stale(self) -> bool:
        """The client configs on this Mac need ./carl.sh install --config-only (the Connect tab's ⚠)."""
        try:
            return bool(self.client_drift(fsio.installed_here(self.endpoint.base, self.opts.home))[0])
        except Exception:           # a model list that can't be read: no warning (the Settings tab says why)
            return False

    def saved_mode(self) -> str:
        """llama.mode in config.json (single when unset or unreadable)."""
        try:
            return str(jdict(self.store.load_config().get("llama")).get("mode") or "single")
        except Exception:           # a broken config.json: the Server panel says why
            return "single"

    def body_settings(self, d: ServerData, cols: int, height: int) -> List[Row]:
        """The Settings tab: the panel bar, then the picker, a question, or the selected panel."""
        ui = self.ui
        out: List[Row] = [subpanel_bar(ui.sp), ("", [])]
        if ui.picker:
            return out + self.view.picker(ui.picker, cols, height - 2, ui)
        if ui.confirm2:
            return out + self.view.confirm(ui.confirm2, cols, ui)
        try:
            if ui.sp == SP_SERVER:
                body = self.view.server(ui, self.ensure_pending(d), d, cols, height - 2, self.endpoint.port,
                                        self.collector.gpu_limit[1] if self.collector.gpu_limit else "")
            elif ui.sp == SP_MODELS and ui.card:
                ui.keys = list(TYPING_KEYS if ui.card.typing is not None else FORM_KEYS)
                body = draw_form(ui.card, cols, height - 2)
            elif ui.sp == SP_FIT:
                body = self.view.autofit(ui, self.ensure_pending(d), cols, height - 2)
            elif ui.sp == SP_ROUTER:
                book = self.collector.tail.book
                stale, n = self.client_drift(fsio.installed_here(self.endpoint.base, self.opts.home))
                here = bool(fsio.installed_here(self.endpoint.base, self.opts.home))
                body = self.view.router(ui, d, self.saved_mode(), [(book.wall(t), name) for t, name in book.switches],
                                        [s.line(n) for s in stale], cols, here)[:height - 2]
            elif ui.sp == SP_CACHE:
                folder = self.jobs.paths.slots
                api = f"{self._api.host}:{self._api.port}" if self._api else ""
                body = self.view.caching(ui, self.jobs.cache_conf(), diskcache.listing(folder), folder, cols,
                                         api)[:height - 2]
            elif ui.sp == SP_MODELS:
                mdir = self.store.models_dir()
                body = self.view.models(ui, cols, height - 2, ModelsDir(mdir, disk_free(mdir)), d.alias)
            else:
                body = self.view.tune(ui, cols, d.up)
        except Exception as e:      # the catalogue, models.json or config.json can't be read: say so, keep running
            body = panel_error(e, cols)
        if not ui.keys:
            ui.keys = [("[ ]", "panels")]
        return (out + body)[:height]

    def ensure_pending(self, d: ServerData) -> Pending:
        """The Server panel's values, from config.json and the running server the first time."""
        ui = self.ui
        if ui.pending is None:
            ui.pending, ui.set_run = self.ctl.pending_init(d), self.svc.running(d)
        return ui.pending

    # ------------------------------------------------------------ main loop
    def warm_up(self, term: Terminal) -> None:
        """The first start: the server state, the model list and what fits read once, with a progress line
        for each step (the GGUF headers and a Hugging Face read can take some seconds)."""
        cols, rows = shutil.get_terminal_size((120, 36))

        def show(done: int) -> None:
            term.write(place_lines(progress_lines(done, cols, rows), 0))

        def models() -> None:
            self.svc.models.get()

        def fits() -> None:
            p = self.ensure_pending(self.ctl.data)
            self.svc.fit_cached(p)

        for i, step in enumerate((lambda: setattr(self.ctl, "data", self.collector.collect()), models, fits)):
            show(i)
            try:
                step()
            except Exception:       # a broken catalogue or config: the screens say why
                pass
        show(len(PROGRESS_STEPS))

    def run(self) -> None:
        """Print one snapshot (--once), or run the interactive dashboard until quit."""
        c, ui = self.collector, self.ui
        threading.Thread(target=c.slow_loop, daemon=True).start()
        threading.Thread(target=c.load_gpu_limit, daemon=True).start()
        if self.opts.once:
            self.ctl.data = c.collect()
            time.sleep(0.5)
            if self.opts.tab:
                ui.tab = self.opts.tab - 1
            print("\n".join(self.frame(self.ctl.data)))
            return
        term = Terminal()
        wake_r, wake_w = os.pipe()                 # Ctrl-C wakes the loop and opens the quit dialog

        def interrupted() -> None:
            ui.quit = True
            os.write(wake_w, b"x")

        term.enter(interrupted)
        buf = InputBuffer()
        try:
            self.warm_up(term)
            next_fetch = time.time() + self.opts.interval
            logo_size: Optional[os.terminal_size] = None
            logo_t = 0.0
            while True:
                if time.time() >= next_fetch:
                    self.ctl.data = c.collect()
                    next_fetch = time.time() + (0.5 if ui.stopping else self.opts.interval)
                self.jobs.poll()
                self.jobs.drain_tick(self.ctl.data)
                self.cache_api()
                if ui.stopping:
                    pid, deadline = ui.stopping
                    if not system.pid_alive(pid):
                        ui.exit_msg = f"Server (pid {pid}) stopped."
                        break
                    if time.time() > deadline:
                        system.kill(pid, signal.SIGKILL)
                tsize = shutil.get_terminal_size((120, 36))
                if self.logo and self.logo_kind and (tsize != logo_size or time.time() - logo_t > 30):
                    term.write(("\x1b[2J" if tsize != logo_size else "") + logo_escape(self.logo_kind, self.logo))
                    logo_size, logo_t = tsize, time.time()
                term.write(place_lines(self.frame(self.ctl.data), self.logo_cols))
                r, _, _ = select.select([term.fd, wake_r], [], [], max(min(next_fetch - time.time(), 1.0), 0.05))
                if wake_r in r:
                    os.read(wake_r, 64)
                if term.fd in r:
                    data = buf.feed(term.read(), lambda: bool(select.select([term.fd], [], [], 0.05)[0]))
                    if data and self.ctl.handle_input(data):
                        next_fetch = 0
        except SystemExit:
            pass
        finally:
            term.restore()
            if ui.exit_msg:
                print(ui.exit_msg)


PROGRESS_STEPS = ("Reading the server state", "Reading the model list", "Checking what fits this Mac")


def progress_lines(done: int, cols: int, rows: int) -> List[str]:
    """The first start's progress: each step with ✓ (done) or … (now), centred."""
    w = min(60, cols - 4)
    L: List[CardLine] = ["", *[(f"{GRN}✓{R} " if i < done else f"{YEL}…{R} " if i == done else "  ") + step
                               for i, step in enumerate(PROGRESS_STEPS)], ""]
    card = draw_card("start", "CARL STARTS", f"{DIM}the dashboard reads what it shows{R}", L, w, 1)
    top = max((rows - len(card)) // 2, 0)
    pad = " " * max((cols - w) // 2, 0)
    return [""] * top + [pad + t for t, _ in card]


def panel_error(e: Exception, cols: int) -> List[Row]:
    """A Settings panel that could not be drawn: the reason instead of a crash."""
    lines = [f"{RED}{x}{R}" for x in wwrap(f"{type(e).__name__}: {e}", cols - 6)[:6]]
    lines += ["", *[f"{DIM}{x}{R}" for x in wwrap("CARL cannot read the catalogue (host/catalog.json), models.json or "
                                                  "config.json. Correct the file: ./carl.sh models and ./carl.sh config "
                                                  "show tell the problem. This panel tries again.", cols - 6)]]
    return indent(draw_card("seterror", "SETTINGS UNAVAILABLE", "", lines, cols - 1, 2))


def disk_free(path: str) -> int:
    """Free bytes on the disk holding path (0 if unknown)."""
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0


def main(argv: Optional[Sequence[str]], description: str) -> None:
    """Run the dashboard with these arguments (None: the command line); description is the --help text."""
    App.create(cli.parse(argv, description, os.environ)).run()
