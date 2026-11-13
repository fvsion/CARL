"""The platform profiles: for each kind of computer, the platform words and the adapters of its hardware.

  macos             a Mac: unified memory (the Metal limit), sysctl, the VM network
  windows, linux    a PC with a discrete GPU: its free VRAM, apart from the RAM
  shared_memory_pc  a PC whose GPU shares the RAM (no discrete GPU, an integrated one): unified, as a Mac
  cpu_only          a PC with no GPU: the model runs from the RAM

select() picks macos, windows or linux from sys.platform. A PC's profile becomes shared_memory_pc or cpu_only only
after its GPUs were asked (for_memory): the composition never asks the hardware at import or at selection. Each
profile builds its adapters only when asked, and imports the platform's adapters only then (rule R4).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Optional, Tuple

from ..domain.shared.platform_words import PlatformWords, for_os
from ..ports import CpuLayout, GpuLimit, HostMemory

UNIFIED, DISCRETE, CPU = "unified", "discrete", "cpu"


@dataclass(frozen=True)
class Machine:
    """What the hardware adapters are built from: the paths and the settings the composition read for them."""
    metal_probe: str = ""               # the Swift probe of the Metal limit (a Mac)
    metal_limit_cache: str = ""         # ~/models/.metal-limit
    llama_dir: Optional[str] = None     # the folder of the llama.cpp build: CARL_LLAMA_DIR, off the Mac also llama.path
                                        # and the newest build `carl engine install` made (wiring.machine)
    rocm: bool = False                  # CARL_ROCM=1: the ROCm build on AMD
    gpu_env: Tuple[Tuple[str, str], ...] = ()   # the GPU settings' and the visibility variables that are set (gpu.py)
    conf_dir: str = ""                  # the settings folder: config.json's gpu section, CARL's server files


def _mac_gpu(m: Machine) -> GpuLimit:
    from ..adapters.macos.sysctl_host import MacGpuLimit
    return MacGpuLimit(m.metal_probe, m.metal_limit_cache)


def _mac_host(m: Machine) -> HostMemory:
    from ..adapters.macos.sysctl_host import MacHost
    return MacHost()


def _pc_gpu(os_name: str) -> Callable[[Machine], GpuLimit]:
    """The GPU limit of a PC with this OS: its GPU inventory's sources (composition/gpu.py)."""
    def build(m: Machine) -> GpuLimit:
        from . import gpu
        return gpu.limit(os_name, m.llama_dir, m.rocm, dict(m.gpu_env), m.conf_dir)  # type: ignore[return-value]
    return build


def _pc_host(m: Machine) -> HostMemory:
    from ..adapters.gpu.inventory import PcHost
    from ..adapters.windows.host import free_bytes, ram_bytes
    return PcHost(free_bytes, ram_bytes)


def _linux_host(m: Machine) -> HostMemory:
    from ..adapters.linux.host import LinuxHost
    return LinuxHost()


def _windows_cpu() -> CpuLayout:
    from ..adapters.windows.cpu_layout import WindowsCpuLayout
    return WindowsCpuLayout()


def _linux_cpu() -> CpuLayout:
    from ..adapters.linux.cpu_layout import LinuxCpuLayout
    return LinuxCpuLayout()


@dataclass(frozen=True)
class Profile:
    """One kind of computer. os: macos, windows or linux (its words); memory: unified, discrete or cpu (what a
    model may use: the GPU limit, the free VRAM, or the RAM)."""
    name: str
    os: str
    memory: str
    words: PlatformWords
    gpu: Callable[[Machine], GpuLimit]
    host: Callable[[Machine], HostMemory]
    cpu: Optional[Callable[[], CpuLayout]] = None   # the CPU's cores (a PC; a Mac has no experts apart from its GPU)

    def with_command(self, command: str) -> Profile:
        """The same profile whose texts use another command word (CARL_CMD)."""
        return replace(self, words=self.words.with_command(command))


MACOS = Profile("macos", "macos", UNIFIED, for_os("macos"), _mac_gpu, _mac_host)
WINDOWS = Profile("windows", "windows", DISCRETE, for_os("windows"), _pc_gpu("windows"), _pc_host, _windows_cpu)
LINUX = Profile("linux", "linux", DISCRETE, for_os("linux"), _pc_gpu("linux"), _linux_host, _linux_cpu)
# A PC's variants: the words and the adapters of its OS (for_memory fills them in), which find the memory.
SHARED_MEMORY_PC = Profile("shared_memory_pc", "linux", UNIFIED, for_os("linux"), _pc_gpu("linux"), _linux_host,
                           _linux_cpu)
CPU_ONLY = Profile("cpu_only", "linux", CPU, for_os("linux"), _pc_gpu("linux"), _linux_host, _linux_cpu)
PROFILES = {p.name: p for p in (MACOS, WINDOWS, LINUX, SHARED_MEMORY_PC, CPU_ONLY)}


def select(platform: str) -> Profile:
    """The profile of sys.platform: darwin is a Mac, win32 Windows, anything else Linux."""
    if platform == "darwin":
        return MACOS
    if platform == "win32":
        return WINDOWS
    return LINUX


def for_memory(base: Profile, gpu_found: bool, unified: bool) -> Profile:
    """A PC's profile once its GPUs are known: no GPU is cpu_only, a GPU that shares the RAM shared_memory_pc, a
    discrete GPU the OS profile itself. A Mac stays macos."""
    if base.os == "macos" or base.name not in ("windows", "linux"):
        return base
    if not gpu_found:
        kind = CPU_ONLY
    elif unified:
        kind = SHARED_MEMORY_PC
    else:
        return base
    return replace(kind, os=base.os, words=base.words, gpu=base.gpu, host=base.host, cpu=base.cpu)
