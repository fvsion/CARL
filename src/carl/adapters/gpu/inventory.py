"""A computer that is not a Mac (Windows, Linux): its GPUs, what a model can use on them, its RAM and the llama.cpp
programs. The rules are in domain/hardware/gpu.py (merge, select), domain/memory/budget.py (the room on each GPU)
and domain/llama/build.py; the sources that ask the tools are beside this module, and the composition chooses them
for the OS (composition/profiles.py):

  windows   nvidia-smi, then the Windows GPU counters (D3DKMT and PDH); llama-server --list-devices without its free
  linux     nvidia-smi, then the amdgpu sysfs files; llama-server --list-devices
llama-server's list is asked when no other source finds a GPU, and for the Vulkan device types (which GPU is an
integrated one: an APU) when another source found a GPU of AMD that its name and size do not show to be integrated
(amdgpu's files give an APU no name, and a BIOS carve-out of 1 GiB or more looks like a discrete card's memory); the
type is matched to the GPU by its name or its memory (domain/hardware/gpu.py mark_integrated). Every tool runs from an
argument list; a tool that is missing or fails gives no answer, never an error.

The answers are asked again after TTL seconds (the free VRAM moves while other programs run), never only once per
process. While CARL's own llama-server runs, the VRAM it holds is added back, so `carl fit` gives the same answer
with the server running as with it stopped.
"""
from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ...domain.hardware import gpu as inv
from ...domain.hardware.devices import Gpu, shares_ram
from ...domain.hardware.gpu import Device
from ...domain.memory import budget
from ...domain.memory.budget import GpuSettings
from ...domain.memory.split import Slot

TTL = 5.0                                      # seconds an answer stays fresh
RETRY = 60.0                                   # seconds before llama-server's types are asked again, if it gave none


def llama_program(name: str = "llama-server", folder: Optional[str] = None, exe: str = "") -> Optional[str]:
    """The path of a llama.cpp program: in folder (CARL_LLAMA_DIR, the folder of an unpacked llama.cpp release:
    the composition reads it; exe: the program files' ending on this OS, ".exe" on Windows), else on PATH; None when
    neither has it."""
    if folder:
        p = os.path.join(os.path.expanduser(folder), name + exe)
        return p if os.path.isfile(p) else None
    return shutil.which(name)


@dataclass(frozen=True)
class Answer:
    """One reading of every source: every GPU (Device: CARL's number, and why a model does not use it), the CUDA
    version, the sources that answered in words, and the settings it was read with."""
    listing: List[Device] = field(default_factory=list)
    cuda: Optional[Tuple[int, int]] = None
    source: str = ""
    settings: GpuSettings = GpuSettings()


