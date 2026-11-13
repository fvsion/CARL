"""The NVIDIA GPUs from nvidia-smi (Windows and Linux): each card's total and free VRAM, its compute capability, PCI
address and UUID, the CUDA version of the driver, and the VRAM a process holds (Linux only: on Windows, WDDM, the
driver says [N/A] for every process; the Windows counters tell it there).
"""
from __future__ import annotations

import shutil
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ...domain.hardware.devices import Gpu
from . import parsers
from .tool import run_text

CSV = "--format=csv,noheader,nounits"
QUERY = ["--query-gpu=index,name,memory.total,memory.free,driver_version,compute_cap,pci.bus_id,uuid", CSV]
QUERY_OLD = ["--query-gpu=index,name,memory.total,memory.free,driver_version", CSV]
APPS = ["--query-compute-apps=pid,used_memory,gpu_bus_id", CSV]


class NvidiaSmi:
    """One source of the GPU inventory. run: how a command runs (tests give made-up output)."""
    name = "nvidia-smi"

    def __init__(self, run: Callable[[Sequence[str]], str] = run_text, exe: str = "nvidia-smi") -> None:
        self.run = run
        self.exe = exe

    def _ok(self) -> bool:
        return self.run is not run_text or bool(shutil.which(self.exe))

    def gpus(self) -> List[Gpu]:
        if not self._ok():
            return []
        # a driver older than compute_cap's field (2022) refuses the whole query: then without the last three
        return (parsers.parse_nvidia_smi(self.run([self.exe, *QUERY]))
                or parsers.parse_nvidia_smi(self.run([self.exe, *QUERY_OLD])))

    def cuda(self) -> Optional[Tuple[int, int]]:
        """The newest CUDA the driver runs, from its own line (None when it prints none)."""
        return parsers.cuda_version(self.run([self.exe])) if self._ok() else None

    def process_vram(self, pid: int) -> Dict[str, int]:
        """The bytes the process holds on each GPU, by PCI address ({} when unknown)."""
        return parsers.parse_compute_apps(self.run([self.exe, *APPS])).get(pid, {}) if self._ok() else {}
