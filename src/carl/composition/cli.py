"""The command line's wiring (W4.1): the Cli of this computer's profile (cli/commands/base.py) over its adapters.

Each command's services and adapters are built when it asks for them, not here (rule R6): the API over the settings
folder, the server file, the disk cache and the client config, the client package, the log's place, the GPU report
and Auto-tune's server and system calls.

The disk cache's folder rules and the client config's file are adapter modules (adapters/common slot_folder,
zstd_patch, session_names, client_config_store): this module is the one place that reaches them, behind the
DiskCacheFolder and ClientConfigStore ports (contracts C-036, C-037). The command line and the application never
import them.
"""
from __future__ import annotations

import json
import os
import platform
import sys
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from ..application.api import Api
from ..application.client_sync import ClientSync
from ..application.disk_cache import DiskCache
from ..cli.commands.base import Cli, CliTools
from ..cli.commands.gpu import GpuFacts, release_tag
from ..cli.commands.tune import TuneTools
from ..domain.clients.package import Outcome
from ..domain.config.key_paths import KEY_FILE
from ..domain.config.paths import home_path
from ..domain.hardware.devices import Gpu
from ..domain.shared.constants import DEFAULT_CTX, DEFAULT_PORT
from ..domain.shared.types import JsonObject
from ..ports import EngineAdapter
from ..ports.cache import CacheSettings, CachedFile
from . import engines as engine_wiring, llama as llama_wiring, profiles, wiring
from .profiles import Machine, Profile

EDIT_WORKLOAD = os.path.join(wiring.REPO, "src", "carl", "data", "tune-edit-workload.txt")
CARL_PY = os.path.join(wiring.REPO, "carl.py")
LATEST_LOG = "llama-server-latest.log"
GITHUB_RELEASE = "https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/{tag}"


def build(env: Mapping[str, str], profile: Profile, machine: Machine, home: str) -> Cli:
    """The Cli of this computer: its words, its OS, COLUMNS, the settings folder and the tools of the commands."""
    paths = wiring.CarlPaths.from_env(env, home)

    def api() -> Api:
        return Api(wiring.build_carl(paths, env, profile.words), paths.config, paths.local)

    tools = CliTools(
        carl_id=lambda: _carl_id(paths.conf_dir), engine=lambda: _engine(profile, machine, env, api),
        server_file=lambda: wiring.build_server_file(paths.conf_dir),
        disk_cache=lambda: DiskCache(MonitorDiskCache(os.path.join(paths.conf_dir, "slots"), home)),
        client_sync=lambda: ClientSync(MonitorClientConfig(paths.conf_dir)),
        make_package=lambda out_dir, anyway: _package(cli, env, out_dir, anyway),
        log_file=lambda: log_file(home, env.get("PORT") or str(DEFAULT_PORT)),
        gpu=lambda offline: gpu_facts(profile, machine, offline),
        tune=lambda: tune_tools(env, profile, home, machine, lambda: api().carl.fit.load_reserve()),
        paths=lambda: product_paths(env, home, paths),
        llama_install=lambda: llama_wiring.install_service(dict(env), profile, machine, home, _line),
        read_secret=read_secret)
    port = env.get("PORT", "")
    cli = Cli(profile.words, profile.os, env.get("COLUMNS", ""), home, paths.config, paths.local, api, tools,
              int(port) if port.isdigit() and 0 < int(port) < 65536 else DEFAULT_PORT)
    return cli


def read_secret(question: str) -> str:
    """A secret: the first line of standard input when it is piped, else the answer to question, with nothing shown as
    it is typed."""
    if sys.stdin.isatty():
        import getpass
        return getpass.getpass(question)
    return sys.stdin.readline()


def _carl_id(conf_dir: str) -> str:
    from ..adapters.common.runtime_files import carl_id
    return carl_id(conf_dir)


def _engine(profile: Profile, machine: Machine, env: Mapping[str, str], api: Callable[[], Api]) -> EngineAdapter:
    """The engine of the commands that name no model (CARL_ENGINE, the setting engine.name, else llama.cpp): its
    program is the locator's (CARL_LLAMA_DIR, llama.path off the Mac, else PATH)."""
    section = api().load_config().get("engine")
    return engine_wiring.default_adapter(profile, machine, env, section.get("name") if isinstance(section, dict) else None)


def _line(text: str) -> None:
    print(text, flush=True)


def _package(cli: Cli, env: Mapping[str, str], out_dir: Optional[str], anyway: bool) -> Outcome:
    """The client package (carl package): the zip of client/ with the server's address and key. The server's
    remote.json and api-key come from CARL_CLIENT_DIR when it is set, else client/."""
    from .package import make_package
    api = cli.api()
    from ..adapters.posix.start_tools import err
    return make_package(wiring.REPO, wiring.client_dir(env), out_dir or os.path.join(wiring.REPO, "dist"),
                        api.client_models(), api.load_config(), cli.words, anyway, cli.os, err, cli.plugins())


