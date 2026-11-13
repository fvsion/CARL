"""What CARL tells a plugin of this computer (ports/plugin.py PluginComputer, contract C-066): the GPU (its API, the room
a model may use there now, the CUDA of the llama.cpp build CARL uses), the llama.cpp programs a plugin runs (found in
the llama.cpp CARL uses or on PATH, or fetched: Homebrew's llama.cpp on a Mac, the release CARL pins on a PC) and the
folder of the language models. Each is read when a hook asks, never when the registry is built.
"""
from __future__ import annotations

import os
import shutil
from typing import Callable, Mapping, Optional, Tuple

from ..domain.llama.build import build_cuda
from ..ports.plugin import GpuRoom, ProgramFetch
from . import wiring
from .profiles import Machine, Profile

LLAMA_EMBEDDING = "llama-embedding"                      # what Homebrew's llama.cpp has ...
LLAMA_SERVER = "llama-server"
BREW_PROGRAMS = (LLAMA_EMBEDDING, LLAMA_SERVER)
RELEASE_PROGRAMS = (LLAMA_SERVER,)                       # ... and the pinned release: no llama-embedding
#                                                          (checked on b11429 and b11738)


class LlamaPrograms:
    """The llama.cpp programs of this computer: found in the llama.cpp CARL uses (CARL_LLAMA_DIR, llama.path, the
    newest build `carl engine install` made) or on PATH. A name may list alternatives joined by "|"
    ("llama-embedding|llama-server"): found is the first of them this computer has. A Mac gets them from Homebrew's
    llama.cpp (both programs), a PC from the pinned release, which has llama-server and no llama-embedding: the program
    it fetches is the first alternative that the source has. machine: asked again at each call (an install changes it).
    brew: runs `brew install llama.cpp`; engine: `carl engine install` (the new folder); look: the search of a program
    in the folder (else PATH), the adapters' llama_program."""

    def __init__(self, profile: Profile, machine: Callable[[], Machine], brew: Callable[[], Tuple[int, str]],
                 engine: Callable[[Callable[[str], None]], str],
                 look: Optional[Callable[[str, Optional[str], str], Optional[str]]] = None) -> None:
        from ..adapters.gpu.inventory import llama_program
        self.profile, self.machine, self._brew, self._engine = profile, machine, brew, engine
        self._look = look or llama_program

    def find(self, name: str) -> Optional[str]:
        from .llama import program_ending
        for one in name.split("|"):
            got = self._look(one, self.machine().llama_dir, program_ending())
            if got:
                return got
        return None

    def _source(self, name: str) -> str:
        """The alternative of name that this computer's source (Homebrew, the release) has; the first when none is."""
        names = name.split("|")
        has = BREW_PROGRAMS if self.profile.os == "macos" else RELEASE_PROGRAMS
        return next((n for n in names if n in has), names[0])

    def how(self, name: str) -> str:
        one = self._source(name)
        if self.profile.os == "macos":
            return f"fetch {one} with llama.cpp from Homebrew (brew install llama.cpp), from the network"
        return (f"fetch {one} with the llama.cpp release CARL pins (the same as `{self.profile.words.command} engine "
                f"install`), from GitHub")

    def fetch(self, name: str, say: Callable[[str], None]) -> ProgramFetch:
        found = self.find(name)
        if found:
            return ProgramFetch(True, f"{name.replace('|', ' or ')} is at {found}")
        one = self._source(name)
        if self.profile.os == "macos":
            say(self.how(name))
            code, out = self._brew()
            got = self.find(name)
            if got:
                return ProgramFetch(True, f"fetched {one} (llama.cpp from Homebrew): {got}", True)
            return ProgramFetch(False, f"{one} is not in your llama.cpp (brew exit {code}: {out.strip()[-200:]}); "
                                       f"update it with: {self.profile.words.update_hint()}")
        folder = self.machine().llama_dir
        if folder:
            return ProgramFetch(False, f"{one} is not in {folder}: that build has no {one}. Point llama.path at a "
                                       f"full llama.cpp release, or run `{self.profile.words.command} engine install`")
        say(self.how(name))
        where = self._engine(say)
        got = self.find(name)
        if got:
            return ProgramFetch(True, f"fetched {one} (the llama.cpp release in {where}): {got}", True)
        return ProgramFetch(False, f"the llama.cpp release in {where} has no {one}")


