"""The devices llama-server sees (`llama-server --list-devices`): every backend's names (CUDA0, Vulkan1) and totals.
Its free VRAM is right on Linux only (on Windows it leaves out the other programs), so the inventory asks it last:
for the names, and for the GPUs no other source sees. Its Vulkan lines (stderr) say which device is an integrated one
(uma 1: Vulkan's device type of an APU), so the text read is stdout and stderr.
"""
from __future__ import annotations

from typing import Callable, List, Optional, Sequence

from ...domain.hardware.devices import Gpu
from . import parsers
from .tool import run_logged

TIMEOUT = 60.0


def listed_devices(exe: Optional[str], run: Callable[..., str] = run_logged, free_known: bool = True) -> List[Gpu]:
    """The devices the llama-server at exe sees ([] without one)."""
    return parsers.parse_list_devices(run([exe, "--list-devices"], TIMEOUT), free_known) if exe else []


class LlamaDevices:
    """One source of the GPU inventory. exe: finds the llama-server (the locator: CARL_LLAMA_DIR, PATH); free_known:
    False on Windows. run: how the program runs; the composition gives the CUDA devices in PCI bus order, as the
    server CARL starts has them (the names it lists are what -dev takes)."""
    name = "llama-server --list-devices"

    def __init__(self, exe: Callable[[], Optional[str]], free_known: bool = True,
                 run: Callable[[Sequence[str], float], str] = run_logged) -> None:
        self.exe = exe
        self.free_known = free_known
        self.run = run

    def gpus(self) -> List[Gpu]:
        return listed_devices(self.exe(), self.run, self.free_known)
