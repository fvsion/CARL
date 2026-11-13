"""Which llama.cpp build runs this computer's GPU (the backend), and the files of a llama.cpp release that make it.
Pure: the GPU comes from domain/hardware/devices.py, the CUDA version from the GPU inventory (domain/hardware/gpu.py).
CUDA versions compare as numbers: 13.10 is above 13.4.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from ..hardware.devices import Gpu, is_integrated

# Where llama.cpp's Windows CUDA 13 builds start: compute capability 7.5 (Turing). Their log says
# "CUDA : ARCHS = 750,800,860,890,900,1200,1210". Older cards (Pascal 6.x, Volta 7.0) need the CUDA 12 build.
CUDA13_MIN_CC = (7, 5)

Backend = str          # cuda-13, cuda-12, rocm, vulkan, cpu, metal
Version = Tuple[int, int]
# The values of the setting gpu.backend (domain/config/settings.py): auto lets CARL choose.
BACKENDS = ("auto", "cuda", "vulkan", "rocm", "cpu")


@dataclass(frozen=True)
class BuildChoice:
    """The llama.cpp build for this computer and why, in words."""
    backend: Backend
    why: str


_CUDART = re.compile(r"(?:^cudart64_(\d+)\.dll$|^libcudart\.so\.(\d+)(?:\.|$))")
_BUILD_CUDA = re.compile(r"^b\d+-cuda-(\d+)")


def build_cuda(build_folder: str, files: Sequence[str] = (), driver: Optional[Version] = None) -> int:
    """The CUDA major version that matches the llama.cpp CARL uses: the one in its folder's name (b11738-cuda-13), else
    the one of the CUDA runtime in its files (cudart64_13.dll), else the driver's CUDA (13 from 13.0, else 12). 0 when
    none of them tells."""
    m = _BUILD_CUDA.match(build_folder)
    if m:
        return int(m.group(1))
    for f in files:
        m = _CUDART.match(f)
        if m:
            return int(m.group(1) or m.group(2))
    if driver is not None:
        return 13 if driver[0] >= 13 else 12
    return 0


def version(text: str) -> Optional[Version]:
    """A version as numbers: "13.10" is (13, 10), above "13.4" (13, 4); None when it is not one."""
    m = re.match(r"\s*(\d+)(?:\.(\d+))?", text or "")
    return (int(m.group(1)), int(m.group(2) or 0)) if m else None


def _cuda(gpu: Gpu, cuda: Optional[Version]) -> BuildChoice:
    """NVIDIA: CUDA 13 from compute capability 7.5 with a driver for CUDA 13, else CUDA 12, else Vulkan."""
    cc = gpu.compute_cap
    if cc is not None and cc < (5, 0):
        return BuildChoice("vulkan", f"{gpu.name} is too old for llama.cpp's CUDA builds")
    if (cc is None or cc >= CUDA13_MIN_CC) and cuda is not None and cuda >= (13, 0):
        return BuildChoice("cuda-13", f"{gpu.name} with a driver for CUDA {cuda[0]}.{cuda[1]}")
    if cuda is None or cuda >= (12, 4):
        why = "compute capability below 7.5" if cc is not None and cc < CUDA13_MIN_CC else "a driver for CUDA 12"
        return BuildChoice("cuda-12", f"{gpu.name}: {why}")
    return BuildChoice("vulkan", f"the driver of {gpu.name} is older than CUDA 12.4: update it for CUDA")


def choose_build(system: str, gpu: Optional[Gpu], cuda: Optional[Version] = None,
                 prefer_rocm: bool = False, want: str = "auto", others: Sequence[Gpu] = ()) -> BuildChoice:
    """The llama.cpp release build to run. system: platform.system() (Darwin, Windows, Linux).
    NVIDIA: CUDA 13 from compute capability 7.5 with a driver for CUDA 13, else CUDA 12, else Vulkan.
    AMD: Vulkan (every Radeon of the last 8 years; no ROCm install), ROCm only when asked (prefer_rocm): the ROCm
    build runs only the Radeon models that ROCm supports. Intel and others: Vulkan. No GPU: the CPU build.
    A GPU that shares the RAM (an AMD APU: Ryzen AI, Strix Halo) runs on Vulkan too. others: the other GPUs a model
    uses: with a GPU of another maker among them (an NVIDIA card beside an AMD card or an APU) one build must run them
    all, Vulkan (W6.6).
    want: the setting gpu.backend; anything but auto is the user's choice (cuda still picks 13 or 12)."""
    if system == "Darwin":
        return BuildChoice("metal", "a Mac: Homebrew's llama.cpp (Metal)")
    if want == "cpu" or (gpu is None and want == "auto"):
        return BuildChoice("cpu", "your setting gpu.backend" if want == "cpu" else
                           "CARL found no GPU: the CPU build (slow)")
    if gpu is None:
        return BuildChoice(want, "your setting gpu.backend")
    if want == "auto" and any(o.vendor != gpu.vendor for o in others):
        names = " and ".join(dict.fromkeys(g.name for g in (gpu, *others)))
        return BuildChoice("vulkan", f"{names}: GPUs of different makers run on the Vulkan build")
    if want == "vulkan":
        return BuildChoice("vulkan", f"{gpu.name}: Vulkan, your setting gpu.backend")
    if want == "rocm" or (want == "auto" and gpu.vendor == "amd" and prefer_rocm):
        return BuildChoice("rocm", f"{gpu.name}: ROCm (HIP), as you set")
    if want == "cuda" or gpu.vendor == "nvidia":
        return _cuda(gpu, cuda)
    if gpu.vendor == "amd":
        if is_integrated(gpu):
            return BuildChoice("vulkan", f"{gpu.name}: Vulkan (it shares the RAM, as the APUs of Ryzen AI do)")
        return BuildChoice("vulkan", f"{gpu.name}: Vulkan (ROCm is an option for the Radeon models that it supports)")
    return BuildChoice("vulkan", f"{gpu.name}: Vulkan")