def llama_programs(env: Mapping[str, str], profile: Profile, home: Optional[str] = None) -> LlamaPrograms:
    """The llama.cpp programs of this computer, and how to fetch them."""
    from ..adapters.common.program import run_program
    from . import llama as llama_wiring
    home = home or os.path.expanduser("~")

    def machine() -> Machine:
        return wiring.machine(env, home, profile.os)

    def brew() -> Tuple[int, str]:
        exe = shutil.which("brew")
        if not exe:
            return 127, "Homebrew (brew) is not installed"
        return run_program([exe, "install", "llama.cpp"], dict(env))

    def engine(say: Callable[[str], None]) -> str:
        service = llama_wiring.install_service(dict(env), profile, machine(), home, say)    # type: ignore[arg-type]
        return service.run().folder
    return LlamaPrograms(profile, machine, brew, engine)


def cuda_major(machine: Machine, gpu: object) -> int:
    """The CUDA major version of the llama.cpp build CARL uses (its folder's name, else its CUDA runtime files), else
    the driver's. 0 when none tells."""
    folder = machine.llama_dir or ""
    try:
        files = os.listdir(folder) if folder else []
    except OSError:
        files = []
    try:
        driver = gpu.inventory.cuda_version()                # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001  (a driver that cannot be read: the build alone tells)
        driver = None
    return build_cuda(os.path.basename(folder.rstrip("/\\")), files, driver)


def gpu_room(profile: Profile, machine: Machine) -> GpuRoom:
    """The GPU (A65: a plugin's auto is the GPU when there is a usable one): metal on a Mac, with the room the GPU
    limit and the free memory leave; cuda on an NVIDIA GPU, else other, with the room of the best device. GpuRoom()
    when there is no GPU or it cannot be read."""
    try:
        gpu = wiring.gpu(profile, machine)
        if profile.os == "macos":
            room = gpu.limit()[0]
            free = wiring.host(profile, machine).free_bytes()
            return GpuRoom("metal", min(room, free) if free else room)
        slots = gpu.slots()
        if not slots:
            return GpuRoom()
        best = max(slots, key=lambda x: x.room)
        api = "cuda" if best.name.upper().startswith("CUDA") else "other"
        return GpuRoom(api, max(best.room, 0), cuda_major(machine, gpu) if api == "cuda" else 0)
    except Exception:  # noqa: BLE001  (a GPU that cannot be read is no GPU for a plugin)
        return GpuRoom()


class Computer:
    """PluginComputer (C-066) of one computer. models: the folder of the language models (the models folder setting),
    asked each time. programs: the llama.cpp programs (default: llama_programs, made on first use)."""

    def __init__(self, env: Mapping[str, str], profile: Profile, models: Callable[[], str],
                 home: Optional[str] = None, programs: Optional[LlamaPrograms] = None) -> None:
        self.env, self.profile, self._models, self.home = dict(env), profile, models, home
        self._programs = programs

    def programs(self) -> LlamaPrograms:
        if self._programs is None:
            self._programs = llama_programs(self.env, self.profile, self.home)
        return self._programs

    def gpu(self) -> GpuRoom:
        return gpu_room(self.profile, wiring.machine(self.env, self.home, self.profile.os))

    def find_program(self, name: str) -> Optional[str]:
        return self.programs().find(name)

    def fetch_how(self, name: str) -> str:
        return self.programs().how(name)

    def fetch_program(self, name: str, say: Callable[[str], None]) -> ProgramFetch:
        return self.programs().fetch(name, say)

    def models_dir(self) -> str:
        return self._models()
