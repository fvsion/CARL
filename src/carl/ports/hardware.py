"""Ports of the computer's hardware: the GPUs, their memory and the RAM."""
from __future__ import annotations

from typing import Dict, List, Optional, Protocol, Tuple

from ..domain.hardware.cpu_topology import CpuTopology
from ..domain.hardware.devices import Gpu
from ..domain.memory.split import Slot


class GpuLimit(Protocol):
    """What a model can use on the GPU. Contract C-003."""

    @property
    def unified(self) -> bool:
        """True when the GPU and the CPU share the RAM (a Mac, or no GPU: the model runs from the RAM, or a GPU that
        shares the RAM: an APU). False on a discrete GPU: its VRAM is apart from the RAM, so the RAM does not
        cap it."""
        ...

    @property
    def dedicated(self) -> int:
        """Of limit(), the bytes that are the GPUs' own memory (a discrete GPU's VRAM, the carve-out of a GPU that
        shares the RAM), which the RAM does not cap. 0 on a Mac and with no GPU: all of it is the RAM's."""
        ...

    @property
    def shared_gpus(self) -> Tuple[str, ...]:
        """The names of the GPUs a model runs on that share the RAM (an APU); () when none."""
        ...

    def limit(self) -> Tuple[int, str]:
        """(bytes the GPU may use, how it was found)."""
        ...

    def slots(self) -> List[Slot]:
        """The devices a start may use, in the order of llama.cpp's -dev: each with its llama.cpp name (CUDA0)
        and the room a model may use there. [] on a Mac, with no GPU, and when no GPU can be named."""
        ...


class HostMemory(Protocol):
    """This Mac's RAM and whether the VM's network is up (auto fit keeps a reserve for
    macOS and apps, more with the VM running). Contract C-004."""

    def ram_bytes(self) -> int:
        """Installed RAM (0 when unknown)."""
        ...

    def vm_network_up(self) -> bool: ...

    def free_bytes(self) -> int:
        """The RAM free now, with what the OS hands back at once (its file cache, the inactive pages): the load mode's
        test (A44, A45). 0 when unknown."""
        ...


class GpuInventory(Protocol):
    """Every GPU of this computer, from every source the OS has (merged; asked again after a short time).
    Contract C-013."""

    def devices(self) -> List[Gpu]:
        """The GPUs, the main one first ([] when there is none)."""
        ...

    def cuda_version(self) -> Optional[Tuple[int, int]]:
        """The CUDA version the NVIDIA driver supports (None without one)."""
        ...

    def source(self) -> str:
        """Which tool told the free memory, in words (for the reports)."""
        ...


class OwnServerVram(Protocol):
    """The VRAM CARL's own llama-server holds, added back to the free VRAM while it runs. Contract C-014."""

    def used(self, pid: int) -> Dict[int, int]:
        """Bytes the process holds on each GPU, by device index ({} when unknown, or with unified memory)."""
        ...


class HostInfo(Protocol):
    """This computer: its RAM and the name of its processor, for the reports and the Auto-tune record.
    Contract C-015."""

    def ram_bytes(self) -> int:
        """Installed RAM (0 when unknown)."""
        ...

    def machine(self) -> str:
        """The processor and the RAM in words ("Apple M2 Max 32 GB"); "" when unknown."""
        ...


class CpuLayout(Protocol):
    """The CPU's logical processors and which physical core each belongs to, for the threads of a start that keeps MoE
    experts in the RAM (decision A54). Contract C-058."""

    def topology(self) -> CpuTopology:
        """The logical processors this process may run on, each with llama.cpp's number for it (its bit in
        --cpu-mask), its physical core and its core's speed rank. Empty when the computer cannot say (the start then
        passes no pin). Asked again on each call (cheap: a few files or one system call)."""
        ...
