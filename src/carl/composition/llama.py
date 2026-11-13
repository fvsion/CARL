"""The llama.cpp wiring (W6.2): the LlamaCpp port of this computer's profile, the pinned release's installer and
service, and the first-run offer (Dependencies) of Windows and Linux.

  macos            HomebrewLlama (PATH or CARL_LLAMA_DIR; brew upgrade llama.cpp); the Dependencies are HomebrewDeps
                   (composition/start.py); `engine install` does not run there
  windows, linux   LlamaLocator over Machine.llama_dir (CARL_LLAMA_DIR, else llama.path, else the newest build in the
                   settings folder's llama.cpp folder: wiring.machine resolves it); the offer installs the pinned
                   build (data/llamacpp-release.json) after a yes on a terminal, or prints `carl engine install`

Nothing is built at import (rule R6). The build for a GPU is the GPU inventory's (the same reading as `carl gpu`).
"""
from __future__ import annotations

import os
import platform
import shutil
import sys
from dataclasses import dataclass
from typing import Callable, List, MutableMapping, Optional, Tuple

from ..application.llama_install import LlamaInstall
from ..application.start_server import LLAMA_SERVER
from ..domain.config.settings import LLAMA_KEYS, key_path, set_path
from ..domain.hardware.devices import Gpu
from ..domain.hardware.gpu import Device
from ..domain.llama.build import BuildChoice, choose_build
from ..domain.llama.release import SUBFOLDER, Release, parse_release
from ..domain.memory.budget import GpuSettings
from ..ports import Dependencies, LlamaCpp
from . import gpu as gpu_wiring, wiring
from .profiles import Machine, Profile

RELEASE_FILE = os.path.join(wiring.REPO, "src", "carl", "data", "llamacpp-release.json")
_RELEASE: List[Release] = []


def release() -> Release:
    """The pinned release (data/llamacpp-release.json), read once. ConfigError when it is damaged."""
    if not _RELEASE:
        from ..adapters.common.json_files import JsonFile
        _RELEASE.append(parse_release(JsonFile(RELEASE_FILE).load()))
    return _RELEASE[0]


def arch_of(machine: str) -> str:
    """The release's architecture name of platform.machine(): arm64 or x64."""
    return "arm64" if machine.lower() in ("arm64", "aarch64") else "x64"


def exe_suffix(profile: Profile) -> str:
    return ".exe" if profile.os == "windows" else ""


def program_ending() -> str:
    """The ending of a program's file on the OS this process runs on (the adapters read no sys.platform)."""
    return ".exe" if sys.platform == "win32" else ""


def llama_cpp(profile: Profile, machine: Machine) -> LlamaCpp:
    """The LlamaCpp of this computer: Homebrew's on a Mac, the locator over Machine.llama_dir elsewhere."""
    update = profile.words.update_hint()
    if profile.os == "macos":
        from ..adapters.llama.homebrew import HomebrewLlama
        return HomebrewLlama(machine.llama_dir, update)
    from ..adapters.llama.locator import LlamaLocator
    return LlamaLocator(machine.llama_dir, update, exe_suffix(profile))


def server_program(machine: Machine) -> str:
    """llama-server's program: its path in the machine's llama.cpp folder (CARL_LLAMA_DIR, or off the Mac the
    setting and the installed builds), else the bare name, which the OS looks up on PATH."""
    if machine.llama_dir:
        from ..adapters.gpu.inventory import llama_program
        return llama_program(LLAMA_SERVER, machine.llama_dir, program_ending()) or LLAMA_SERVER
    return LLAMA_SERVER


# ---------------------------------------------------------------- the GPU, for the choice of a build
@dataclass(frozen=True)
class GpuState:
    """What the GPU inventory reads, once: every device (with why a model leaves one out), the settings, the GPU the
    build is chosen for, the driver's CUDA version, and the inventory and the GPU limit themselves. others: the other
    GPUs a model uses (the build has to run them too)."""
    limit: object
    inventory: object
    listing: List[Device]
    settings: GpuSettings
    main: Optional[Gpu]
    cuda: Optional[Tuple[int, int]]
    others: Tuple[Gpu, ...] = ()


