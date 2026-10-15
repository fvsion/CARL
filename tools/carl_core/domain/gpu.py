"""A discrete GPU (NVIDIA, AMD, Intel) on Windows and Linux: what the GPU tools say, which llama.cpp build
runs it, and the memory a model can use on it. Pure: the adapters (adapters/gpu_probe.py) run the tools.

On a Mac the GPU and the CPU share one memory (unified memory): the limit is what macOS lets the GPU use
(adapters/system.py MacGpuLimit). A discrete GPU has its own memory (VRAM), apart from the RAM:
  limit = the free VRAM when CARL looks - VRAM_KEEP
Free, not total: the desktop and other programs use VRAM too (a Windows desktop: 1.9 GiB of 8 GiB, measured).
Which tool tells the free VRAM (measured on Windows 11, an RTX 2070 SUPER, 2026-10-14):
  - nvidia-smi (NVIDIA, Windows and Linux): total, used and free of the whole GPU: correct.
  - llama-server --list-devices (every backend): the total is correct, but on Windows the "free" of the CUDA and
    Vulkan backends leaves out the other programs (7131 MiB free while nvidia-smi said 4364): use it only for the
    total, or on Linux.
  - Windows, any vendor: the registry's HardwareInformation.qwMemorySize (the total) and the performance counter
    "GPU Adapter Memory\\Dedicated Usage" (in use; nvidia-smi said 0.45 GiB more: the driver's own part).
  - Linux, AMD: /sys/class/drm/cardN/device/mem_info_vram_total and mem_info_vram_used (bytes).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

MIB = 2 ** 20
GIB = 2 ** 30
# VRAM kept free on a discrete GPU: the backend's own context (CUDA: 0.1 GiB more than llama.cpp's buffers,
# measured; Vulkan: less), and room for the desktop's VRAM to grow while the server runs. llama.cpp's own
# --fit keeps 1 GiB (--fit-target).
VRAM_KEEP = 768 * MIB
# Where llama.cpp's Windows CUDA 13 builds start: compute capability 7.5 (Turing). Their log says
# "CUDA : ARCHS = 750,800,860,890,900,1200,1210". Older cards (Pascal 6.x, Volta 7.0) need the CUDA 12 build.
CUDA13_MIN_CC = (7, 5)
VENDORS = {"10de": "nvidia", "1002": "amd", "8086": "intel"}

# llama-server --list-devices: "  CUDA0: NVIDIA GeForce RTX 2070 SUPER (8191 MiB, 7131 MiB free)"
_DEVICE = re.compile(r"^\s*([A-Za-z_]+?)(\d+)\s*:\s*(.+?)\s*\((\d+)\s*MiB,\s*(\d+)\s*MiB free\)\s*$")


@dataclass(frozen=True)
class Gpu:
    """One GPU. backend: the llama.cpp device kind (CUDA, ROCm, Vulkan, MTL...) or the tool that saw it
    (nvidia-smi, sysfs, registry); free None: not known."""
    name: str
    total: int
    free: Optional[int] = None
    vendor: str = ""
    backend: str = ""
    index: int = 0
    compute_cap: Optional[Tuple[int, int]] = None
    driver: str = ""

    def label(self) -> str:
        """NVIDIA GeForce RTX 2070 SUPER, 8.0 GiB"""
        return f"{self.name}, {self.total / GIB:.1f} GiB"


def vendor_of(text: str) -> str:
    """nvidia, amd, intel or "" from a GPU name or a PCI ID (VEN_10DE, 0x1002)."""
    t = text.lower()
    m = re.search(r"(?:ven_|0x)([0-9a-f]{4})", t)
    if m and m.group(1) in VENDORS:
        return VENDORS[m.group(1)]
    if re.search(r"nvidia|geforce|quadro|tesla|rtx", t):
        return "nvidia"
    if re.search(r"\bamd\b|radeon|instinct|\bati\b", t):
        return "amd"
    if re.search(r"intel|\barc\b|iris", t):
        return "intel"
    return ""


def parse_list_devices(text: str) -> List[Gpu]:
    """The devices of `llama-server --list-devices` (none: [])."""
    out: List[Gpu] = []
    for line in text.splitlines():
        m = _DEVICE.match(line)
        if m:
            out.append(Gpu(name=m.group(3), total=int(m.group(4)) * MIB, free=int(m.group(5)) * MIB,
                           vendor=vendor_of(m.group(3)), backend=m.group(1), index=int(m.group(2))))
    return out


def parse_nvidia_smi(text: str) -> List[Gpu]:
    """`nvidia-smi --query-gpu=index,name,memory.total,memory.free,driver_version,compute_cap
    --format=csv,noheader,nounits` (MiB)."""
    out: List[Gpu] = []
    for line in text.splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) < 4 or not (f[0].isdigit() and f[2].isdigit()):
            continue
        cc = None
        if len(f) > 5 and re.fullmatch(r"\d+\.\d+", f[5]):
            major, minor = f[5].split(".")
            cc = (int(major), int(minor))
        out.append(Gpu(name=f[1], total=int(f[2]) * MIB, free=int(f[3]) * MIB if f[3].isdigit() else None,
                       vendor="nvidia", backend="nvidia-smi", index=int(f[0]), compute_cap=cc,
                       driver=f[4] if len(f) > 4 else ""))
    return out


def cuda_version(nvidia_smi_text: str) -> Optional[Tuple[int, int]]:
    """The newest CUDA the driver runs, from plain `nvidia-smi` ("CUDA Version: 13.4", or since the 610 drivers
    "CUDA UMD Version: 13.4")."""
    m = re.search(r"CUDA (?:UMD )?Version:\s*(\d+)\.(\d+)", nvidia_smi_text)
    return (int(m.group(1)), int(m.group(2))) if m else None


def parse_sysfs_vram(total: str, used: str, name: str = "AMD GPU") -> Optional[Gpu]:
    """An amdgpu card from /sys/class/drm/cardN/device/mem_info_vram_total and mem_info_vram_used (bytes)."""
    try:
        t, u = int(total.strip()), int(used.strip())
    except ValueError:
        return None
    return Gpu(name=name, total=t, free=max(t - u, 0), vendor="amd", backend="sysfs") if t > 0 else None


def windows_adapter(total: int, used: Optional[int], name: str, pci_id: str) -> Gpu:
    """A Windows display adapter: its registry total and the performance counter's dedicated use (bytes)."""
    return Gpu(name=name, total=total, free=max(total - used, 0) if used is not None else None,
               vendor=vendor_of(pci_id) or vendor_of(name), backend="registry")