class Inventory:
    """Every GPU of this computer from every source (implements GpuInventory, contract C-013). sources: the OS's
    sources, best first (each has name and gpus(); nvidia-smi also cuda()); fallback: llama-server's list, asked
    when they find nothing, and for the Vulkan types of AMD GPUs; settings: gpu.vram_keep, gpu.backend, gpu.devices and
    gpu.shared_limit_gib (domain/memory/budget.py); env: the visibility variables (CUDA_VISIBLE_DEVICES and the
    like); prefer_rocm: CARL_ROCM=1."""

    def __init__(self, sources: Sequence[object], fallback: Optional[object] = None,
                 settings: Callable[[], GpuSettings] = GpuSettings, env: Optional[Mapping[str, str]] = None,
                 prefer_rocm: bool = False, ttl: float = TTL, clock: Callable[[], float] = time.monotonic) -> None:
        self.sources = list(sources)
        self.fallback = fallback
        self.read_settings = settings
        self.env = {k: v for k, v in (env or {}).items() if k in inv.VISIBILITY}
        self.prefer_rocm = prefer_rocm
        self.ttl = ttl
        self.clock = clock
        self._answer: Optional[Answer] = None
        self._at = 0.0
        self._types: List[Gpu] = []                  # llama-server's devices with their Vulkan types
        self._types_at = -RETRY

    def answer(self) -> Answer:
        """The last reading, read again when it is older than ttl."""
        now = self.clock()
        if self._answer is None or now - self._at >= self.ttl:
            self._answer, self._at = self._read(), now
        return self._answer

    def _read(self) -> Answer:
        found = [(s, s.gpus()) for s in self.sources]          # type: ignore[attr-defined]
        found = [(s, g) for s, g in found if g]
        if not found and self.fallback is not None:
            found = [(s, g) for s, g in ((self.fallback, self.fallback.gpus()),) if g]   # type: ignore[attr-defined]
        merged = inv.merge([g for _, g in found])
        if found and self.fallback is not None and not any(s is self.fallback for s, _ in found):
            merged = inv.mark_integrated(merged, self._vulkan_types(merged))
        header = next((s.cuda() for s in self.sources if hasattr(s, "cuda") and any(
            g.vendor == "nvidia" for g in merged)), None)
        settings = self.read_settings()
        return Answer(inv.select(merged, self.env, settings.backend, settings.devices, self.prefer_rocm),
                      inv.cuda_of(header, merged), " and ".join(str(getattr(s, "name", "?")) for s, _ in found),
                      settings)

    def _vulkan_types(self, merged: Sequence[Gpu]) -> List[Gpu]:
        """llama-server's devices, for the Vulkan device types, when an AMD GPU is not yet known to be integrated. The
        device types do not change while CARL runs: a list is kept; an empty one is asked for again after RETRY."""
        if not any(g.vendor == "amd" and not inv.is_integrated(g) for g in merged):
            return []
        return self.llama_list()

    def llama_list(self) -> List[Gpu]:
        """The devices of the llama-server that will run, with llama.cpp's names for them (CUDA0, Vulkan1): the names
        -dev takes (W6.6). They do not change while CARL runs: a list is kept; an empty one is asked for again after
        RETRY. [] without the source."""
        if self.fallback is None:
            return []
        now = self.clock()
        if not self._types and now - self._types_at >= RETRY:
            self._types, self._types_at = self.fallback.gpus(), now        # type: ignore[attr-defined]
        return self._types

    def listing(self) -> List[Device]:
        """Every GPU with CARL's number, the ones a model does not use with the reason (carl gpu)."""
        return list(self.answer().listing)

    def devices(self) -> List[Gpu]:
        """The GPUs a model uses, in CARL's order (PCI bus order)."""
        return inv.used(self.answer().listing)

    def cuda_version(self) -> Optional[Tuple[int, int]]:
        return self.answer().cuda

    def source(self) -> str:
        return self.answer().source

    def settings(self) -> GpuSettings:
        return self.answer().settings

    def names(self) -> Dict[int, str]:
        """llama.cpp's name for each GPU a model uses, by CARL's number (domain/hardware/gpu.py llama_names): from the
        devices the llama-server lists, else (CUDA) by PCI order. A llama-server that is there but lists nothing (it
        failed or timed out, or it is a build without a GPU) gives no names: the build is not known to be CUDA's, and
        -dev with a wrong name would stop the server. With no llama-server yet, CARL installs the build that matches
        the GPUs, so the guess by PCI order stands."""
        a = self.answer()
        listed = self.llama_list()
        if not listed and self.server_found():
            return {}
        return inv.llama_names(a.listing, listed, self.env, a.settings.backend, self.prefer_rocm)

    def server_found(self) -> bool:
        """Is there a llama-server to ask (the fallback source finds its program)?"""
        exe = getattr(self.fallback, "exe", None)
        return bool(exe and exe())


class OwnServers:
    """The VRAM a process holds on each GPU, by CARL's number (implements OwnServerVram, contract C-014): the
    sources that can tell it (nvidia-smi's compute apps on Linux, the per-process counter on Windows, amdgpu's
    fdinfo), matched to the inventory's GPUs by PCI address or LUID. The larger answer wins for one GPU."""

    def __init__(self, inventory: Inventory, sources: Sequence[object]) -> None:
        self.inventory = inventory
        self.sources = [s for s in sources if hasattr(s, "process_vram")]

    def used(self, pid: int) -> Dict[int, int]:
        out: Dict[int, int] = {}
        gpus = [d.gpu for d in self.inventory.listing()]
        for s in self.sources:
            for key, n in s.process_vram(pid).items():                  # type: ignore[attr-defined]
                g = next((g for g in gpus if key and key in (g.pci, g.luid)), None)
                if g is not None:
                    out[g.index] = max(out.get(g.index, 0), n)
        return out


