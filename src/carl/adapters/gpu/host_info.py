"""A PC's HostInfo for Auto-tune's record (implements HostInfo, contract C-015): the processor and the GPUs with their
VRAM (domain/hardware/devices.py machine_key), over the OS's HostInfo (the RAM and the processor) and the GPU
inventory. The GPUs are asked when the record is made, not when the object is built."""
from __future__ import annotations

from typing import Callable, List

from ...domain.hardware.devices import Gpu, machine_key
from ...ports import HostInfo


class GpuHostInfo:
    """host: the OS's HostInfo; cpu: the processor's name alone (its cpu_name); gpus: the inventory's devices."""

    def __init__(self, host: HostInfo, cpu: Callable[[], str], gpus: Callable[[], List[Gpu]]) -> None:
        self.host, self.cpu, self.gpus = host, cpu, gpus

    def ram_bytes(self) -> int:
        return self.host.ram_bytes()

    def cpu_name(self) -> str:
        """The processor's name alone (Auto-tune's first line)."""
        return self.cpu()

    def machine(self) -> str:
        try:
            found = self.gpus()
        except (OSError, ValueError):
            found = []                                  # the key falls back to the processor and the RAM
        return machine_key(self.cpu(), self.host.machine(), found)
