"""Composition root: where CARL's files are, the profile of this computer (profiles.py) and the adapters of the
ports. The entries' builders are in entries.py.

Nothing is built at import (rule R6). The adapters this module shares in a process are built on first use and kept:
the header cache (header reads are 64 MB each) and the GPU limit (a Swift probe on a Mac). The old module names
GPU, HOST, SHAPES and MAC are lazy accessors of the same objects, for the callers that still use them.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Mapping, Optional, Sequence

from ..adapters.common.console import StdConsole
from ..adapters.common.downloader import Aria2OrCurl, HubDownloader
from ..adapters.common.model_folder import LocalModelFolder
from ..adapters.common.gguf_reader import GgufShapes
from ..adapters.common.hf_http import HfHttpClient
from ..adapters.common.json_files import JsonFile, LegacyEnvFiles
from ..adapters.common.runtime_files import RuntimeFiles, now_text
from ..application.app import Carl, Stores
from ..application.server_file import ServerFileService
from ..domain.config.paths import conf_dir
from ..domain.config.settings import LLAMA_KEYS
from ..domain.shared.platform_words import PlatformWords
from ..plugins import PluginRegistry
from ..ports import GpuLimit, HostMemory, Plugin, ServerFit
from . import gpu as gpu_wiring, hub as hub_wiring, profiles
from .profiles import Machine, Profile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
METAL_PROBE = os.path.join(REPO, "src", "carl", "adapters", "macos", "metal_limit.swift")


def models_home(home: str) -> str:
    """~/models: the header cache and the Metal limit's cache are there."""
    return os.path.join(home, "models")


def shape_cache(home: str) -> str:
    return os.path.join(models_home(home), ".gguf-shapes.json")       # headers of files not downloaded


def metal_limit_cache(home: str) -> str:
    return os.path.join(models_home(home), ".metal-limit")


def logs_dir(home: str) -> str:
    """~/models/logs: the jobs' output; on Windows also the keep-awake child's state files (the start writes them, the
    dashboard reads them)."""
    return os.path.join(models_home(home), "logs")


def profile(env: Optional[Mapping[str, str]] = None, platform: Optional[str] = None) -> Profile:
    """The profile of this computer, with CARL_CMD as its command word when it is set."""
    env = os.environ if env is None else env
    return profiles.select(sys.platform if platform is None else platform).with_command(env.get("CARL_CMD") or "")


def words(env: Optional[Mapping[str, str]] = None, platform: Optional[str] = None) -> PlatformWords:
    """The platform words of this process (the command word: CARL_CMD, else carl)."""
    return profile(env, platform).words


def machine(env: Optional[Mapping[str, str]] = None, home: Optional[str] = None,
            os_name: Optional[str] = None) -> Machine:
    """What the hardware adapters are built from. CARL_LLAMA_DIR, CARL_ROCM and the GPU variables (gpu.py GPU_VARS)
    are read here only. os_name: the profile's OS. Off the Mac the llama.cpp folder is CARL_LLAMA_DIR, else the
    setting llama.path, else the newest build `carl engine install` made (adapters/llama/locator.py); a Mac (or no OS)
    has CARL_LLAMA_DIR alone, as it always had."""
    env = os.environ if env is None else env
    home = home or os.path.expanduser("~")
    where = conf_dir(home, env, os.path.isdir)
    llama_dir = env.get(str(LLAMA_KEYS["path"].env)) or None
    if os_name not in (None, "macos"):
        from ..adapters.llama.locator import configured_path, resolve_folder
        llama_dir = resolve_folder(llama_dir, configured_path(where), where)
    return Machine(METAL_PROBE, metal_limit_cache(home), llama_dir, env.get("CARL_ROCM") == "1",
                   gpu_wiring.env_values(env), where)


def client_dir(env: Optional[Mapping[str, str]] = None, repo: str = REPO) -> str:
    """The folder of the server's remote.json and api-key for the clients: CARL_CLIENT_DIR (read here only), else
    client/ in the clone (repo: another clone, as the dashboard's tests give it)."""
    env = os.environ if env is None else env
    return env.get("CARL_CLIENT_DIR") or os.path.join(repo, "client")


# The adapters shared by a process: built on first use, then kept.
_SHARED: Dict[str, object] = {}


def _shared(name: str, build: Callable[[], object]) -> object:
    if name not in _SHARED:
        _SHARED[name] = build()
    return _SHARED[name]


def _shapes() -> GgufShapes:
    """The header reader, with the user's Hugging Face token (a gated repo's header needs it)."""
    home = os.path.expanduser("~")
    token = hub_wiring.hub_token(os.environ, home, profile().os, CarlPaths.from_env(os.environ, home).conf_dir)
    return GgufShapes(shape_cache(home), hub_wiring.hf_auth(token))


def shapes() -> GgufShapes:
    return _shared("shapes", _shapes)  # type: ignore[return-value]


def gpu(p: Optional[Profile] = None, m: Optional[Machine] = None) -> GpuLimit:
    """The GPU limit of this computer (the first caller's profile and machine build it). The machine is this OS's: it
    finds the llama-server CARL installed, whose device list decides which -dev names a start may get."""
    return _shared("gpu", lambda: _on(p, m, "gpu"))  # type: ignore[return-value]


def host(p: Optional[Profile] = None, m: Optional[Machine] = None) -> HostMemory:
    return _shared("host", lambda: _on(p, m, "host"))  # type: ignore[return-value]


def _on(p: Optional[Profile], m: Optional[Machine], what: str) -> object:
    """The profile's gpu or host over the machine of its OS (the llama.cpp folder: machine())."""
    prof = p or profile()
    return getattr(prof, what)(m or machine(os_name=prof.os))