def log_file(home: str, port: str) -> str:
    """The log of the server on port (the log filter's pointer names it), else the latest log's link."""
    from ..adapters.common.log_filter import pointer_path
    logs = os.path.join(home, "models", "logs")
    try:
        with open(pointer_path(home, int(port)), encoding="utf-8") as f:
            log = json.load(f).get("log")
        if isinstance(log, str) and os.path.isfile(log):
            return log
    except (OSError, ValueError, AttributeError):
        pass
    return os.path.join(logs, LATEST_LOG)


def product_paths(env: Mapping[str, str], home: str, paths: wiring.CarlPaths) -> Dict[str, str]:
    """`carl paths --json`: where the product and its files are. key_file is the file the server uses and the
    clients read: API_KEY_FILE, else api-key in CARL_CONF_DIR, else ~/.config/carl/api-key (an earlier key file while
    only that holds a key, without CARL_CONF_DIR)."""
    from ..adapters.common.key_store import key_file
    return {"repo": wiring.REPO, "carl_py": CARL_PY, "catalog": paths.catalog, "client": wiring.client_dir(env),
            "conf_dir": paths.conf_dir, "config": paths.config, "models_json": paths.local,
            "key_file": key_file(env, home),
            "models_dir": env.get("MODELS_DIR") or os.path.join(home, "models", "gguf"),
            "logs": os.path.join(home, "models", "logs"), "templates": os.path.join(home, "models", "templates"),
            "slots": os.path.join(paths.conf_dir, "slots"),
            "default_port": str(DEFAULT_PORT), "default_ctx": str(DEFAULT_CTX)}


def gpu_facts(profile: Profile, machine: Machine, offline: bool) -> GpuFacts:
    """What `carl gpu` shows: every GPU of this computer (the GPU inventory, composition/gpu.py; a Mac asks the
    Linux tools, which find none), what a model can use on each, the release's files (GitHub, unless offline) and
    what the llama-server the locator finds sees."""
    from ..adapters.gpu.inventory import llama_program
    from .gpu import ram_reader
    from ..adapters.gpu.llama_devices import listed_devices
    from ..adapters.macos.sysctl_host import llama_server_version
    from ..domain.hardware.devices import all_share_ram
    from ..domain.llama.build import choose_build
    g = llama_wiring.gpu_state(profile, machine)
    # the profile once the GPUs are known: a PC whose GPUs all share the RAM is a shared_memory_pc (profiles.for_memory);
    # an APU beside a discrete GPU is not
    used = g.inventory.devices()
    settled = profiles.for_memory(profile, bool(used), bool(g.limit.unified) and all_share_ram(used))
    limit, inventory, listing, settings, main, cuda = g.limit, g.inventory, g.listing, g.settings, g.main, g.cuda
    names: List[str] = []
    if not offline:
        try:
            import urllib.request
            with urllib.request.urlopen(GITHUB_RELEASE.format(tag=release_tag()), timeout=20) as resp:
                names = [a.get("name", "") for a in json.load(resp).get("assets", [])]
        except (OSError, ValueError):
            names = []
    exe = llama_program(folder=machine.llama_dir, exe=llama_wiring.exe_suffix(profile))
    build_choice = choose_build(platform.system(), main, cuda, machine.rocm, settings.backend, g.others)
    return GpuFacts(platform.system(), platform.machine(), ram_reader(profile.os)(), listing, inventory.source(), cuda, main,
                    build_choice, names, exe, llama_server_version(exe) if exe else "?",
                    listed_devices(exe) if exe else [], limit.rooms(), settings.keep,  # type: ignore[attr-defined]
                    list(settings.notes), settings.shared_limit, settled.name == "shared_memory_pc")


