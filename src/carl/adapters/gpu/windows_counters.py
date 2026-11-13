"""The GPUs of a Windows computer, any vendor: each display adapter's own memory, PCI address and LUID (D3DKMT), and
the VRAM in use on it and per process (the "GPU Adapter Memory" and "GPU Process Memory" counters, named by LUID).
The calls to Windows are adapters/windows/gpu_counters.py's; the composition gives them here (rule R3), so every OS
tests this module with made-up answers.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Mapping, Union

from ...domain.hardware.devices import Gpu
from ...domain.hardware.gpu import pci_of
from . import parsers

Value = Union[str, int, bool]
ADAPTER_MEMORY = "\\GPU Adapter Memory(*)\\Dedicated Usage"
PROCESS_MEMORY = "\\GPU Process Memory(*)\\Dedicated Usage"


def adapter_gpu(a: Mapping[str, Value], used: Mapping[str, float]) -> Gpu:
    """One adapter of adapters() as a Gpu, with the VRAM in use from the adapter counter (by its LUID)."""
    luid = parsers.luid_of(int(a.get("luid_high", 0)), int(a.get("luid_low", 0)))
    vendor = int(a.get("vendor_id", 0))
    in_use = used.get(luid)
    bus, dev, fn = int(a.get("bus", 0)), int(a.get("device", 0)), int(a.get("function", 0))
    # an adapter with no PCI address says bus 0xFFFFFFFF (measured on the Windows test PC: the display-only twin of a card)
    pci = pci_of(bus, dev, fn) if a.get("pci") and bus < 256 and dev < 32 and fn < 8 else ""
    return parsers.windows_adapter(int(a.get("dedicated", 0)), int(in_use) if in_use is not None else None,
                                   str(a.get("name", "")), f"0x{vendor:04x}" if vendor else "", luid, pci,
                                   bool(a.get("hybrid_integrated")), int(a.get("shared", 0)))


def by_luid(values: Mapping[str, float], pid: int = -1) -> Dict[str, float]:
    """A counter's values summed by LUID: the adapters' (pid -1) or one process's."""
    out: Dict[str, float] = {}
    for name, v in values.items():
        who, luid = parsers.counter_instance(name)
        if luid and (who is None if pid < 0 else who == pid):
            out[luid] = out.get(luid, 0.0) + v
    return out


class WindowsCounters:
    """One source of the GPU inventory. adapters: D3DKMT's adapter list; counters: a counter's {instance: value} by
    its English path (adapters/windows/gpu_counters.py). A missing counter leaves the free VRAM unknown."""
    name = "the Windows GPU counters"

    def __init__(self, adapters: Callable[[], List[Dict[str, Value]]],
                 counters: Callable[[str], Dict[str, float]]) -> None:
        self.adapters = adapters
        self.counters = counters

    def gpus(self) -> List[Gpu]:
        # a software or display-only adapter, or one with no memory of its own (a virtual display), runs no model
        found = [a for a in self.adapters() if a.get("render", True) and not a.get("software") and a.get("dedicated")]
        if not found:
            return []
        used = by_luid(self.counters(ADAPTER_MEMORY))
        return [adapter_gpu(a, used) for a in found]

    def process_vram(self, pid: int) -> Dict[str, int]:
        """The bytes the process holds on each GPU, by LUID ({} when unknown)."""
        return {k: int(v) for k, v in by_luid(self.counters(PROCESS_MEMORY), pid).items()}

    def every_process_vram(self) -> Dict[int, Dict[str, int]]:
        """pid -> {LUID: bytes} of every process that holds GPU memory, from one read of the counter (the dashboard's
        MEMORY card on Windows, W6.8)."""
        out: Dict[int, Dict[str, int]] = {}
        for name, v in self.counters(PROCESS_MEMORY).items():
            pid, luid = parsers.counter_instance(name)
            if pid is not None and luid and v > 0:
                per = out.setdefault(pid, {})
                per[luid] = per.get(luid, 0) + int(v)
        return out
