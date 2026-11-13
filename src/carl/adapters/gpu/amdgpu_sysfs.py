"""The AMD GPUs of a Linux computer from the amdgpu driver's sysfs files: each card's VRAM (total and used, bytes; of
an APU the BIOS carve-out, with mem_info_gtt_total the system memory it maps besides), its PCI address (the device's
uevent) and name; and the VRAM a process holds, from its DRM file descriptors
(/proc/PID/fdinfo: drm-pdev, drm-client-id, drm-memory-vram). Files that are missing give nothing, never an error.
"""
from __future__ import annotations

import glob
import os
from typing import Dict, List, Set

from ...domain.hardware.devices import GENERIC_AMD, Gpu
from . import parsers

DRM = "/sys/class/drm"
PROC = "/proc"


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _slot(dev: str) -> str:
    """The card's PCI address: its uevent's PCI_SLOT_NAME, else the name its device link points to."""
    for line in _read(os.path.join(dev, "uevent")).splitlines():
        if line.startswith("PCI_SLOT_NAME="):
            return line.split("=", 1)[1].strip()
    return os.path.basename(os.path.realpath(dev))


class AmdgpuSysfs:
    """One source of the GPU inventory. drm and proc: the folders (tests give made-up trees)."""
    name = "sysfs"

    def __init__(self, drm: str = DRM, proc: str = PROC) -> None:
        self.drm = drm
        self.proc = proc

    def gpus(self) -> List[Gpu]:
        out: List[Gpu] = []
        for total in sorted(glob.glob(os.path.join(self.drm, "card*", "device", "mem_info_vram_total"))):
            dev = os.path.dirname(total)
            name = _read(os.path.join(dev, "product_name")).strip() or GENERIC_AMD
            g = parsers.parse_sysfs_vram(_read(total), _read(os.path.join(dev, "mem_info_vram_used")), name,
                                         _slot(dev), _read(os.path.join(dev, "mem_info_gtt_total")))
            if g is not None and g.pci not in {x.pci for x in out if x.pci}:
                out.append(g)
        return out

    def process_vram(self, pid: int) -> Dict[str, int]:
        """The bytes the process holds on each GPU, by PCI address: each DRM client once ({} when unknown)."""
        out: Dict[str, int] = {}
        seen: Set[str] = set()
        for fd in glob.glob(os.path.join(self.proc, str(pid), "fdinfo", "*")):
            pdev, client, vram = parsers.parse_fdinfo(_read(fd))
            if pdev and f"{pdev}/{client}" not in seen:
                seen.add(f"{pdev}/{client}")
                out[pdev] = out.get(pdev, 0) + vram
        return out
