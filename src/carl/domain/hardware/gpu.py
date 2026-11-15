"""The GPU inventory's rules (W6.1): every GPU of this computer, from every source the OS has, merged into one list.
Pure: the adapters in adapters/gpu/ ask the tools; this module decides what the answers mean.

  merge     one device per GPU from every source: matched by PCI address, else the Windows LUID or NVIDIA's UUID,
            else the vendor and the name. An earlier source wins for each value (nvidia-smi's free VRAM before the
            counters', the counters' before llama-server's --list-devices).
  select    CARL's numbers (PCI bus order) and which devices a model uses: integrated GPUs are left out (those of an
            AMD APU stay in: they share the RAM, WAPU, decision A37; domain/hardware/devices.py shares_ram), then what
            the visibility variables hide (CUDA_VISIBLE_DEVICES, HIP_VISIBLE_DEVICES, ROCR_VISIBLE_DEVICES,
            GGML_VK_VISIBLE_DEVICES, for the backend that runs), then what the setting gpu.devices leaves out.
  versions  CUDA versions compare as numbers (13.10 is above 13.4); without the driver's own line, its version
            number tells the newest CUDA it runs.

Moved (W2.2): the GPU values are domain/hardware/devices.py, the llama.cpp build is domain/llama/build.py. This
module also forwards those names until W9.4.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .devices import (GENERIC_AMD, GIB, MIB, VENDORS, VRAM_KEEP, Gpu, is_integrated, main_gpu, shares_ram,  # noqa: F401
                      vendor_of, vram_limit)
from ..llama.build import (CUDA13_MIN_CC, Backend, BuildChoice, Version, choose_build, release_assets,  # noqa: F401
                           version)
# The variables that hide GPUs from a backend (llama.cpp's backends read them; CARL counts only what they show).
CUDA_VISIBLE, HIP_VISIBLE, ROCR_VISIBLE, VK_VISIBLE = (
    "CUDA_VISIBLE_DEVICES", "HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES", "GGML_VK_VISIBLE_DEVICES")
VISIBILITY = (CUDA_VISIBLE, HIP_VISIBLE, ROCR_VISIBLE, VK_VISIBLE)
# The newest CUDA an NVIDIA driver runs, from its version (NVIDIA's table of the toolkits' smallest drivers, CUDA 12.9
# release notes; the larger of the Windows and Linux numbers of each GA release, checked in W6.6: 12.8 is 570.65, 11.8 is
# 520.61; the 13.0 row is not in that table, and a driver newer than it is taken as 13.0: the 616.56 of the Windows test PC runs 13.4).
# Only for a driver whose nvidia-smi does not print its CUDA version.
DRIVER_CUDA: Tuple[Tuple[Tuple[int, int], Version], ...] = (
    ((580, 88), (13, 0)), ((576, 2), (12, 9)), ((570, 65), (12, 8)), ((560, 94), (12, 6)), ((555, 85), (12, 5)),
    ((551, 61), (12, 4)), ((546, 12), (12, 3)), ((536, 25), (12, 2)), ((531, 14), (12, 1)), ((527, 41), (12, 0)),
    ((520, 61), (11, 8)))
# The words for why a GPU is not used (carl gpu).
INTEGRATED_WHY = "it shares the RAM (an integrated GPU)"
SETTING_WHY = "not in your setting gpu.devices"


def driver_cuda(driver: str) -> Optional[Version]:
    """The newest CUDA an NVIDIA driver runs, from its version ("616.56", "580.88"); None when unknown or too old."""
    m = re.match(r"\s*(\d+)\.(\d+)", driver or "")
    if not m:
        return None
    d = (int(m.group(1)), int(m.group(2)))
    return next((cuda for least, cuda in DRIVER_CUDA if d >= least), None)


def cuda_of(header: Optional[Version], gpus: Sequence[Gpu]) -> Optional[Version]:
    """The CUDA version the driver runs: nvidia-smi's own line, else what the drivers' version numbers say (the
    lowest, so every NVIDIA GPU runs it)."""
    if header is not None:
        return header
    found = [c for c in (driver_cuda(g.driver) for g in gpus if g.vendor == "nvidia") if c is not None]
    return min(found) if found else None


# ---------------------------------------------------------------- merge
def pci_address(text: str) -> str:
    """A PCI address in one form, 0000:01:00.0 (nvidia-smi's 00000000:01:00.0 and sysfs' 0000:01:00.0 alike); ""
    when the text is not one."""
    m = re.search(r"(?:([0-9a-fA-F]{4,8}):)?([0-9a-fA-F]{2}):([0-9a-fA-F]{2})\.([0-7])\s*$", text or "")
    if not m:
        return ""
    return f"{int(m.group(1) or '0', 16):04x}:{m.group(2).lower()}:{m.group(3).lower()}.{m.group(4)}"


def pci_of(bus: int, device: int, function: int) -> str:
    """A PCI address from its numbers (D3DKMT gives them apart)."""
    return f"0000:{bus:02x}:{device:02x}.{function}"


def _name(g: Gpu) -> str:
    return re.sub(r"[^a-z0-9]+", " ", g.name.lower()).replace("nvidia ", "").strip()


def _same(a: Gpu, b: Gpu) -> bool:
    """Two answers about one GPU: the same PCI address, LUID or UUID; else (one of them has no address) the same
    vendor and name."""
    for key in ("pci", "luid", "uuid"):
        x, y = getattr(a, key), getattr(b, key)
        if x and y:
            return bool(x == y)
    return bool(a.vendor == b.vendor and _name(a) == _name(b))


def _fill(a: Gpu, b: Gpu) -> Gpu:
    """a, with what only b knows."""
    return replace(a, total=a.total or b.total, free=a.free if a.free is not None else b.free,
                   vendor=a.vendor or b.vendor, compute_cap=a.compute_cap or b.compute_cap, driver=a.driver or b.driver,
                   pci=a.pci or b.pci, uuid=a.uuid or b.uuid, luid=a.luid or b.luid,
                   integrated=a.integrated or b.integrated, device=a.device or b.device, shared=a.shared or b.shared,
                   os_total=a.os_total or b.os_total, os_used=a.os_used if a.os_used is not None else b.os_used)


def merge(sources: Sequence[Sequence[Gpu]]) -> List[Gpu]:
    """One device per GPU from every source's answer, the sources best first (see the module's docstring). Each
    device of a source matches at most one device found before."""
    out: List[Gpu] = []
    for found in sources:
        taken: List[int] = []
        for g in found:
            i = next((i for i, d in enumerate(out) if i not in taken and _same(d, g)), None)
            if i is None:
                out.append(g)
                taken.append(len(out) - 1)
            else:
                out[i] = _fill(out[i], g)
                taken.append(i)
    return out


VULKAN_MATCH = 0.05            # a Vulkan device and an amdgpu card are one GPU when their memory is this close


def mark_integrated(merged: Sequence[Gpu], listed: Sequence[Gpu]) -> List[Gpu]:
    """merged, with the AMD GPUs that llama.cpp's Vulkan device type calls integrated (uma 1) marked as such, for the
    sources that cannot tell (amdgpu's files: an APU has no product_name, and with a BIOS carve-out of 1 GiB or more
    its name and size look like a discrete card's). listed: llama-server's devices (Gpu.integrated is the type).
    Vulkan has no PCI address, so the match is by the GPU's name, else its memory: each Vulkan device that is not
    integrated takes the AMD GPU of nearly its memory, and the Vulkan devices that are integrated are the AMD GPUs
    left, when their number is the same. If it is not (a device hidden from Vulkan, an odd size) nothing is marked: the
    GPU stays as the files found it. A marked GPU takes llama.cpp's name for it and its device (Vulkan1)."""
    amd = [i for i, g in enumerate(merged) if g.vendor == "amd"]
    vk = [v for v in listed if v.vendor == "amd"]
    uma = [v for v in vk if v.integrated]
    if not uma:
        return list(merged)
    known = [i for i in amd if is_integrated(merged[i])]                  # their name or size already says so
    free = [i for i in amd if i not in known]
    for v in vk:
        if v.integrated:
            continue
        near = [i for i in free if _same(merged[i], v) or (
            v.total and abs(merged[i].total - v.total) <= VULKAN_MATCH * v.total)]
        if near:
            free.remove(near[0])
    want = len(uma) - len(known)
    if want <= 0 or len(free) != want:
        return list(merged)
    out = list(merged)
    names = [v for v in uma if not any(_same(merged[i], v) for i in known)]
    for i, v in zip(free, names):
        g = out[i]
        out[i] = replace(g, integrated=True, name=v.name if g.name == GENERIC_AMD else g.name,
                         device=g.device or v.device)
    return out


# ---------------------------------------------------------------- select
@dataclass(frozen=True)
class Device:
    """One GPU of the inventory: the GPU (its index is CARL's number) and why a model does not use it ("": used)."""
    gpu: Gpu
    left_out: str = ""


def run_backend(gpus: Sequence[Gpu], setting: str = "auto", prefer_rocm: bool = False) -> str:
    """The backend that runs these GPUs: cuda, rocm, vulkan or cpu. setting: gpu.backend (auto picks: NVIDIA alone is
    cuda, mixed vendors and AMD are vulkan, AMD with CARL_ROCM rocm)."""
    if setting != "auto":
        return setting
    vendors = {g.vendor for g in gpus}
    if not vendors:
        return "cpu"
    if vendors == {"nvidia"}:
        return "cuda"
    if vendors == {"amd"} and prefer_rocm:
        return "rocm"
    return "vulkan"


def _pci_order(gpus: Sequence[Gpu]) -> List[Gpu]:
    """The GPUs in PCI bus order (CUDA_DEVICE_ORDER=PCI_BUS_ID's order), those with no address after, as found."""
    keyed = sorted(enumerate(gpus), key=lambda p: (not p[1].pci, p[1].pci, p[0]))
    return [g for _, g in keyed]


def _shown(value: str, ids: Sequence[Tuple[int, str]]) -> List[int]:
    """The positions a visibility variable lets through: a list of numbers or (CUDA) UUIDs, comma-separated; the list
    ends at the first entry that names no device, as CUDA reads it. ids: (position, uuid)."""
    out: List[int] = []
    for tok in (t.strip() for t in value.split(",")):
        pos = next((p for p, u in ids if (tok.isdigit() and int(tok) == p) or (u and tok and u.startswith(tok)
                                                                                     and not tok.isdigit())), None)
        if pos is None:
            break
        out.append(pos)
    return out


def _hidden(gpus: Sequence[Gpu], env: Mapping[str, str], backend: str) -> Dict[int, str]:
    """{index: the variable that hides it} for the backend that runs."""
    rules: List[Tuple[str, Sequence[Gpu]]] = []
    if backend == "cuda":
        rules.append((CUDA_VISIBLE, [g for g in gpus if g.vendor == "nvidia"]))
    elif backend == "rocm":
        amd = [g for g in gpus if g.vendor == "amd"]
        rules += [(ROCR_VISIBLE, amd), (HIP_VISIBLE, amd)]
    elif backend == "vulkan":
        rules.append((VK_VISIBLE, list(gpus)))
    out: Dict[int, str] = {}
    for var, group in rules:
        if var not in env:
            continue
        group = [g for g in group if g.index not in out]
        ids = [(_vk(g, p) if var == VK_VISIBLE else p, g.uuid) for p, g in enumerate(group)]
        keep = set(_shown(env[var], ids))
        for (pos, _), g in zip(ids, group):
            if pos not in keep:
                out[g.index] = var
    return out


def _vk(g: Gpu, pos: int) -> int:
    """A GPU's Vulkan number: llama.cpp's Vulkan name for it when known (Vulkan1), else its place."""
    m = re.fullmatch(r"vulkan(\d+)", g.device.lower())
    return int(m.group(1)) if m else pos


def select(merged: Sequence[Gpu], env: Mapping[str, str], backend: str = "auto", chosen: Sequence[int] = (),
           prefer_rocm: bool = False) -> List[Device]:
    """Every GPU with CARL's number (PCI bus order) and, for the ones a model does not use, why: integrated, hidden by
    a visibility variable of the backend that runs (backend: gpu.backend), or not in chosen (gpu.devices; empty:
    every GPU). An integrated GPU is left out unless it is an AMD APU's (shares_ram), which a model runs on, beside
    a discrete GPU too when it is a named APU GPU (each by its own rule: domain/memory/budget.py)."""
    gpus = [replace(g, index=i) for i, g in enumerate(_pci_order(merged))]
    beside = any(not is_integrated(g) for g in gpus)
    runs = [g for g in gpus if not is_integrated(g) or shares_ram(g, beside)]
    hidden = _hidden(runs, env, run_backend(runs, backend, prefer_rocm))
    out: List[Device] = []
    for g in gpus:
        why = (INTEGRATED_WHY if is_integrated(g) and not shares_ram(g, beside) else
               f"hidden by {hidden[g.index]}" if g.index in hidden
               else SETTING_WHY if chosen and g.index not in chosen else "")
        out.append(Device(g, why))
    return out


def used(devices: Sequence[Device]) -> List[Gpu]:
    """The GPUs a model uses, in CARL's order."""
    return [d.gpu for d in devices if not d.left_out]


# ---------------------------------------------------------------- llama.cpp's names (W6.6)
CUDA_ORDER = ("CUDA_DEVICE_ORDER", "PCI_BUS_ID")      # the server's order of CUDA devices is CARL's (PCI bus order)
_KIND = {"cuda": "CUDA", "rocm": "ROCm", "vulkan": "Vulkan"}


def _cuda_places(listing: Sequence[Device], env: Mapping[str, str]) -> Dict[int, int]:
    """{CARL's number: its CUDA index} for the NVIDIA GPUs CUDA sees: those that CUDA_VISIBLE_DEVICES does not hide
    (the GPUs that the setting gpu.devices leaves out count too: the server sees them), in PCI bus order
    (CUDA_DEVICE_ORDER=PCI_BUS_ID), or in the order of CUDA_VISIBLE_DEVICES when it lists them."""
    every = [d for d in listing if d.gpu.vendor == "nvidia"]
    if CUDA_VISIBLE in env:
        order = _shown(env[CUDA_VISIBLE], [(p, d.gpu.uuid) for p, d in enumerate(every)])
        seen = [every[p].gpu for p in order]
    else:
        seen = [d.gpu for d in every if not d.left_out.startswith("hidden")]
    return {g.index: k for k, g in enumerate(seen)}


def _same_gpu(g: Gpu, listed: Gpu) -> bool:
    """llama-server's device is this GPU: the same vendor and name, else (amdgpu gives a card no name) the vendor and
    nearly the same memory."""
    if g.vendor and listed.vendor and g.vendor != listed.vendor:
        return False
    if _name(g) == _name(listed):
        return True
    return bool(g.total and listed.total and g.name == GENERIC_AMD
                and abs(g.total - listed.total) <= VULKAN_MATCH * listed.total)


def llama_names(listing: Sequence[Device], listed: Sequence[Gpu], env: Mapping[str, str], backend: str = "auto",
                prefer_rocm: bool = False) -> Dict[int, str]:
    """llama.cpp's name for each GPU a model uses (CUDA0, Vulkan1), by CARL's number: what -dev takes. listed: the
    devices of the llama-server that will run (llama-server --list-devices, in PCI order for CUDA); a GPU is the
    device with its vendor and name (identical GPUs in order), of the backend the run picks (run_backend) first. A GPU
    that llama-server does not see has no name. Without a list only CUDA can be named: the NVIDIA GPUs in PCI order
    (CUDA0, CUDA1; the server gets CUDA_DEVICE_ORDER=PCI_BUS_ID); other backends give none ({})."""
    mine = used(listing)
    if not mine:
        return {}
    run = run_backend(mine, backend, prefer_rocm)
    if run == "cpu":                                  # the setting gpu.backend: no GPU
        return {}
    kind = _KIND.get(run, "")
    if not listed:
        if kind != "CUDA" or any(g.vendor != "nvidia" for g in mine):
            return {}
        places = _cuda_places(listing, env)
        return {g.index: f"CUDA{places[g.index]}" for g in mine if g.index in places}
    out: Dict[int, str] = {}
    taken: List[str] = []
    for g in sorted(mine, key=lambda g: g.index):
        found = [d for d in listed if d.device and d.device not in taken and _same_gpu(g, d)]
        found.sort(key=lambda d: d.backend.lower() != kind.lower())            # the backend the run picks first
        if found:
            out[g.index] = found[0].device
            taken.append(found[0].device)
    return out