_ASSET_KIND = {"cuda-13": r"cuda-13[.\d]*", "cuda-12": r"cuda-12[.\d]*", "rocm": r"(?:rocm-[.\d]+|hip-radeon)",
               "vulkan": r"vulkan", "cpu": r"cpu"}


def _newest(found: Sequence[str], pattern: "re.Pattern[str]", cuda: Optional[Version]) -> str:
    """The build to take when a release has several of one kind (cuda-13.4 and cuda-13.10): by their versions as
    numbers, the newest that the driver's CUDA runs (cuda), else the oldest; the newest when cuda is not known."""
    def ver(name: str) -> Tuple[int, int]:
        m = pattern.match(name)
        v = version(re.sub(r"^\D+", "", m.group(1))) if m and m.groups() else None
        return v or (0, 0)
    ranked = sorted(found, key=lambda n: (ver(n), n))
    if cuda is not None:
        runs = [n for n in ranked if ver(n) <= cuda]
        return runs[-1] if runs else ranked[0]
    return ranked[-1]


def release_assets(names: Sequence[str], system: str, machine: str, backend: Backend,
                   cuda: Optional[Version] = None) -> List[str]:
    """The files of a llama.cpp release (its asset names) that make the build: the main zip (Windows) or tarball
    (Linux, "ubuntu"), and for CUDA its runtime ("cudart-...") of the same CUDA version. [] when the release has
    no such build. machine: platform.machine() (AMD64, x86_64, ARM64, aarch64); cuda: the driver's CUDA version."""
    kind = _ASSET_KIND.get(backend)
    if kind is None:
        return []
    arch = "arm64" if machine.lower() in ("arm64", "aarch64") else "x64"
    osname, ext = ("win", r"\.zip") if system == "Windows" else ("ubuntu", r"\.tar\.gz") if system == "Linux" else ("", "")
    if not osname:
        return []
    if osname == "ubuntu" and backend == "cpu":
        main = re.compile(rf"^llama-b\d+-bin-ubuntu-{arch}{ext}$")
    else:
        main = re.compile(rf"^llama-b\d+-bin-{osname}-({kind})-{arch}{ext}$")
    found = [n for n in names if main.match(n)]
    if not found:
        return []
    pick = _newest(found, main, cuda if backend.startswith("cuda") else None)
    out = [pick]
    if backend.startswith("cuda"):
        m = main.match(pick)
        ver = m.group(1) if m and m.groups() else ""
        rt = [n for n in names if n.startswith("cudart-") and f"-{ver}-{arch}" in n and re.search(ext + "$", n)
              and (f"-{osname}-" in n)]
        out += sorted(rt)[-1:]
    return out
