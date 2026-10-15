"""A computer that is not a Mac (Windows, Linux): its RAM, its GPU and the llama.cpp programs. The rules are in
domain/gpu.py; here are the tools that are asked. Every tool runs from an argument list; a tool that is missing
or fails gives no answer, never an error.
"""
from __future__ import annotations

import ctypes
import glob
import os
import platform
import shutil
import sys
from typing import List, Optional, Tuple

from ..domain import gpu as dg
from .system import run_text

NVIDIA_QUERY = ["--query-gpu=index,name,memory.total,memory.free,driver_version,compute_cap",
                "--format=csv,noheader,nounits"]
# Windows, any vendor: the display adapters' total VRAM (registry) and their dedicated use (performance counters)
DISPLAY_CLASS = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
PS_USAGE = ("(Get-Counter '\\GPU Adapter Memory(*)\\Dedicated Usage' -ErrorAction SilentlyContinue).CounterSamples"
            " | ForEach-Object { $_.CookedValue }")
EXE = ".exe" if sys.platform == "win32" else ""


def llama_program(name: str = "llama-server") -> Optional[str]:
    """The path of a llama.cpp program: in CARL_LLAMA_DIR (the folder of an unpacked llama.cpp release), else on
    PATH; None when neither has it."""
    folder = os.environ.get("CARL_LLAMA_DIR")
    if folder:
        p = os.path.join(os.path.expanduser(folder), name + EXE)
        return p if os.path.isfile(p) else None
    return shutil.which(name)


def ram_bytes() -> int:
    """Installed RAM (0 when unknown): Windows GlobalMemoryStatusEx, Linux /proc/meminfo."""
    if sys.platform == "win32":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        st = MemoryStatus()
        st.dwLength = ctypes.sizeof(MemoryStatus)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):  # type: ignore[attr-defined]
                return int(st.ullTotalPhys)
        except (AttributeError, OSError):
            return 0
        return 0
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def nvidia_gpus() -> Tuple[List[dg.Gpu], Optional[Tuple[int, int]]]:
    """NVIDIA GPUs (nvidia-smi) and the CUDA version their driver runs."""
    if not shutil.which("nvidia-smi"):
        return [], None
    return dg.parse_nvidia_smi(run_text(["nvidia-smi", *NVIDIA_QUERY])), dg.cuda_version(run_text(["nvidia-smi"]))


def sysfs_gpus() -> List[dg.Gpu]:
    """Linux, AMD (amdgpu): each card's VRAM from sysfs."""
    out: List[dg.Gpu] = []
    for total in sorted(glob.glob("/sys/class/drm/card*/device/mem_info_vram_total")):
        try:
            with open(total, encoding="utf-8") as t, open(total.replace("_total", "_used"), encoding="utf-8") as u:
                g = dg.parse_sysfs_vram(t.read(), u.read())
        except OSError:
            continue
        if g is not None:
            out.append(g)
    return out


def windows_gpus() -> List[dg.Gpu]:
    """Windows, any vendor: the registry's display adapters with their VRAM, and the in-use VRAM of the adapter
    that uses the most (the counters name an adapter by its LUID, not its registry key)."""
    try:
        import winreg
    except ImportError:
        return []
    found: List[Tuple[str, str, int]] = []
    try:
        cls = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, DISPLAY_CLASS)
    except OSError:
        return []
    for i in range(64):
        try:
            sub = winreg.EnumKey(cls, i)
        except OSError:
            break
        try:
            with winreg.OpenKey(cls, sub) as k:
                name = str(winreg.QueryValueEx(k, "DriverDesc")[0])
                pci = str(winreg.QueryValueEx(k, "MatchingDeviceId")[0])
                total = int(winreg.QueryValueEx(k, "HardwareInformation.qwMemorySize")[0])
        except (OSError, ValueError, TypeError):
            continue
        found.append((name, pci, total))
    if not found:
        return []
    used = [float(x) for x in run_text(["powershell", "-NoProfile", "-Command", PS_USAGE], 30).split()
            if x.replace(".", "", 1).isdigit()]
    top = max(used) if used else None
    big = max(found, key=lambda f: f[2])
    return [dg.windows_adapter(t, int(top) if top is not None and (n, p, t) == big else None, n, p)
            for n, p, t in found]


def listed_devices(binary: Optional[str] = None) -> List[dg.Gpu]:
    """The devices llama-server sees (its backend: CUDA, ROCm, Vulkan). Its free VRAM is right on Linux only."""
    exe = binary or llama_program()
    return dg.parse_list_devices(run_text([exe, "--list-devices"], 60)) if exe else []


class GpuReport:
    """What CARL found on this computer, asked once: the GPUs, the CUDA version of the driver, the GPU a start
    uses and how its free VRAM was found."""

    def __init__(self) -> None:
        self.cuda: Optional[Tuple[int, int]] = None
        self.gpus: List[dg.Gpu] = []
        self.source = ""
        nv, self.cuda = nvidia_gpus()
        for gpus, source in ((nv, "nvidia-smi"), (sysfs_gpus() if sys.platform.startswith("linux") else [], "sysfs"),
                             (windows_gpus() if sys.platform == "win32" else [], "the Windows GPU counters")):
            if gpus:
                self.gpus, self.source = gpus, source
                break
        if not self.gpus:
            self.gpus, self.source = listed_devices(), "llama-server --list-devices"
        self.main = dg.main_gpu(self.gpus)

    def build(self) -> dg.BuildChoice:
        return dg.choose_build(platform.system(), self.main, self.cuda, os.environ.get("CARL_ROCM") == "1")


class DeviceGpuLimit:
    """What a model can use on a computer with a discrete GPU (implements GpuLimit): the free VRAM less
    domain/gpu.py VRAM_KEEP. No GPU: the RAM (the CPU build runs the model from RAM; auto fit keeps its reserve).
    Asked once per process."""

    def __init__(self) -> None:
        self._value: Optional[Tuple[int, str]] = None
        self._gpu = False

    @property
    def unified(self) -> bool:
        """False with a GPU: its VRAM is apart from the RAM. No GPU: the model is in the RAM, as on a Mac."""
        self.limit()
        return not self._gpu

    def limit(self) -> Tuple[int, str]:
        if self._value is None:
            r = GpuReport()
            self._gpu = r.main is not None
            if r.main is None:
                self._value = (ram_bytes(), "no GPU found: the RAM")
            else:
                how = f"the free GPU memory of {r.main.name} ({r.source}) less {dg.VRAM_KEEP / dg.GIB:.2f} GiB"
                self._value = (dg.vram_limit(r.main), how)
        return self._value


class PcHost:
    """The RAM of a computer that is not a Mac (implements HostMemory). The VMware network check is a Mac's."""

    def ram_bytes(self) -> int:
        return ram_bytes()

    def vm_network_up(self) -> bool:
        return False
