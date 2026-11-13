"""The dashboard's host side: the ports it reaches this computer through, picked for this computer's profile (rule
R6: only the composition picks adapters and reads the environment). The dashboard (dashboard/app.py, loop.py) and
its use cases (application/collector.py, dashboard_api_host.py, client_status.py) get them from here and never
import an adapter.

  watch        the server's process, sockets and files, the memory, power and slow stats (ServerWatch, TcpTable,
               ServerFiles)
  endpoint     the server's address, key and API (ServerEndpoint)
  terminal     the interactive terminal (Terminal), built only for the interactive dashboard: --once never imports
               termios (Windows: the console, adapters/windows/console.py)
  desktop      the clipboard and the file manager (Desktop)
  cache        the disk cache's saved states and what the clients' cache did (CacheWatch)
  gpu, shapes  the GPU limit and the GGUF shapes (GpuLimit, ShapeReader): the limit through the budget's builder
               (FitService.gpu_limit of the dashboard entry, contract C-003's check), the shapes the process's adapter
  store        the models, config.json and the GGUF shapes (ModelStore of application/model_list.py) over the
               dashboard entry's typed API
  plugins      the plugins that are installed (PluginHost, src/carl/plugins): their cards, panels, checks and settings
  jobs_host    what the background jobs reach (JobsHost, C-132): the processes (ProcessControl), the files of a start
               and of the disk cache, the client config, the client package, the desktop and the platform's words
  client_rules the client package's model-entry rules (adapters/client_bundle.py) for the pasted client configs
  the rest     the dashboard API's server, the client lookups, the saved section levels, the start-time facts

macOS, Linux (W6.11) and Windows (W6.8) each have their own adapters. On Windows the GPUs and what each process holds
there come from the GPU inventory's Windows source (D3DKMT and the GPU counters, adapters/gpu/windows_counters.py),
wired here into the system stats (rule R3); the background jobs use the Windows ProcessControl (a hidden console of
its own, the console stop).

Built with no I/O: each part reads the computer only when it is asked.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ..application.client_status import ClientLookups
from ..application.dashboard_api_host import ApiServer, ClientRegistry
from ..application.model_list import CarlStore, ModelStore
from ..application.server_jobs.host import CacheSettings, JobsHost, Resident
from ..domain.clients.package import Outcome
from ..domain.memory.split import Slot
from ..domain.shared.platform_words import PlatformWords
from ..domain.server.snapshot import JSONDict
from ..ports import (CacheWatch, Desktop, GpuLimit, PluginHost, ProcessControl, ServerEndpoint, ServerWatch, ShapeReader,
                     Terminal)
from ..ports.cache import CacheEvent, StatRow, Tally
from ..ui.screen import logo_mode
from . import wiring
from .profiles import Machine, Profile

if TYPE_CHECKING:
    from ..application.fit import FitService

REMOVE_WAIT = 15.0          # seconds a file another process holds open may take to be free (Windows)


class FitGpuLimit:
    """The GPU limit the dashboard reads (GpuLimit, C-003): the budget's builder's (FitService.gpu_limit, which checks
    the adapter's answer), not the raw adapter, so the Settings tab's fit and `carl fit` read one limit (W2.4).
    fit: the dashboard entry's FitService, built at the first read (on the collector's background thread)."""

    def __init__(self, fit: Callable[[], "FitService"]) -> None:
        self._fit = fit

    @property
    def unified(self) -> bool:
        return self._fit().unified

    @property
    def dedicated(self) -> int:
        return self._fit().dedicated

    @property
    def shared_gpus(self) -> Tuple[str, ...]:
        return self._fit().shared_gpus

    def limit(self) -> Tuple[int, str]:
        return self._fit().gpu_limit()

    def slots(self) -> List[Slot]:
        return self._fit().slots()


class LocalCacheWatch:
    """CacheWatch (C-054) over the slots folder's and the cache events' modules (every OS: plain files)."""

    def __init__(self) -> None:
        from ..adapters.common import cache_events
        self._events = cache_events
        self.windows: Tuple[str, ...] = tuple(cache_events.WINDOWS)
        self._log: Any = None

    def files(self, folder: str) -> List[Any]:
        from ..adapters.common import slot_folder
        return list(slot_folder.listing(folder))

    def events(self, carl_dir: str) -> List[CacheEvent]:
        if self._log is None or self._log.dir != self._events.events_dir(carl_dir):   # another settings folder
            self._log = self._events.EventLog(carl_dir)
        try:
            self._log.poll()
        except Exception:           # a file that can't be read: what was read stays
            pass
        return list(self._log.events)

    def slot_column(self, events: Sequence[CacheEvent], slot: int, model: str, names: Mapping[str, str],
                    tokens: int = 0) -> Optional[Tuple[str, str, str]]:
        return self._events.slot_column(events, slot, model, names, tokens=tokens)

    def slot_session(self, events: Sequence[CacheEvent], slot: int, model: str, before: Optional[float] = None) -> str:
        return str(self._events.slot_session(events, slot, model, before))

    def table(self, events: Sequence[CacheEvent], tally: Sequence[Tally], since: float) -> List[StatRow]:
        return list(self._events.table(events, tally, since))

    def window_start(self, window: str, now: float, server_start: Optional[float]) -> float:
        return float(self._events.window_start(window, now, server_start))


class JobsHostAdapter:
    """JobsHost (C-132) over this computer's adapters. The processes: ProcessControl (posix/process.py on every OS
    until W6.4 gives Windows its own); the rest: plain files and the dashboard's modules of the disk cache and the
    client config (every OS). env: the environment the jobs' children start from; plugins: the installed plugins, asked
    when a package is made (their client files; None: none)."""

    def __init__(self, procs: ProcessControl, desktop: Desktop, words: PlatformWords, python: str,
                 env: Mapping[str, str], client_dir: Callable[[str], str],
                 alive: Optional[Callable[[int], bool]] = None,
                 kill_group: Optional[Callable[[int, int], None]] = None,
                 end: Optional[Callable[[int], None]] = None, remove_wait: float = 0.0, os_name: str = "",
                 plugins: Callable[[], Optional[PluginHost]] = lambda: None) -> None:
        self.procs, self.desktop, self.words, self.python = procs, desktop, words, python
        self._plugins = plugins
        self._os = os_name                                          # the package's key copy, on Windows (fix1.6)
        self._env, self._client_dir = env, client_dir
        self._alive, self._kill_group = alive, kill_group           # this computer's (default: the Mac's, as before)
        self._end = end
        self._remove_wait = remove_wait                             # Windows: REMOVE_WAIT (a held file); else none

    def env(self) -> Mapping[str, str]:
        return self._env

    def interrupt(self, pid: int) -> None:
        import signal
        if self._kill_group is None:
            from ..adapters.macos.system_stats import kill_group
            self._kill_group = kill_group
        self._kill_group(pid, signal.SIGINT)

    def end(self, pid: int) -> None:
        if self._end is None:
            import signal
            from ..adapters.macos.system_stats import kill
            kill(pid, signal.SIGTERM)                   # a Mac and Linux: SIGTERM, as before
            return
        self._end(pid)

    def alive(self, pid: int) -> bool:
        if self._alive is None:
            from ..adapters.macos.system_stats import pid_alive
            self._alive = pid_alive
        return self._alive(pid)

    def healthy(self, host: str, port: int, key: str) -> bool:
        from ..adapters.common.llama_http import health_ok
        return health_ok(host, port, key)

    # ------------------------------------------------------------ files
    def read_text(self, path: str) -> Optional[str]:
        from ..adapters.common import installed_clients
        return installed_clients.read_text(path, errors="replace")

    def read_lines(self, path: str) -> List[str]:
        from ..adapters.common import installed_clients
        return installed_clients.read_lines(path)

    def file_size(self, path: str) -> int:
        from ..adapters.common import installed_clients
        return installed_clients.file_size(path)

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def write_private(self, path: str, text: str) -> None:
        from ..adapters.common import installed_clients
        installed_clients.write_private(path, text)

    def remove(self, path: str) -> None:
        """Remove the file. Windows refuses while another process holds it open (a start that is still ending holds
        its console file: W6.8): there (remove_wait) try again for a while, then empty it instead. Elsewhere a
        refusal is raised, as before."""
        if not self._remove_wait:
            os.remove(path)
            return
        import time
        end = time.monotonic() + self._remove_wait
        while True:
            try:
                os.remove(path)
                return
            except PermissionError:
                if time.monotonic() >= end:
                    break
                time.sleep(0.25)
        with open(path, "w", encoding="utf-8", newline="\n"):
            pass

    def make_folder(self, path: str) -> None:
        os.makedirs(path, exist_ok=True)

    # ------------------------------------------------------------ the disk cache
    def cache_settings(self, config: JSONDict) -> CacheSettings:
        from ..adapters.common import slot_folder
        return slot_folder.config_of(config)

    def tidy_cache(self, folder: str, conf_dir: str, now: float, pack: bool) -> None:
        from ..adapters.common import cache_events, slot_folder, zstd_patch
        slot_folder.remove(folder, slot_folder.legacy(folder))
        slot_folder.reconcile(folder, now)              # what clients that went away left
        cache_events.prune(conf_dir)                    # the counters' events older than two days
        if pack:
            zstd_patch.tidy(folder)                     # new saves as patches against their prompt, copies gone

    def over_limit(self, folder: str, limit: int, keep: str) -> List[str]:
        from ..adapters.common import slot_folder
        return slot_folder.over_budget(slot_folder.listing(folder), limit, keep)

    def cache_files(self, folder: str) -> List[Any]:
        from ..adapters.common import slot_folder
        return list(slot_folder.listing(folder))

    def remove_states(self, folder: str, names: Sequence[str]) -> None:
        from ..adapters.common import slot_folder
        slot_folder.remove(folder, list(names))

    def free_enough(self, folder: str) -> bool:
        from ..adapters.common import slot_folder
        return slot_folder.free_enough(folder)

    def residents(self, folder: str, model: str) -> List[Resident]:
        from ..adapters.common import slot_folder
        return list(slot_folder.residents(folder, model))

    def drop_resident(self, resident: Resident) -> None:
        from ..adapters.common import slot_folder
        slot_folder.drop_resident(resident)  # type: ignore[arg-type]

    def turns(self, folder: str, model: str) -> List[int]:
        from ..adapters.common.slot_claims import SlotClaims
        return SlotClaims(folder).turns(model)

    def saving(self, folder: str, within: float) -> bool:
        import time
        try:
            return any(n.startswith("carl-") and n.endswith(".bin")
                       and time.time() - os.path.getmtime(os.path.join(folder, n)) < within
                       for n in os.listdir(folder))
        except OSError:
            return False

    # ------------------------------------------------------------ the clients
    def publish(self, conf_dir: str, models: JSONDict) -> str:
        from ..adapters.common import client_config_store
        return client_config_store.publish(conf_dir, models)

    def published(self, conf_dir: str) -> Optional[JSONDict]:
        from ..adapters.common import client_config_store
        return client_config_store.published(conf_dir)

    def installed_here(self, base: str, home: str) -> bool:
        from ..adapters.common import installed_clients
        return bool(installed_clients.installed_here(base, home))

    def llama_backends(self, conf_dir: str) -> Optional[List[str]]:
        from ..adapters.llama.locator import configured_path
        from ..adapters.llama.release_installer import builds_in
        from ..domain.config.settings import LLAMA_KEYS
        from ..domain.llama.release import SUBFOLDER, folder_key
        if self._env.get(str(LLAMA_KEYS["path"].env)) or configured_path(conf_dir):
            return None
        keys = [folder_key(os.path.basename(p)) for p in builds_in(os.path.join(conf_dir, SUBFOLDER))]
        return [k[1] for k in keys if k is not None]

    def make_package(self, repo: str, models: JSONDict, config: JSONDict) -> Outcome:
        import zipfile
        from .package import make_package
        try:
            plugins = self._plugins()
        except Exception:  # noqa: BLE001  (no plugins: the package of client/ alone)
            plugins = None
        try:
            return make_package(repo, self._client_dir(repo), os.path.join(repo, "dist"), models, config, self.words,
                                os_name=self._os, plugins=plugins)
        except zipfile.BadZipFile as e:          # a broken zip in the way: said as the other errors are
            raise ValueError(str(e)) from e


@dataclass
class DashboardHost:
    """The dashboard's ports for this computer. env: the environment the dashboard runs with."""
    profile: Profile
    machine: Machine
    env: Mapping[str, str]
    _clients: List[ClientLookups] = field(default_factory=list, repr=False)
    _fit: List["FitService"] = field(default_factory=list, repr=False)
    _plugins: List[PluginHost] = field(default_factory=list, repr=False)

    # ------------------------------------------------------------ the start-time facts
    def linux(self) -> bool:
        """This computer reads its facts from /proc and /sys (adapters/linux); otherwise from the Mac's commands."""
        return self.profile.os == "linux"

    def windows(self) -> bool:
        """This computer reads its facts from Windows (adapters/windows)."""
        return self.profile.os == "windows"

    def page_size(self) -> int:
        if self.windows():
            return 4096
        if self.linux():
            from ..adapters.linux.host import page_size
            return page_size()
        from ..adapters.macos.system_stats import sysctl_int
        return sysctl_int("hw.pagesize", 16384)

    def total_memory(self) -> int:
        if self.windows():
            from ..adapters.windows.host import ram_bytes as windows_ram
            return windows_ram() or 1
        if self.linux():
            from ..adapters.linux.host import ram_bytes
            return ram_bytes() or 1
        from ..adapters.macos.system_stats import sysctl_int
        return sysctl_int("hw.memsize", 1)

    def llama_version(self) -> str:
        """The "version:" line of `llama-server --version` ("?" when it cannot run). Off the Mac the llama-server is the
        one CARL starts: in the llama.cpp folder of the settings (CARL_LLAMA_DIR, llama.path, `carl engine install`)."""
        from ..adapters.macos.sysctl_host import llama_server_version
        if self.linux() or self.windows():
            from ..adapters.gpu.inventory import llama_program
            from .llama import program_ending
            exe = llama_program(folder=self.machine.llama_dir, exe=program_ending())
            return llama_server_version(exe) if exe else "?"
        return llama_server_version()

    def local_addresses(self, vm_addr: str) -> List[str]:
        """This computer's IPv4 addresses for the network choices, without loopback and the VM address."""
        if self.linux() or self.windows():
            from ..adapters.common.socket_addresses import LOOPBACK, SocketAddresses
            return [a for a in SocketAddresses().local_addresses() if a not in (LOOPBACK, vm_addr)]
        from ..adapters.macos.system_stats import local_addrs
        return local_addrs(vm_addr)

    def logo_kind(self) -> Optional[str]:
        """The image protocol of the logo on this terminal (CARL_LOGO, TERM_PROGRAM), None: an emoji."""
        return logo_mode(self.env)

    # ------------------------------------------------------------ the ports
    def watch(self, page: int) -> ServerWatch:
        if self.windows():
            from ..adapters.windows.system_stats import WindowsServerWatch
            paths = wiring.CarlPaths.from_env(self.env, os.path.expanduser("~"))
            return WindowsServerWatch(page, paths.conf_dir, wiring.logs_dir(os.path.expanduser("~")), windows_gpus())
        if self.linux():
            from ..adapters.linux.system_stats import LinuxServerWatch
            return LinuxServerWatch(page)
        from ..adapters.macos.system_stats import MacServerWatch
        return MacServerWatch(page)

    def terminal(self) -> Terminal:
        if self.windows():
            from ..adapters.windows.console import WindowsConsole
            return WindowsConsole()
        from ..adapters.posix.terminal import Terminal as PosixTerminal       # termios: only here (rule R4)
        return PosixTerminal()

    def desktop(self) -> Desktop:
        if self.windows():
            from ..adapters.windows.desktop import WindowsDesktop
            return WindowsDesktop()
        if self.linux():
            from ..adapters.linux.desktop import LinuxDesktop
            return LinuxDesktop()
        from ..adapters.macos.desktop import MacDesktop
        return MacDesktop()

    def cache(self) -> CacheWatch:
        """The disk cache's saved states and what the clients' cache did (the Caching panel, the SLOTS card)."""
        return LocalCacheWatch()

    def gpu(self) -> GpuLimit:
        """The GPU limit through the budget's builder: the dashboard entry's FitService over the GPU limit the process
        shares (the fit and the settings read the same one)."""
        return FitGpuLimit(self.fit)

    def fit(self) -> "FitService":
        """The dashboard entry's FitService (application/fit.py), built on first use."""
        if not self._fit:
            from .entries import Entry
            paths = wiring.CarlPaths.from_env(self.env, os.path.expanduser("~"))
            self._fit.append(Entry("dashboard", self.profile, paths, self.machine, self.env).app().fit)
        return self._fit[0]

    def shapes(self) -> ShapeReader:
        return wiring.shapes()

    def store(self) -> ModelStore:
        """The models, config.json and the GGUF shapes over the dashboard entry's typed API (application/api.py)."""
        from .entries import build_dashboard
        return CarlStore(build_dashboard(self.env).api())

    def plugins(self) -> PluginHost:
        """The plugins of the dashboard entry's settings folder (src/carl/plugins; built on first use)."""
        if not self._plugins:
            from .entries import build_dashboard
            host = build_dashboard(self.env).app().plugins
            assert host is not None
            self._plugins.append(host)
        return self._plugins[0]

    def vram_keep(self) -> Optional[int]:
        """The memory kept free on each GPU (gpu.vram_keep and its variable, bytes; a PC's MEMORY card); None on a
        Mac, which has no such setting."""
        if self.profile.os == "macos":
            return None
        return self.fit().gpu_settings().keep

    def jobs_host(self) -> JobsHost:
        """What the dashboard's background jobs reach of this computer (JobsHost, C-132)."""
        import sys
        if self.windows():
            from ..adapters.windows import process as windows_process
            from ..adapters.windows.server_runner import recorded_start
            conf = wiring.CarlPaths.from_env(self.env, os.path.expanduser("~")).conf_dir
            procs = windows_process.WindowsProcesses(lambda pid: recorded_start(conf, pid))
            return JobsHostAdapter(procs, self.desktop(), self.profile.words, sys.executable, self.env,
                                   lambda repo: wiring.client_dir(self.env, repo), procs.alive,
                                   lambda pid, _sig: windows_interrupt(pid), windows_interrupt, REMOVE_WAIT,
                                   self.profile.os, self.plugins)
        from ..adapters.posix.process import PosixProcesses
        alive = kill_group = None
        if self.linux():
            from ..adapters.linux import system_stats
            alive, kill_group = system_stats.pid_alive, system_stats.kill_group
        return JobsHostAdapter(PosixProcesses(), self.desktop(), self.profile.words, sys.executable, self.env,
                               lambda repo: wiring.client_dir(self.env, repo), alive, kill_group,
                               os_name=self.profile.os, plugins=self.plugins)

    def client_rules(self) -> Any:
        """The client package's model-entry rules (its data/thinking.json), through the one bridge to it."""
        from ..adapters import client_bundle
        return client_bundle.model_rules()

    def key_file(self, home: str) -> str:
        """The key file the server uses (API_KEY_FILE, CARL's own path, or an earlier one that holds a key)."""
        from ..adapters.common.key_store import key_file
        return key_file(self.env, home)

    def endpoint(self, host: str, port: int, key_file: str) -> ServerEndpoint:
        """The dashboard's client of the llama-server API (ServerEndpoint; its key read from key_file)."""
        from ..adapters.common.llama_http import Endpoint
        from ..adapters.common.key_store import read_key
        return Endpoint(host, port, read_key(key_file))

    # ------------------------------------------------------------ the saved section levels (dashboard.json)
    def prefs_path(self, config_file: str) -> str:
        from ..adapters.common import ui_prefs
        return ui_prefs.path_for(config_file)

    def saved_detail(self, path: str) -> str:
        from ..adapters.common import ui_prefs
        return ui_prefs.load_detail(path)

    def saved_levels(self, path: str) -> Dict[str, int]:
        from ..adapters.common import ui_prefs
        return dict(ui_prefs.load_levels(path))

    def save_detail(self, path: str, detail: str, levels: Dict[str, int]) -> bool:
        """Save the detail level and each section's level (False: the folder cannot be written)."""
        from ..adapters.common import ui_prefs
        return ui_prefs.save_detail(path, detail, levels)

    # ------------------------------------------------------------ the dashboard API and the clients
    def api_server(self, host: str, port: int, key: Callable[[], str], folder: str, conf: Callable[[], object],
                   save_recorded: Callable[[str], int], conf_dir: str, repo: str, server_port: Callable[[], int],
                   status: Callable[[], JSONDict]) -> ApiServer:
        """The dashboard API's server for host:port (not started): the cache files of folder, this CARL's ID and
        version."""
        from ..adapters.common import runtime_files
        from .cache_api import CacheApi
        from .package import carl_version
        return CacheApi(host, port, key, folder, conf, save_recorded,  # type: ignore[arg-type]
                        conf_dir, carl_version(repo), runtime_files.carl_id(conf_dir), server_port, status)

    def publish_api(self, repo: str, conf_dir: str, server_port: int, api_port: int, url: str) -> None:
        """The API's port into the server's file (server-PORT.json) and the client folder's remote.json."""
        from ..adapters.common import runtime_files
        from .package import publish_api_port
        runtime_files.set_api_port(conf_dir, server_port, api_port)
        publish_api_port(repo, server_port, url, wiring.client_dir(self.env, repo), self.profile.os)

    def clients_registry(self, path: str) -> ClientRegistry:
        """The computers that synced (clients.json), read while the API does not run."""
        from ..adapters.common.client_registry import Registry
        return Registry(path)

    def session_names(self, home: str, ids: Sequence[str]) -> Dict[str, str]:
        """The names the clients' stores here give these sessions (only the IDs a store knows)."""
        from ..adapters.common import session_names
        return session_names.names(home, ids)

    def session_id(self, file_name: str) -> str:
        """The session ID in a saved session's file name."""
        from ..adapters.common import session_names
        return session_names.session_id(file_name)

    def server_file(self, conf_dir: str, port: int) -> JSONDict:
        """What the launcher wrote for the server on port (server-PORT.json), {} when there is none."""
        from ..adapters.common import runtime_files
        return runtime_files.read(conf_dir, port)

    def clients(self) -> ClientLookups:
        """How the client status reads this computer's client setups."""
        if not self._clients:
            from ..adapters.common import client_config_store, installed_clients
            self._clients.append(ClientLookups(
                installed_here=lambda base, home: installed_clients.installed_here(base, home),
                moved_here=lambda base, home: installed_clients.moved_here(base, home),
                listed_models=lambda home, client, provider: installed_clients.listed_models(home, client, provider),
                coder_model_here=lambda base, home: installed_clients.coder_model_here(base, home),
                versions=lambda repo, home: installed_clients.client_versions(repo, home, os.environ),
                version_of=lambda doc: client_config_store.version_of(doc),
                file_size=lambda path: installed_clients.file_size(path)))
        return self._clients[0]