def main_gpu(gpus: Sequence[Gpu]) -> Optional[Gpu]:
    """The GPU a start uses: the one with the most VRAM (llama.cpp uses every GPU it sees; CARL plans for one)."""
    real = [g for g in gpus if g.total > 0]
    return max(real, key=lambda g: (g.total, -g.index)) if real else None


def vram_limit(gpu: Gpu, keep: int = VRAM_KEEP) -> int:
    """What a model can use on a discrete GPU: the free VRAM (the total when the free is not known) less keep."""
    free = gpu.free if gpu.free is not None else gpu.total
    return max(free - keep, 0)


# ---------------------------------------------------------------- which llama.cpp build
Backend = str          # cuda-13, cuda-12, rocm, vulkan, cpu, metal


@dataclass(frozen=True)
class BuildChoice:
    """The llama.cpp build for this computer and why, in words."""
    backend: Backend
    why: str


def choose_build(system: str, gpu: Optional[Gpu], cuda: Optional[Tuple[int, int]] = None,
                 prefer_rocm: bool = False) -> BuildChoice:
    """The llama.cpp release build to run. system: platform.system() (Darwin, Windows, Linux).
    NVIDIA: CUDA 13 from compute capability 7.5 with a driver for CUDA 13, else CUDA 12, else Vulkan.
    AMD: Vulkan (every Radeon of the last 8 years; no ROCm install), ROCm only when asked (prefer_rocm): the ROCm
    build runs only the Radeon models that ROCm supports. Intel and others: Vulkan. No GPU: the CPU build."""
    if system == "Darwin":
        return BuildChoice("metal", "a Mac: Homebrew's llama.cpp (Metal)")
    if gpu is None:
        return BuildChoice("cpu", "CARL found no GPU: the CPU build (slow)")
    if gpu.vendor == "nvidia":
        cc = gpu.compute_cap
        if cc is not None and cc < (5, 0):
            return BuildChoice("vulkan", f"{gpu.name} is too old for llama.cpp's CUDA builds")
        if (cc is None or cc >= CUDA13_MIN_CC) and cuda is not None and cuda >= (13, 0):
            return BuildChoice("cuda-13", f"{gpu.name} with a driver for CUDA {cuda[0]}.{cuda[1]}")
        if cuda is None or cuda >= (12, 4):
            why = "compute capability below 7.5" if cc is not None and cc < CUDA13_MIN_CC else "a driver for CUDA 12"
            return BuildChoice("cuda-12", f"{gpu.name}: {why}")
        return BuildChoice("vulkan", f"the driver of {gpu.name} is older than CUDA 12.4: update it for CUDA")
    if gpu.vendor == "amd":
        if prefer_rocm:
            return BuildChoice("rocm", f"{gpu.name}: ROCm (HIP), as you set")
        return BuildChoice("vulkan", f"{gpu.name}: Vulkan (ROCm is an option for the Radeon models that it supports)")
    return BuildChoice("vulkan", f"{gpu.name}: Vulkan")


_ASSET_KIND = {"cuda-13": r"cuda-13[.\d]*", "cuda-12": r"cuda-12[.\d]*", "rocm": r"(?:rocm-[.\d]+|hip-radeon)",
               "vulkan": r"vulkan", "cpu": r"cpu"}


def release_assets(names: Sequence[str], system: str, machine: str, backend: Backend) -> List[str]:
    """The files of a llama.cpp release (its asset names) that make the build: the main zip (Windows) or tarball
    (Linux, "ubuntu"), and for CUDA its runtime ("cudart-...") of the same CUDA version. [] when the release has
    no such build. machine: platform.machine() (AMD64, x86_64, ARM64, aarch64)."""
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
    pick = sorted(found)[-1]                        # the newest CUDA 13.x when a release has two
    out = [pick]
    if backend.startswith("cuda"):
        m = main.match(pick)
        ver = m.group(1) if m and m.groups() else ""
        rt = [n for n in names if n.startswith("cudart-") and f"-{ver}-{arch}" in n and re.search(ext + "$", n)
              and (f"-{osname}-" in n)]
        out += sorted(rt)[-1:]
    return out
