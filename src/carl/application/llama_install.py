"""Installing the pinned llama.cpp build (W6.2; Windows and Linux): which build this computer gets, the install and
the record of llama.path. The build is the GPU's (domain/llama/build.py choose_build, asked through `choose`), looked
up in the pinned release (domain/llama/release.py); a build the release lacks falls back to the one it has with a sentence,
or the plan says to point llama.path at a build of one's own. The install itself is the LlamaReleaseInstaller port's.
Contract C-127.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List

from ..domain.llama import release as rel
from ..domain.llama.build import BACKENDS, BuildChoice
from ..domain.llama.release import Build, Release
from ..domain.shared.contracts import ensure, require
from ..domain.shared.errors import ConfigError
from ..domain.shared.units import file_size
from ..ports import LlamaReleaseInstaller

CHOICES = tuple(b for b in BACKENDS if b != "auto") + rel.BUILDS      # what --backend takes, after auto


@dataclass(frozen=True)
class InstallPlan:
    """What an install would do: the release, the build (backend) and why that one, the sentence about a fallback
    ("" if none) and the files it downloads."""
    tag: str
    version: str
    backend: str
    why: str
    note: str
    build: Build

    def summary(self) -> str:
        """llama.cpp b11738 (cuda-13, 552 MB)"""
        return f"llama.cpp {self.tag} ({self.backend}, {file_size(self.build.size)})"


@dataclass(frozen=True)
class Installed:
    """The result: the folder of the build, and the plan it followed."""
    folder: str
    plan: InstallPlan


class LlamaInstall:
    """release: the pinned release; os_name, arch: windows or linux, x64 or arm64; choose: the build for a wanted
    backend (auto, cuda, cpu) from this computer's GPU; record: writes the setting llama.path."""

    def __init__(self, release: Release, installer: LlamaReleaseInstaller, os_name: str, arch: str,
                 choose: Callable[[str], BuildChoice], record: Callable[[str], None]) -> None:
        self.release, self.installer, self.os, self.arch = release, installer, os_name, arch
        self.choose, self.record = choose, record

    def installed(self) -> List[str]:
        """The builds in the settings folder, newest first."""
        return self.installer.installed()

    def plan(self, want: str = "") -> InstallPlan:
        """The build for a wanted backend ("" or auto: the one for this computer; cuda-13 and cuda-12 name a build).
        ConfigError for a backend that does not exist, a computer the release has no builds for, or a release with
        nothing for it (then its sentence says to point llama.path at one's own build)."""
        want = want or "auto"
        if want not in CHOICES + ("auto",):
            raise ConfigError(f"unknown build '{want}'. Use one of: auto, {', '.join(CHOICES)}.")
        if self.os not in rel.OSES or self.arch not in rel.ARCHES:
            raise ConfigError("CARL installs llama.cpp release builds for Windows and Linux on x64 and arm64 only. "
                              "Take a build of your own and set llama.path to its folder.")
        choice = BuildChoice(want, "your choice") if want in rel.BUILDS else self.choose(want)
        if choice.backend == "cuda":                          # no NVIDIA GPU found, but CUDA asked for: the newest
            choice = BuildChoice("cuda-13", f"{choice.why}; CARL found no NVIDIA GPU")
        found = rel.resolve(self.release, self.os, self.arch, choice.backend)
        if found.build is None:
            raise ConfigError(found.note)
        return InstallPlan(self.release.tag, self.release.version, found.backend, choice.why, found.note, found.build)

    def install(self, plan: InstallPlan) -> Installed:
        """Install a planned build and record llama.path (the older builds stay). ConfigError when the download, a
        check or the unpacking fails; nothing is recorded then."""
        require(bool(plan.build.files), "C-127", "the plan has files to install")
        folder = self.installer.install(plan.backend)
        ensure(bool(folder), "C-127", "the install gives the folder of the build")
        self.record(folder)
        return Installed(folder, plan)

    def run(self, want: str = "") -> Installed:
        """plan() and install() for a wanted backend."""
        return self.install(self.plan(want))