def gpu_state(profile: Profile, machine: Machine) -> GpuState:
    """The GPU inventory of a PC as `carl gpu` reads it (a Mac asks the Linux tools, which find none)."""
    from ..domain.hardware.devices import main_gpu
    os_name = profile.os if profile.os == "windows" else "linux"
    limit = gpu_wiring.limit(os_name, machine.llama_dir, machine.rocm, dict(machine.gpu_env), machine.conf_dir)
    inventory = limit.inventory                                   # type: ignore[attr-defined]
    listing = inventory.listing()
    used = [d.gpu for d in listing if not d.left_out]
    main = main_gpu(used) or main_gpu([d.gpu for d in listing])
    others = tuple(g for g in used if main is None or g.index != main.index)
    return GpuState(limit, inventory, listing, inventory.settings(), main, inventory.cuda_version(), others)


def build_choice(profile: Profile, machine: Machine, want: str = "auto") -> BuildChoice:
    """The llama.cpp build for this computer's GPU. want: the setting gpu.backend's values; auto: the setting."""
    g = gpu_state(profile, machine)
    return choose_build(platform.system(), g.main, g.cuda, machine.rocm, g.settings.backend if want == "auto" else want,
                        g.others)


# ---------------------------------------------------------------- the install
def record_path(env: MutableMapping[str, str], home: str, profile: Profile) -> Callable[[str], None]:
    """Writes the setting llama.path (config.json of the settings folder), as `config set llama.path` does."""
    def record(folder: str) -> None:
        from ..application.api import Api
        paths = wiring.CarlPaths.from_env(env, home)
        api = Api(wiring.build_carl(paths, env, profile.words), paths.config, paths.local)
        cfg = api.load_config()
        set_path(cfg, key_path("llama.path"), folder)
        api.save_config(cfg)
    return record


def install_service(env: MutableMapping[str, str], profile: Profile, machine: Machine, home: str,
                    out: Callable[[str], None]) -> LlamaInstall:
    """The install of the pinned build for this computer. out: a line for the user (the downloads, the unpacking)."""
    from ..adapters.common.downloader import Aria2OrCurl
    from ..adapters.llama.release_installer import GithubDigests, ReleaseInstaller, UrllibFetch
    pinned = release()
    fetch = Aria2OrCurl() if shutil.which("aria2c") or shutil.which("curl") else UrllibFetch(out)
    installer = ReleaseInstaller(pinned, profile.os, arch_of(platform.machine()),
                                 os.path.join(machine.conf_dir, SUBFOLDER), fetch, GithubDigests(pinned.api_url()), out)
    return LlamaInstall(pinned, installer, profile.os, arch_of(platform.machine()),
                        lambda want: build_choice(profile, machine, want), record_path(env, home, profile))


def offer(env: MutableMapping[str, str], profile: Profile, home: str) -> Optional[Dependencies]:
    """The first-run offer of Windows and Linux (None on a Mac: HomebrewDeps asks there). It looks for llama-server
    again at each call, with the environment and the settings of that moment, so the install it makes counts. It offers
    the install only when neither CARL_LLAMA_DIR nor llama.path is set: a named folder without a server is an error."""
    if profile.os == "macos":
        return None
    from ..adapters.llama.locator import SERVER, configured_path, find
    from ..adapters.llama.release_offer import ReleaseOffer
    from ..adapters.posix import start_tools

    def where() -> Machine:
        return wiring.machine(env, home, profile.os)

    def found() -> bool:
        m = where()
        return find(SERVER, m.llama_dir, exe_suffix(profile)) is not None

    def named() -> str:
        """The folder CARL_LLAMA_DIR or llama.path names (CARL_LLAMA_DIR wins), as the error says it; "" if neither."""
        var = str(LLAMA_KEYS["path"].env)
        if env.get(var):
            return f"{var} ({env[var]})"
        setting = configured_path(where().conf_dir)
        return f"the setting llama.path ({setting})" if setting else ""

    def service() -> LlamaInstall:
        return install_service(env, profile, where(), home, start_tools.out)

    return ReleaseOffer(found, lambda: service().plan().summary(), lambda: service().run().folder,
                        f"{profile.words.command} engine install", env, start_tools.out, start_tools.err,
                        start_tools.ask, named)


def starts_server(argv: Tuple[str, ...]) -> bool:
    """Whether a command line starts the server (the Windows dispatch runs the start in this process): no command,
    start, or a leading option. Help, the dashboard words and every other command do not."""
    word = argv[0] if argv else ""
    if any(a in ("-h", "--help") for a in argv):
        return False
    return word in ("", "start") or (word.startswith("-") and word != "--no-start")
