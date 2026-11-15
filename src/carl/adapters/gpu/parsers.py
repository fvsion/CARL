"""What the GPU tools print, read into Gpu values (domain/hardware/devices.py). Pure: no tool runs here (the
sources beside it run them: nvidia_smi.py, amdgpu_sysfs.py, windows_counters.py, llama_devices.py), so every OS can
test every parser.
"""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Dict, List, Optional, Set, Tuple

from ...domain.hardware.devices import MIB, Gpu, is_integrated, vendor_of
from ...domain.hardware.gpu import pci_address

# llama-server --list-devices: "  CUDA0: NVIDIA GeForce RTX 2070 SUPER (8191 MiB, 7131 MiB free)"
_DEVICE = re.compile(r"^\s*([A-Za-z_]+?)(\d+)\s*:\s*(.+?)\s*\((\d+)\s*MiB,\s*(\d+)\s*MiB free\)\s*$")
# A Windows GPU counter's instance: "pid_14240_luid_0x00000000_0x0000C2B5_phys_0" (per process) or
# "luid_0x00000000_0x0000C2B5_phys_0" (per adapter)
# The line llama.cpp's Vulkan backend writes for each device when it starts (also for --list-devices), on stderr:
# "ggml_vulkan: 0 = AMD Radeon Graphics (RADV GFX1151) (radv) | uma: 1 | fp16: 1 | ..." ; uma 1: the device shares the
# memory of the CPU (Vulkan's integrated GPU type: an APU)
_VULKAN_DEVICE = re.compile(r"^\s*ggml_vulkan:\s*(\d+)\s*=\s*.*?\|\s*uma:\s*([01])\b", re.M)
_INSTANCE = re.compile(r"^(?:pid_(\d+)_)?luid_(0x[0-9a-fA-F]+)_(0x[0-9a-fA-F]+)_phys_\d+$")


def parse_vulkan_types(text: str) -> Dict[int, bool]:
    """{Vulkan device number: it shares the CPU's memory (an integrated GPU)} from the "ggml_vulkan: N = ... | uma: 1"
    lines of llama.cpp's start-up log."""
    return {int(m.group(1)): m.group(2) == "1" for m in _VULKAN_DEVICE.finditer(text)}


def parse_list_devices(text: str, free_known: bool = True) -> List[Gpu]:
    """The devices of `llama-server --list-devices` (none: []). free_known False: its "free" is not the GPU's (on
    Windows it leaves out the other programs), so free is None. A Vulkan device that the text's ggml_vulkan lines
    call uma (Vulkan's integrated type) is integrated."""
    out: List[Gpu] = []
    uma = parse_vulkan_types(text)
    seen: Set[Tuple[str, str]] = set()           # a line that stdout and stderr both carry counts once
    for line in text.splitlines():
        m = _DEVICE.match(line)
        if m and (m.group(1), m.group(2)) not in seen:
            seen.add((m.group(1), m.group(2)))
            out.append(Gpu(name=m.group(3), total=int(m.group(4)) * MIB,
                           free=int(m.group(5)) * MIB if free_known else None, vendor=vendor_of(m.group(3)),
                           backend=m.group(1), index=int(m.group(2)), device=f"{m.group(1)}{m.group(2)}",
                           integrated=m.group(1).lower() == "vulkan" and uma.get(int(m.group(2)), False)))
    return out