def tune_tools(env: Mapping[str, str], profile: Profile, home: str, machine: Optional[Machine] = None,
               load_reserve: Optional[Callable[[], float]] = None) -> TuneTools:
    """Auto-tune's server and system calls: the profile's port probe and process table (the guard), the host's words
    (HostInfo: sysctl on a Mac, /proc on Linux, the registry on Windows; on a PC also its GPUs, the machine key of
    the record), llama.cpp's server through carl.py started by the profile's ProcessControl, llama-batched-bench from
    the locator's folder, and the server's key. Every OS measures the batch size (-ub; A45) from this OS's default.
    load_reserve: the load reserve of the settings (llama.load_reserve_gib), which the test starts get as
    LOAD_RESERVE_GIB (they read no config.json) unless the environment sets it."""
    from ..adapters.common.console import StepPrinter
    from ..adapters.common.key_store import key_file, read_key
    from ..adapters.llama.tune_control import LlamaServerControl
    from ..adapters.macos.sysctl_host import MacHostInfo, SystemClock, llama_server_version
    from ..domain.config.settings import LLAMA_KEYS, default_ub
    from ..domain.tune.scoring import UB_CANDIDATES
    from ..ports import ProcessControl
    from . import start
    where = machine or wiring.machine(env, home, profile.os)
    h = start.host(dict(env), profile, home, home_path(home, KEY_FILE))
    info: Any = MacHostInfo()                                   # HostInfo, with the processor's name alone (cpu_name)
    procs: ProcessControl
    if profile.os == "windows":
        from ..adapters.posix import start_tools
        from ..adapters.windows.host import WindowsHostInfo
        from ..adapters.windows.process import WindowsProcesses
        info, procs = WindowsHostInfo(), WindowsProcesses(note=start_tools.err)
    elif profile.os == "linux":
        from ..adapters.linux.host import LinuxHostInfo
        from ..adapters.posix.process import PosixProcesses
        info, procs = LinuxHostInfo(), PosixProcesses()
    else:
        from ..adapters.posix.process import PosixProcesses
        procs = PosixProcesses()
    if profile.os != "macos":
        from ..adapters.gpu.host_info import GpuHostInfo

        def gpus() -> List[Gpu]:
            return list(llama_wiring.gpu_state(profile, where).inventory.devices())  # type: ignore[attr-defined]
        info = GpuHostInfo(info, info.cpu_name, gpus)

    def server(model: str, port: int) -> LlamaServerControl:
        start_env, name = dict(env), str(LLAMA_KEYS["load_reserve_gib"].env)
        if load_reserve is not None and not start_env.get(name):
            start_env[name] = f"{load_reserve():g}"
        return LlamaServerControl(carl_py=CARL_PY, model_path=model, port=port,
                                  api_key=lambda: read_key(key_file(env, home)),
                                  log_path=os.path.join(home, "models", "logs", f".tune-{port}.out"),
                                  listening=h.probe.listening, processes=procs, env=start_env, python=sys.executable,
                                  bench=llama_wiring.llama_cpp(profile, where).batched_bench())
    return TuneTools(listening=h.probe.listening, processes=h.table.processes, host=info, cpu=info.cpu_name,
                     llama_version=lambda: llama_server_version(llama_wiring.server_program(where)), server=server,
                     progress=StepPrinter(), allow_second=env.get("ALLOW_SECOND_MODEL") == "1",
                     big_gb=env.get("BIG_GB"), pid=os.getpid(), edit_workload=EDIT_WORKLOAD,
                     today=SystemClock().today, ubs=UB_CANDIDATES, ub_default=default_ub(profile.os))


# ---------------------------------------------------------------- the dashboard's disk-cache modules
class MonitorDiskCache:
    """DiskCacheFolder over the slot folder, zstd patch and session names modules of the adapters (contract C-036)."""

    def __init__(self, folder: str, home: str) -> None:
        self._folder, self.home = folder, home

    def folder(self) -> str:
        return self._folder

    def settings(self, config: Mapping[str, object]) -> CacheSettings:
        from ..adapters.common import slot_folder as diskcache
        c = diskcache.config_of(dict(config))
        return CacheSettings(c.disk_gb, c.prefix, c.sessions, c.share)

    def files(self) -> List[CachedFile]:
        from ..adapters.common import session_names as sessionnames, slot_folder as diskcache
        return [CachedFile(f.name, f.kind == diskcache.PROMPT, f.bytes, f.mtime, f.packed, diskcache.describe(f.name),
                           sessionnames.session_id(f.name)) for f in diskcache.listing(self._folder)]

    def drop_legacy(self) -> None:
        from ..adapters.common import slot_folder as diskcache
        diskcache.remove(self._folder, diskcache.legacy(self._folder))

    def pack(self) -> int:
        from ..adapters.common import zstd_patch as slotpack
        return slotpack.tidy(self._folder).packed

    def over_limit(self, limit: int) -> List[str]:
        from ..adapters.common import slot_folder as diskcache
        return diskcache.over_budget(diskcache.listing(self._folder), limit)

    def saving(self) -> int:
        from ..adapters.common import slot_folder as diskcache
        return diskcache.shared_saving(diskcache.listing(self._folder))

    def remove(self, names: Sequence[str]) -> None:
        from ..adapters.common import slot_folder as diskcache
        diskcache.remove(self._folder, list(names))

    def session_names(self, ids: Sequence[str]) -> Dict[str, str]:
        from ..adapters.common import session_names as sessionnames
        return sessionnames.names(self.home, list(ids))


class MonitorClientConfig:
    """ClientConfigStore over the dashboard's clientsync module (contract C-037)."""

    def __init__(self, conf_dir: str) -> None:
        self.conf_dir = conf_dir

    def publish(self, models: JsonObject) -> str:
        from ..adapters.common import client_config_store as clientsync
        return clientsync.publish(self.conf_dir, models)