def windows_gpus() -> Callable[[], Tuple[List[Any], Dict[int, Dict[str, int]]]]:
    """Windows: the GPUs (D3DKMT and the GPU counters, any vendor) and what each process holds there, read at each
    call (the dashboard's system stats)."""
    from ..adapters.gpu.windows_counters import WindowsCounters
    from ..adapters.windows import gpu_counters
    counters = WindowsCounters(gpu_counters.adapters, gpu_counters.counter_values)

    def read() -> Tuple[List[Any], Dict[int, Dict[str, int]]]:
        return counters.gpus(), counters.every_process_vram()
    return read


def windows_interrupt(pid: int) -> None:
    """Windows: Ctrl-C to a process's console (the stop helper), in the background (it takes a moment): Auto-tune's
    stop and the quit dialog's (the loop ends the server after its wait, as on a Mac)."""
    import threading
    from ..adapters.windows import stop_helper
    threading.Thread(target=stop_helper.send_ctrl_c, args=(pid,), daemon=True).start()


def host(env: Optional[Mapping[str, str]] = None, platform: Optional[str] = None) -> DashboardHost:
    """The dashboard's host side for this computer (env: default os.environ)."""
    env = os.environ if env is None else env
    prof = wiring.profile(env, platform)
    return DashboardHost(prof, wiring.machine(env, os_name=prof.os), env)


def run_here(args: Sequence[str]) -> int:
    """The dashboard with these arguments in this process, until it quits; its exit code (Windows has no exec: the
    start and the dispatch run it here, W6.8)."""
    from ..dashboard.app import main
    from ..dashboard.options import help_text
    try:
        main(list(args), help_text())
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    return 0


def environment() -> Mapping[str, str]:
    """The environment the dashboard's options are read from (the command line's --port defaults, and the like)."""
    return os.environ