_LAZY: Dict[str, Callable[[], object]] = dict(GPU=gpu, HOST=host, SHAPES=shapes, MAC=lambda: profile().os == "macos")


def __getattr__(name: str) -> object:
    """GPU, HOST, SHAPES and MAC: the shared adapters, built on first use (PEP 562)."""
    if name in _LAZY:
        return _LAZY[name]()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


@dataclass(frozen=True)
class CarlPaths:
    catalog: str
    conf_dir: str

    @property
    def config(self) -> str:
        return os.path.join(self.conf_dir, "config.json")

    @property
    def local(self) -> str:
        return os.path.join(self.conf_dir, "models.json")

    @property
    def legacy_llama(self) -> str:            # before config.json (migrated once)
        return os.path.join(self.conf_dir, "llama.env")

    @classmethod
    def from_env(cls, env: Mapping[str, str], home: Optional[str] = None,
                 is_dir: Callable[[str], bool] = os.path.isdir) -> CarlPaths:
        """CARL_CATALOG and CARL_CONF_DIR override the defaults (tests use a temporary folder).
        Without CARL_CONF_DIR: ~/.config/carl, or its old name until it is moved (domain/confdir.py)."""
        return cls(catalog=env.get("CARL_CATALOG", os.path.join(REPO, "src", "carl", "data", "catalog.json")),
                   conf_dir=conf_dir(home or os.path.expanduser("~"), env, is_dir))


PLUGINS_FOLDER = "plugins"                  # in the settings folder: the plugins installed beside CARL (one folder or .py each)
PLUGIN_DATA = "plugin-data"                 # in the settings folder: a folder of its own for each plugin's files


def build_plugins(paths: CarlPaths, os_name: str, carl: Callable[[], Carl],
                  extra: Sequence[Plugin] = (), env: Optional[Mapping[str, str]] = None) -> PluginRegistry:
    """The plugin registry of one settings folder (src/carl/plugins): the built-in plugins, and the ones in the settings
    folder's plugins folder. Their saved settings are config.json's plugins section, read and written through the Carl
    of the same folder (carl: asked on first use). The hooks get what CARL tells of this computer (composition/
    computer.py, C-066) and the sessions' own folders (C-067). Nothing is read until the first question."""
    from ..adapters.common.session_folders import SessionFolderStore
    from ..domain.server.session_folders import FOLDER as SESSIONS
    from .computer import Computer
    facts = dict(env if env is not None else os.environ)
    computer = Computer(facts, profile(facts), lambda: carl().models_dir(carl().load_config()))
    return PluginRegistry(
        [os.path.join(paths.conf_dir, PLUGINS_FOLDER)], os_name, os.path.join(paths.conf_dir, PLUGIN_DATA),
        read_settings=lambda: {pid: dict(sec) for pid, sec in carl().load_config().plugins.items()},
        write_setting=lambda plugin, key, value: carl().config.set_plugin_setting(plugin, key, value),
        now=time.monotonic, extra=extra, env=env, computer=computer,
        sessions=SessionFolderStore(os.path.join(paths.conf_dir, SESSIONS)))


def build_carl(paths: CarlPaths, env: Mapping[str, str], pw: Optional[PlatformWords] = None) -> Carl:
    """The Carl of one settings folder, over the shared adapters of this computer's profile. pw: the platform
    words of its texts (default: this process's, words(env))."""
    stores = Stores(catalog=JsonFile(paths.catalog), catalog_path=paths.catalog, config=JsonFile(paths.config),
                    local=JsonFile(paths.local), local_path=paths.local,
                    legacy=LegacyEnvFiles(paths.legacy_llama))
    from ..adapters.macos.sysctl_host import SystemClock      # every OS: the date (R4: imported when built)
    prof = profile(env)
    m = machine(env, os_name=prof.os)
    made: List[Carl] = []

    def server_fit() -> ServerFit:
        """llama-server's own fit, for Auto fit's pick (A67; built when a pick is measured: start imports this module)."""
        from .start import server_fit as fit_of
        return fit_of(env, prof, m, paths.conf_dir)

    def build_backend() -> str:
        """The backend of the llama.cpp build a start runs (A71: the CPU alone is a build that is a CPU's; A72: a build
        of your own is classed from what it offers, and its engine record is written)."""
        import shutil
        from ..adapters.llama.build_class import classify
        from .llama import exe_suffix
        return classify(m.llama_dir, exe_suffix(prof), which=shutil.which)

    seen: Dict[str, str] = {}

    def build_version() -> str:
        """The installed llama-server's version line (A72: a tune record is checked against it), asked once per program."""
        from ..adapters.macos.sysctl_host import llama_server_version
        from .llama import server_program
        program = server_program(m)
        if program not in seen:
            seen[program] = llama_server_version(program)
        return seen[program]

    home = os.path.expanduser("~")
    token = hub_wiring.hub_token(env, home, prof.os, paths.conf_dir)
    auth = hub_wiring.hf_auth(token)
    carl = Carl(stores, LocalModelFolder(), shapes(), gpu(prof, m), host(prof, m), HfHttpClient(auth),
                HubDownloader(Aria2OrCurl(), auth), SystemClock(), StdConsole(), home=home,
                env_models_dir=env.get("MODELS_DIR") or None, words=pw or prof.words, os_name=prof.os,
                gpu_env=dict(m.gpu_env), plugins=build_plugins(paths, prof.os, lambda: made[0], env=env),
                cpu=prof.cpu() if prof.cpu else None, server_fit=server_fit,
                build_backend=build_backend, build_version=build_version, hub_token=token)
    made.append(carl)
    return carl


def build_server_file(conf_dir: str) -> ServerFileService:
    """The server file (server-PORT.json) of one settings folder."""
    return ServerFileService(RuntimeFiles(conf_dir), now_text)