class DeviceGpuLimit:
    """What a model can use on a computer with discrete GPUs (implements GpuLimit, contract C-003): on each GPU a
    model uses, the free VRAM plus what CARL's own servers hold there, less gpu.vram_keep; added up
    (domain/memory/budget.py). A GPU that shares the RAM (an AMD APU) gives its carve-out and the system memory the
    driver maps, or the setting gpu.shared_limit_gib, with no keep (Budget caps its RAM part). No GPU: the RAM (the
    CPU build runs the model from RAM; auto fit keeps its reserve).
    own: OwnServers; own_pids: the pids of CARL's own llama-servers (the composition finds them); ram: the RAM.
    Read again after the inventory's ttl."""

    def __init__(self, inventory: Inventory, own: Optional[OwnServers] = None,
                 own_pids: Callable[[], List[int]] = list, ram: Callable[[], int] = lambda: 0) -> None:
        self.inventory = inventory
        self.own = own
        self.own_pids = own_pids
        self.ram = ram
        self._value: Optional[Tuple[int, str]] = None
        self._dedicated = 0
        self._at = 0.0

    @property
    def unified(self) -> bool:
        """False with a discrete GPU: its VRAM is apart from the RAM. No GPU, or a GPU that shares the RAM: the model
        is in the RAM, as on a Mac."""
        found = self.inventory.devices()
        return not found or any(shares_ram(g) for g in found)

    @property
    def dedicated(self) -> int:
        """Of the limit, the GPUs' own memory: a discrete GPU's room, an APU's carve-out (0 with no GPU). Read with
        limit(), so both come from the same moment."""
        self.limit()
        return self._dedicated

    @property
    def shared_gpus(self) -> Tuple[str, ...]:
        """The names of the GPUs a model runs on that share the RAM."""
        return tuple(g.name for g in self.inventory.devices() if shares_ram(g))

    def own_vram(self) -> Dict[int, int]:
        """What CARL's own servers hold on each GPU, by CARL's number ({} when none runs)."""
        out: Dict[int, int] = {}
        if self.own is None:
            return out
        for pid in self.own_pids():
            for i, n in self.own.used(pid).items():
                out[i] = out.get(i, 0) + n
        return out

    def rooms(self) -> List[budget.Room]:
        """The room on each GPU a model uses."""
        s = self.inventory.settings()
        return budget.rooms(self.inventory.devices(), s.keep, self.own_vram(), s.shared_limit, self.ram())

    def slots(self) -> List[Slot]:
        """The devices a start may use, in CARL's order (PCI bus order, the order of -dev): llama.cpp's name for each
        GPU and the room a model may use there. A GPU llama-server does not see is left out; [] when no GPU has a
        name (no GPU, or none can be named)."""
        names = self.inventory.names()
        return [Slot(names[r.gpu.index], r.gpu, r.limit, r.shared, r.own) for r in self.rooms() if r.gpu.index in names]

    def limit(self) -> Tuple[int, str]:
        now = self.inventory.clock()
        if self._value is None or now - self._at >= self.inventory.ttl:
            found = self.rooms()
            self._dedicated = budget.dedicated(found)
            if not found:
                self._value = (self.ram(), "no GPU found: the RAM")
            else:
                s = self.inventory.settings()
                self._value = (budget.total(found), budget.how(found, s.keep, self.inventory.source(), s.shared_limit))
            self._at = now
        return self._value


class PcHost:
    """The RAM of a computer that is not a Mac (implements HostMemory). The VMware network check is a Mac's. free: the
    RAM free now, the OS's own reader (the composition gives Windows' GlobalMemoryStatusEx); none: not known (0)."""

    def __init__(self, free: Optional[Callable[[], int]] = None, ram: Callable[[], int] = lambda: 0) -> None:
        self._free = free
        self._ram = ram

    def ram_bytes(self) -> int:
        return self._ram()

    def vm_network_up(self) -> bool:
        return False

    def free_bytes(self) -> int:
        return self._free() if self._free is not None else 0
