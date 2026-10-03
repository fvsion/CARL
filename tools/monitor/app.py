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

from . import cli, fsio, system
from .api import Endpoint
from .card_view import draw_form
from .cards import View, status_of
from .clients import Drift, drift
from .collector import Collector
from .controller import Controller, Region
from .fmt import B, DIM, GRN, R, RED, YEL, Row, draw_card, fit, indent, pill, vlen, wwrap
from .jobs import Paths, ServerJobs
from .keys import InputBuffer
from .model import ServerData, jdict
from .settings import Pending, Schema, SettingsService, net_choices
from .settings_view import ModelsDir, SettingsView, subpanel_bar
from .state import SP_FIT, SP_MODELS, SP_ROUTER, SP_SERVER, TABS, UIState
from .store import CarlStore, ModelList
from .terminal import LOGO_COLS, Terminal, logo_escape, logo_mode, place_lines
from .views import body_connect, body_log, body_overview, body_requests, quit_dialog

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOGO_FILE = os.path.join(REPO, "assets", "carl-icon.png")
QUIT_LABEL = "[ Quit ]"


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
        self.ctl = Controller(ui, svc, jobs, view, collector.endpoint, collector.tail, REPO, opts.home)

    @classmethod
    def create(cls, opts: cli.Options) -> "App":
        """Wire the parts together (the composition root)."""
        machine = Machine(page=system.sysctl_int("hw.pagesize", 16384), total_mem=system.sysctl_int("hw.memsize", 1))
        host = opts.host or system.listen_host(system.netstat_tcp(), opts.port) or opts.env_host or "127.0.0.1"
        endpoint = Endpoint(host, opts.port, fsio.read_key(opts.key_file))
        collector = Collector(endpoint, fixed_host=bool(opts.host), key_file=opts.key_file, server_pid=opts.server_pid,
                              console=opts.console, log_arg=opts.log, home=opts.home, page=machine.page)
        ui = UIState(lines=opts.lines)
        if opts.expand:
            ui.levels.update({x: 2 for x in ui.levels})
        store = CarlStore()
        models = ModelList(store, on_error=lambda msg: ui.toast(f"{RED}{msg}{R}", 10))
        schema = Schema(net_choices(system.local_addrs(opts.vm_addr)))
        svc = SettingsService(models, schema, opts.vm_addr, gpu_limit=lambda: (collector.gpu_limit or store.gpu_limit())[0])
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
        """What the cards need besides the snapshot."""
        c, ep, ui = self.collector, self.endpoint, self.ui
        return View(levels=ui.levels, host=ep.host, port=ep.port, base=ep.base, key=ep.key, key_file=self.opts.key_file,
                    key_shown=ui.key_shown, server_pid=c.server_pid, log=c.tail.book, log_path=c.tail.path,
                    model_path=c.model_path, model_size=c.model_size, gpu_limit=c.gpu_limit, slow=c.slow,
                    total_mem=self.machine.total_mem, home=self.opts.home, wrap=ui.wrap, errors_only=ui.errors_only,
                    log_scroll=ui.log_scroll)

    def frame(self, d: ServerData) -> List[str]:
        """The screen's lines; the clickable regions go to the controller."""
        ui, once = self.ui, self.opts.once
        cols, rows = shutil.get_terminal_size((120, 36))
        regions = self.ctl.regions
        regions.clear()
        label, bg, _ = status_of(d, self.collector.server_pid)
        up = f" · up {d.etime}" if d.etime else ""
        kind = ("" if not d.up else f" · llama.cpp router ({len(d.router.models)} models)" if d.router is not None
                else " · llama.cpp")
        name = d.alias or ("no model loaded" if d.router is not None else "")
        left = f"{'' if self.logo else '😎 '}{B}CARL{R}  {pill(label, bg)}  {B}{name}{R}{DIM}{kind}{up}{R}"
        right = f"{DIM}{time.strftime('%H:%M:%S')}{R}  " + ("" if once else f"{B}{RED}{QUIT_LABEL}{R}")
        head = fit(left, cols - self.logo_cols - vlen(right) - 1) + " " + right
        regions.append(Region(1, cols - len(QUIT_LABEL) + 1, cols + 1, "quit"))
        tabs, x = " ", 2 + self.logo_cols
        for i, t in enumerate(TABS):
            lab = f" {i + 1} {t} "
            tabs += (f"\x1b[1;7m{lab}{R}" if i == ui.tab else f"{DIM}{lab}{R}") + " "
            regions.append(Region(2, x, x + len(lab), f"tab:{i}"))
            x += len(lab) + 1
        out = [head, fit(tabs, cols - self.logo_cols), fit(DIM + "─" * cols, cols)]
        height = (rows - 5) if not once else 10**4
        body = quit_dialog(d, ui, cols, height) if ui.quit else self.body(d, cols, height)
        for text, spans in body:
            y = len(out) + 1
            regions.extend(Region(y, a + 1, b + 1, act) for a, b, act in spans)
            out.append(fit(text, cols))
        if not once:
            while len(out) < rows - 2:
                out.append(" " * cols)
            out += [fit(DIM + "─" * cols, cols), fit(" " + self.footer(), cols)]
        if self.opts.demo:                       # screenshots: no key characters, no home path
            key = self.endpoint.key
            out = [ln.replace(self.opts.home, "~").replace(key[-4:] if key else "\0", "••••") for ln in out]
        return out

    def footer(self) -> str:
        """The bottom line: what is happening, a message, or key help."""
        ui = self.ui
        msg, until = ui.toast_msg
        if ui.stopping:
            return f"{YEL}stopping the server (pid {ui.stopping[0]})…{R}"
        if ui.restart:
            return f"{YEL}{ui.restart}{R}"
        if msg and time.time() < until:
            return f"{GRN}{msg}{R}"
        if ui.help:
            return (f"{DIM}1-5/Tab tabs · click a card title: more/less detail · e/c expand/collapse all · k key · "
                    f"o/p/t copy configs · w wrap · f errors only · ↑↓ PgUp PgDn scroll · space refresh · q quit · ? hide{R}")
        return f"{DIM}click a card title for detail · 1-5 tabs · o/p/t copy configs · k key · q quit · ? all keys{R}"

    def body(self, d: ServerData, cols: int, height: int) -> List[Row]:
        """The selected tab's rows."""
        ui, v = self.ui, self.view_ctx()
        if ui.tab == 0:
            log_name = os.path.basename(os.path.realpath(d.log_path)) if d.log_path else None
            return body_overview(v, ui, d, cols, height, log_name)
        if ui.tab == 1:
            here = fsio.installed_here(self.endpoint.base, self.opts.home)
            stale, installed = self.client_drift(here)
            return body_connect(v, ui, d, cols, height, here, self.ctl.preview_text(ui.preview, d, mask=not ui.key_shown),
                                stale, installed)
        if ui.tab == 2:
            return body_requests(v, ui, cols, height)
        if ui.tab == 3:
            n = max(height - 3, 3) if not self.opts.once else max(shutil.get_terminal_size((120, 36))[1] - 8, 10)
            return body_log(v, ui, cols, n)
        return self.body_settings(d, cols, height)

    def client_drift(self, here: List[Tuple[str, str]]) -> Tuple[List[Drift], int]:
        """The client configs on this Mac (for this server) that list other models than are
        installed, and how many are installed."""
        installed = [m["name"] for m in self.svc.models.downloaded()]
        return drift({c: fsio.listed_models(self.opts.home, c, pid) for c, pid in here}, installed), len(installed)

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
            return out + self.view.picker(ui.picker, cols, height - 2)
        if ui.confirm2:
            return out + self.view.confirm(ui.confirm2, cols)
        try:
            if ui.sp == SP_SERVER:
                body = self.view.server(ui, self.ensure_pending(d), d, cols, height - 2, self.endpoint.port)
            elif ui.sp == SP_MODELS and ui.card:
                body = draw_form(ui.card, cols, height - 2)
            elif ui.sp == SP_FIT:
                body = self.view.autofit(ui, self.ensure_pending(d), cols, height - 2)
            elif ui.sp == SP_ROUTER:
                book = self.collector.tail.book
                stale, n = self.client_drift(fsio.installed_here(self.endpoint.base, self.opts.home))
                body = self.view.router(ui, d, self.saved_mode(), [(book.wall(t), name) for t, name in book.switches],
                                        [s.line(n) for s in stale], cols)[:height - 2]
            elif ui.sp == SP_MODELS:
                mdir = self.store.models_dir()
                body = self.view.models(ui, cols, height - 2, ModelsDir(mdir, disk_free(mdir)))
            else:
                body = self.view.tune(ui, cols, d.up)
        except Exception as e:      # the catalogue, models.json or config.json can't be read: say so, keep running
            body = panel_error(e, cols)
        return (out + body)[:height]

    def ensure_pending(self, d: ServerData) -> Pending:
        """The Server panel's values, from config.json and the running server the first time."""
        ui = self.ui
        if ui.pending is None:
            ui.pending, ui.set_run = self.ctl.pending_init(d), self.svc.running(d)
        return ui.pending

    # ------------------------------------------------------------ main loop
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
            next_fetch = 0.0
            logo_size: Optional[os.terminal_size] = None
            logo_t = 0.0
            while True:
                if time.time() >= next_fetch:
                    self.ctl.data = c.collect()
                    next_fetch = time.time() + (0.5 if ui.stopping else self.opts.interval)
                self.jobs.poll()
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


def panel_error(e: Exception, cols: int) -> List[Row]:
    """A Settings panel that could not be drawn: the reason instead of a crash."""
    lines = [f"{RED}{x}{R}" for x in wwrap(f"{type(e).__name__}: {e}", cols - 6)[:6]]
    lines += ["", f"{DIM}The catalogue (host/catalog.json), models.json or config.json could not be read. Fix the file"
                  f" (./carl.sh models and ./carl.sh config show name the problem); this panel tries again.{R}"]
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
