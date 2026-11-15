"""A discrete GPU (NVIDIA, AMD, Intel) on Windows and Linux: the device CARL found and the memory a model can use
on it. Pure: the adapters in adapters/gpu/ ask the tools, adapters/gpu/parsers.py reads their output and
domain/hardware/gpu.py merges the devices of every source; the llama.cpp build for the device is
domain/llama/build.py.

On a Mac the GPU and the CPU share one memory (unified memory): the limit is what macOS lets the GPU use
(adapters/macos/sysctl_host.py MacGpuLimit). A discrete GPU has its own memory (VRAM), apart from the RAM:
  limit = the free VRAM when CARL looks + what CARL's own server holds there - the part kept free
for each GPU; domain/memory/budget.py adds the GPUs up. The part kept free is the setting gpu.vram_keep (512 MiB,
decisions 6 and A43). Free, not total: the desktop and other programs use VRAM too (a Windows desktop: 1.9 GiB of 8 GiB,
measured). Which tool tells the free VRAM (measured on Windows 11, an RTX 2070 SUPER, 2026-10-14):
  - nvidia-smi (NVIDIA, Windows and Linux): total, used and free of the whole GPU: correct.
  - llama-server --list-devices (every backend): the total is correct, but on Windows the "free" of the CUDA and
    Vulkan backends leaves out the other programs (7131 MiB free while nvidia-smi said 4364): use it only for the
    total, or on Linux.
  - Windows, any vendor: each adapter's own memory (D3DKMT) and the performance counter
    "GPU Adapter Memory\\Dedicated Usage" (in use; nvidia-smi said 0.45 GiB more: the driver's own part), both
    named by the adapter's LUID.
  - Linux, AMD: /sys/class/drm/cardN/device/mem_info_vram_total and mem_info_vram_used (bytes).

A GPU that shares the RAM (an AMD APU: Ryzen AI, Strix Halo; WAPU, decision A37) has no VRAM apart from the RAM: its
"total" is the carve-out the BIOS sets aside, and `shared` the system memory the driver lets it map besides (amdgpu's
GTT on Linux, the adapter's shared system memory on Windows). A model there may use the carve-out and the shared
part, and the shared part is the RAM's: domain/memory/budget.py and Budget (domain/memory/autofit.py) cap it by the
RAM less the reserve. The part kept free on a discrete GPU does not apply to it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

MIB = 2 ** 20
GIB = 2 ** 30
# VRAM kept free on each discrete GPU by default (gpu.vram_keep, decisions 6 and A43): room for the desktop's VRAM to
# grow while the server runs. The backend's own context is in the estimate since A43 (domain/memory/calibration.py:
# 163 MiB on CUDA, measured), so the keep is only for that growth (it was 1 GiB before).
VRAM_KEEP = 512 * MIB
VENDORS = {"10de": "nvidia", "1002": "amd", "8086": "intel"}
# A GPU that shares the RAM (an APU, a laptop's graphics) is left out of the discrete devices: by its name, or by its
# own memory (below INTEGRATED_BELOW it is a carve-out of the RAM).
_INTEGRATED = re.compile(r"\b(uhd|hd|iris( xe)?) graphics\b|radeon\(tm\) graphics|\bradeon graphics\b|"
                         r"\bradeon(\(tm\))? \d{3,4}[ms]\b|\bradeon vega \d+ graphics\b|microsoft basic", re.I)
INTEGRATED_BELOW = GIB
GENERIC_AMD = "AMD GPU"             # the name of an amdgpu card that has no product_name (an APU has none)


@dataclass(frozen=True)
class Gpu:
    """One GPU. backend: the llama.cpp device kind (CUDA, ROCm, Vulkan, MTL...) or the tool that saw it
    (nvidia-smi, sysfs, d3dkmt); free None: not known. pci: its PCI address (0000:01:00.0, lower case; "" when not
    known); uuid: NVIDIA's GPU-... id; luid: the Windows adapter's LUID (0x00000000_0x0000c2b5); integrated: a
    source said it shares the RAM; device: llama.cpp's name for it (CUDA0, Vulkan1; "" when not known). os_total and
    os_used: the OS's own figures of the card (Windows: D3DKMT's dedicated memory, which leaves out what the driver
    reserves, and the adapter counter's use, the programs'); 0 and None when not known (W1.6: where the memory goes)."""
    name: str
    total: int
    free: Optional[int] = None
    vendor: str = ""
    backend: str = ""
    index: int = 0
    compute_cap: Optional[Tuple[int, int]] = None
    driver: str = ""
    pci: str = ""
    uuid: str = ""
    luid: str = ""
    integrated: bool = False
    device: str = ""
    shared: int = 0
    os_total: int = 0
    os_used: Optional[int] = None

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


def is_integrated(gpu: Gpu) -> bool:
    """A GPU that shares the RAM: a source said so, its name is an integrated GPU's, or it has less than 1 GiB of
    its own memory. An NVIDIA GPU never is (its laptop GPUs have their own VRAM)."""
    if gpu.integrated:
        return True
    if gpu.vendor == "nvidia":
        return False
    return bool(_INTEGRATED.search(gpu.name)) or 0 < gpu.total < INTEGRATED_BELOW


# The vendors whose integrated GPUs CARL runs a model on (WAPU, decision A37): the AMD APUs. Another vendor's integrated
# GPU stays left out of what a model uses, as before (the CPU build runs the model).
SHARED_VENDORS = ("amd",)
# An APU's named GPU (Radeon 890M, 8060S): the big iGPUs of Ryzen AI and Strix Halo. Mesa's Vulkan driver (RADV) names
# those two just "AMD Radeon Graphics (RADV GFX1151)": their chip (gfx1150 to gfx1153) says it. The small display GPU of
# a desktop Ryzen is "Radeon(TM) Graphics" (two compute units): beside a discrete card it is not worth a layer.
_APU_NAMED = re.compile(r"\bradeon(\(tm\))? \d{3,4}[ms]\b|\bgfx115[0-3]\b", re.I)


def shares_ram(gpu: Gpu, beside_discrete: bool = False) -> bool:
    """A GPU that a model runs on and that shares the RAM: an integrated GPU of an AMD APU (Ryzen AI, Strix Halo and
    the like). Its memory is a carve-out of the RAM and the part of the RAM the driver maps (Gpu.shared). It must show
    that it is one: a named APU GPU, a driver that maps system memory for it (Gpu.shared), Vulkan's integrated type
    (Gpu.integrated) or a carve-out of 1 GiB or more; an integrated GPU with none of these (a bare "Radeon Graphics"
    and no figure) stays left out, and the CPU build runs the model. Beside a discrete GPU (beside_discrete) only a
    named APU GPU counts: a desktop Ryzen's display GPU would only slow the split, and would turn the card's CUDA
    build into Vulkan."""
    if gpu.vendor not in SHARED_VENDORS or not is_integrated(gpu):
        return False
    if beside_discrete:
        return bool(_APU_NAMED.search(gpu.name))
    return bool(_APU_NAMED.search(gpu.name)) or gpu.shared > 0 or gpu.integrated or gpu.total >= INTEGRATED_BELOW


def all_share_ram(gpus: Sequence[Gpu]) -> bool:
    """Every GPU of gpus shares the RAM (the GPUs a model uses: a shared-memory PC), and there is one."""
    return bool(gpus) and all(shares_ram(g) for g in gpus)


def main_gpu(gpus: Sequence[Gpu]) -> Optional[Gpu]:
    """The GPU with the most VRAM (the build is chosen for it); an integrated one only when there is no other."""
    real = [g for g in gpus if g.total > 0]
    own = [g for g in real if not is_integrated(g)] or real
    return max(own, key=lambda g: (g.total, -g.index)) if own else None


def vram_limit(gpu: Gpu, keep: int = VRAM_KEEP, own: int = 0) -> int:
    """What a model can use on one discrete GPU: the free VRAM (the total when the free is not known) plus own (what
    CARL's own server holds there, which CARL plans with), at most the total, less keep."""
    free = (gpu.free if gpu.free is not None else gpu.total) + own
    return max(min(free, gpu.total or free) - keep, 0)


def vram_words(total: int) -> str:
    """A GPU's memory in whole GiB when it is (8 GiB), else with one decimal (5.5 GiB)."""
    gib = total / GIB
    return f"{round(gib)} GiB" if abs(gib - round(gib)) < 0.1 else f"{gib:.1f} GiB"


def machine_key(cpu: str, machine: str, gpus: Sequence[Gpu]) -> str:
    """The computer an Auto-tune record was measured on (W6.9): on a PC with a discrete GPU the processor, then each
    GPU with its name and its VRAM ("AMD Ryzen 9 3900X, NVIDIA GeForce RTX 2070 SUPER 8 GiB"); the RAM does not decide
    the speed there. machine: the processor and the RAM in words (HostInfo.machine), which is the key where the model
    runs from the RAM (no GPU, or one that shares it) and on a Mac."""
    cards = [f"{g.name} {vram_words(g.total)}" for g in gpus if g.total > 0 and not is_integrated(g)]
    return ", ".join(([cpu] if cpu else []) + cards) if cards else machine