def parse_nvidia_smi(text: str) -> List[Gpu]:
    """`nvidia-smi --query-gpu=index,name,memory.total,memory.free,driver_version,compute_cap[,pci.bus_id,uuid]
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
                       driver=f[4] if len(f) > 4 else "", pci=pci_address(f[6]) if len(f) > 6 else "",
                       uuid=f[7] if len(f) > 7 and f[7].startswith("GPU-") else ""))
    return out


def cuda_version(nvidia_smi_text: str) -> Optional[Tuple[int, int]]:
    """The newest CUDA the driver runs, from plain `nvidia-smi` ("CUDA Version: 13.4", or since the 610 drivers
    "CUDA UMD Version: 13.4")."""
    m = re.search(r"CUDA (?:UMD )?Version:\s*(\d+)\.(\d+)", nvidia_smi_text)
    return (int(m.group(1)), int(m.group(2))) if m else None


def parse_compute_apps(text: str) -> Dict[int, Dict[str, int]]:
    """`nvidia-smi --query-compute-apps=pid,used_memory,gpu_bus_id --format=csv,noheader,nounits`: {pid: {PCI
    address: bytes}}. On Windows the memory is [N/A] (WDDM): such a process is left out."""
    out: Dict[int, Dict[str, int]] = {}
    for line in text.splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) >= 3 and f[0].isdigit() and f[1].isdigit():
            pci = pci_address(f[2])
            per = out.setdefault(int(f[0]), {})
            per[pci] = per.get(pci, 0) + int(f[1]) * MIB
    return out


def parse_sysfs_vram(total: str, used: str, name: str = "AMD GPU", pci: str = "", gtt_total: str = "") -> Optional[Gpu]:
    """An amdgpu card from /sys/class/drm/cardN/device/mem_info_vram_total and mem_info_vram_used (bytes). An APU's
    VRAM is the BIOS carve-out of the RAM; mem_info_gtt_total is the system memory the driver maps besides (the GTT),
    kept as Gpu.shared. Only a GPU that shares the RAM uses it (a discrete card's GTT is a window on the RAM, not its
    own memory); and the files do not say which kind a nameless card is (an APU has no product_name): llama.cpp's
    Vulkan device type does (domain/hardware/gpu.py mark_integrated), after this is read."""
    try:
        t, u = int(total.strip()), int(used.strip())
    except ValueError:
        return None
    if t <= 0:
        return None
    g = Gpu(name=name, total=t, free=max(t - u, 0), vendor="amd", backend="sysfs", pci=pci_address(pci))
    gtt = int(gtt_total) if gtt_total.strip().isdecimal() else 0
    return replace(g, shared=gtt) if gtt else g


def parse_fdinfo(text: str) -> Tuple[str, str, int]:
    """One /proc/PID/fdinfo/N of a DRM client: (its PCI address, its client id, its VRAM bytes); ("", "", 0) for
    another kind of file. amdgpu writes "drm-pdev:", "drm-client-id:" and "drm-memory-vram: N KiB"."""
    pdev = client = ""
    vram = 0
    units = {"": 1, "KiB": 1024, "MiB": MIB}
    for line in text.splitlines():
        key, _, val = line.partition(":")
        val = val.strip()
        if key == "drm-pdev":
            pdev = pci_address(val)
        elif key == "drm-client-id":
            client = val
        elif key == "drm-memory-vram":
            num, _, unit = val.partition(" ")
            if num.isdigit():
                vram = int(num) * units.get(unit.strip(), 1)
    return (pdev, client, vram) if pdev and client else ("", "", 0)


def counter_instance(name: str) -> Tuple[Optional[int], str]:
    """(pid or None, LUID) of a Windows GPU counter's instance name; (None, "") when it is not one. The LUID is in
    lower case, as luid_of writes it."""
    m = _INSTANCE.match(name.strip())
    if not m:
        return None, ""
    return (int(m.group(1)) if m.group(1) else None), luid_of(int(m.group(2), 16), int(m.group(3), 16))


def luid_of(high: int, low: int) -> str:
    """A Windows adapter LUID in one form: 0x00000000_0x0000c2b5 (the high part, then the low part)."""
    return f"0x{high & 0xFFFFFFFF:08x}_0x{low & 0xFFFFFFFF:08x}"


def windows_adapter(total: int, used: Optional[int], name: str, pci_id: str, luid: str = "", pci: str = "",
                    integrated: bool = False, shared: int = 0) -> Gpu:
    """A Windows display adapter: its own memory and the performance counter's dedicated use (bytes). pci_id: its
    PCI vendor (VEN_10DE, 0x10de) or name; luid and pci: how the other sources name it. shared: the adapter's shared
    system memory (D3DKMT), kept for a GPU that shares the RAM only."""
    g = Gpu(name=name, total=total, free=max(total - used, 0) if used is not None else None,
            vendor=vendor_of(pci_id) or vendor_of(name), backend="d3dkmt", luid=luid, pci=pci, integrated=integrated,
            os_total=total, os_used=used)
    return replace(g, shared=shared) if shared > 0 and is_integrated(g) else g
