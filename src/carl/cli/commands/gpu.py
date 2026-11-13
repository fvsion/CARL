"""`carl gpu [--offline]`: what CARL finds on this computer: every GPU (from every source) and its free memory, the
ones a model does not use and why, what a model can use on each with the part CARL keeps free (gpu.vram_keep), the
llama.cpp build and the files of its release, and the GPUs the installed llama-server sees. From the Windows
preview's `gpu`. It runs on every OS; the help shows it only off the Mac (cli/commands/__init__.py). The composition
asks the hardware (GpuFacts); this module only writes. Contract C-421.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ...domain.hardware import devices as dg
from ...domain.hardware.devices import Gpu
from ...domain.hardware.gpu import Device
from ...domain.llama.build import BuildChoice, release_assets
from ...domain.llama.version import PINNED
from ...domain.memory.budget import Room, keep_text, size_text, total
from .base import Cli


@dataclass(frozen=True)
class GpuFacts:
    """What the composition found: the OS and machine (platform.system, platform.machine), the RAM, every GPU
    (Device: CARL's number, and why a model does not use it) and the tools that saw them, the driver's CUDA version,
    the GPU the build is chosen for, the build, the files of the tested release (names: [] offline or without the
    network), the llama-server the locator found, its version line and the devices it lists, the room on each GPU
    a model uses (with what CARL's own server holds there), the VRAM kept free on each GPU, and the GPU settings
    that were left out (notes), gpu.shared_limit_gib in bytes (0: not set), and whether this computer is a shared-memory
    PC (its profile: an APU's GPU shares the RAM)."""
    system: str
    machine: str
    ram: int
    listing: List[Device]
    source: str
    cuda: Optional[Tuple[int, int]]
    main: Optional[Gpu]
    build: BuildChoice
    names: List[str] = field(default_factory=list)
    exe: Optional[str] = None
    version: str = "?"
    devices: List[Gpu] = field(default_factory=list)
    rooms: List[Room] = field(default_factory=list)
    keep: int = dg.VRAM_KEEP
    notes: List[str] = field(default_factory=list)
    shared_limit: int = 0
    shared_memory_pc: bool = False


def release_tag() -> str:
    """The llama.cpp release CARL installs and pins (bNNNNN)."""
    return f"b{PINNED.build}"


def room_lines(r: GpuFacts) -> List[str]:
    """What a model can use: on each GPU, and over all of them."""
    gib = dg.GIB
    keep = keep_text(r.keep)
    out: List[str] = []
    for x in r.rooms:
        if dg.shares_ram(x.gpu):
            out.append(shared_line(x, r.shared_limit))
            continue
        back = f", plus {x.own / gib:.1f} GiB that CARL's server holds," if x.own else ""
        out.append(f"A model can use {x.limit / gib:.1f} GiB of GPU {x.gpu.index}: its free memory{back} less "
                   f"{keep} that CARL keeps free.")
    if len(r.rooms) > 1:
        out.append(f"A model can use {total(r.rooms) / gib:.1f} GiB of the {len(r.rooms)} GPUs together.")
    if any(not dg.shares_ram(x.gpu) for x in r.rooms) or (r.listing and not r.rooms):
        out.append(f"CARL keeps {keep} free on each GPU (the setting gpu.vram_keep).")
    return out


def shared_line(x: Room, setting: int) -> str:
    """What a model can use on a GPU that shares the RAM: its carve-out and the RAM it may map, no part kept free (the
    RAM less the reserve caps the shared part: carl fit)."""
    gib = dg.GIB
    head = f"A model can use up to {x.limit / gib:.1f} GiB of GPU {x.gpu.index}, which shares the RAM: "
    if setting > 0:
        return head + "your setting gpu.shared_limit_gib. The RAM less the memory kept free for the system caps it."
    return head + (f"{size_text(x.gpu.total)} of its own and {size_text(x.shared)} of the RAM (the memory the driver "
                   f"maps). The RAM less the memory kept free for the system caps it. CARL keeps nothing more free "
                   f"on it.")


def gpu_lines(r: GpuFacts, command: str = "") -> List[str]:
    """The report. command: the command word, off the Mac (where engine install installs the build); "" on a Mac."""
    gib = dg.GIB
    out = [f"This computer: {r.system} {r.machine}, {r.ram / gib:.1f} GiB RAM."]
    if r.shared_memory_pc:
        out[0] = out[0][:-1] + ". Its GPU shares the RAM (a shared-memory PC)."
    if not r.listing:
        out.append("GPU: CARL found none (nvidia-smi, the GPU counters and llama-server --list-devices gave nothing).")
    for d in r.listing:
        g = d.gpu
        free = f", {g.free / gib:.1f} GiB free" if g.free is not None else ""
        extra = f", compute capability {g.compute_cap[0]}.{g.compute_cap[1]}" if g.compute_cap else ""
        extra += f", {g.shared / gib:.1f} GiB of shared system memory" if g.shared and dg.shares_ram(g) else ""
        out.append(f"GPU {g.index}: {g.name}: {g.total / gib:.1f} GiB{free}{extra} (from {r.source}).")
        if d.left_out:
            out.append(f"  A model does not use it: {d.left_out}.")
    if r.cuda:
        out.append(f"The NVIDIA driver runs CUDA {r.cuda[0]}.{r.cuda[1]} and older.")
    out += room_lines(r)
    out += [f"note: {n}" for n in r.notes]
    b = r.build
    out.append(f"The llama.cpp build for this computer: {b.backend} ({b.why}).")
    tag = release_tag()
    files = release_assets(r.names, r.system, r.machine, b.backend, r.cuda)
    if command and b.backend != "metal":
        out.append(f"{command} engine install downloads this build, checks it and installs it.")
    if files:
        out.append(f"Download from https://github.com/ggml-org/llama.cpp/releases/tag/{tag} (the version CARL is "
                   "tested with), and unpack it into one folder:")
        out += [f"  {f}" for f in files]
        out.append("Then set CARL_LLAMA_DIR to that folder.")
    elif b.backend != "metal":
        out.append(f"The release {tag} of llama.cpp (https://github.com/ggml-org/llama.cpp/releases/tag/{tag}) has "
                   f"the builds. Take the {b.backend} build for {r.system} {r.machine}.")
    if r.exe:
        out.append(f"llama-server: {r.exe}: {r.version}.")
        out += [f"  it sees {d.backend}{d.index}: {d.name}, {d.total / gib:.1f} GiB." for d in r.devices]
        if not r.devices:
            out.append("  it sees no GPU: it runs on the CPU only. Take the build above.")
    else:
        out.append("llama-server: not found (CARL_LLAMA_DIR, llama.path, PATH)." if command else
                   "llama-server: not found (CARL_LLAMA_DIR, PATH).")
    return out


def run(cli: Cli, argv: List[str]) -> int:
    print("\n".join(gpu_lines(cli.tools.gpu("--offline" in argv), "" if cli.os == "macos" else cli.words.command)))
    return 0
