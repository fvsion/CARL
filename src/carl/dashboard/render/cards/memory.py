"""The MEMORY card: a Mac's (unified memory) and a PC's (RAM and every GPU).
Pure: they render a ServerData snapshot and a View."""
from __future__ import annotations

from typing import List, Tuple

from ....domain.memory import loaded, offload
from ....domain.memory.calibration import CUDA, Calibration, for_devices
from ....domain.memory.fit import RESERVE_GB
from ....domain.server.cmdline import CPU_MOE_FLAGS
from ....domain.server.snapshot import GpuUse, PcStats, ServerData, flag
from ....domain.shared.units import GIB, memory, memory_pair
from ....ui.fmt import (DIM, R, YEL, Card, CardLine, bar, row)
from ....ui.words import os_and_apps, os_label, plural
from .slots import kv_info
from .view import View

def server_memory(v: View, d: ServerData) -> Tuple[int, int]:
    """(total, own) bytes of the server. own: its physical footprint (the Metal buffers are in it), its RSS when the
    footprint can't be read. total: own + the mapped model and drafter files (not in the footprint) once a model is
    loaded. With --no-mmap or --load-mode none (A44) the weights are in the footprint already."""
    own = d.footprint or d.rss or 0
    loaded = d.up and not (d.router is not None and not d.alias)
    mapped = (v.model_size or 0) + v.drafter_size if own and loaded and not read_in(d.cmd) else 0
    return own + mapped, own


def load_option(cmd: str) -> Tuple[str, str]:
    """(the load mode, the option as written) of the server's command line: the last of --no-mmap, --mmap and
    --load-mode X counts, as llama-server reads them; ("", "") for none of them (llama.cpp's mapped load)."""
    words, mode, given = cmd.split(), "", ""
    for i, a in enumerate(words):
        if a in ("--no-mmap", "--mmap"):
            mode, given = ("none" if a == "--no-mmap" else "mmap"), a
        elif a == "--load-mode" and i + 1 < len(words):
            mode, given = words[i + 1], f"{a} {words[i + 1]}"
        elif a.startswith("--load-mode="):
            mode, given = a.split("=", 1)[1], a
    return mode, given


def read_in(cmd: str) -> bool:
    """The server read its weights into its own memory, not mapped from the file: --no-mmap, or --load-mode none (the
    last load option counts, as llama-server reads them)."""
    return load_option(cmd)[0] == "none"


def load_text(cmd: str, own: str = "") -> str:
    """The load mode of the running server and why, in one row (A44, A45): carl start passes --load-mode none when the
    free RAM covers the server's RAM and the load reserve, and nothing (the mapped load) otherwise. own: the user's
    own load option in the server options (llama.extra_args; View.own_load): then that is the reason."""
    mode, given = load_option(cmd)
    if given and (own or given != "--load-mode none"):
        where = "into memory" if mode == "none" else "mapped from the file" if mode == "mmap" else given
        return f"{where} ({given}): your server options"
    if given:
        return "into memory (--load-mode none): the free RAM held it and the load reserve"
    return "mapped from the file: the free RAM did not hold it and the load reserve, or was not known"


def card_memory(v: View, d: ServerData, w: int = 66) -> Card:
    """The server's memory: the total with a bar, then its parts (they add up to the total); at full the server
    process alone, the GPU memory limit, the GPU memory of all apps and the server's CPU. A PC's is card_memory_pc."""
    if isinstance(d.system, PcStats):
        return card_memory_pc(v, d, w)
    kv = kv_info(d)
    s = d.system
    total, own = server_memory(v, d)
    weights = v.model_size or 0
    ctx = (kv.kv + kv.rs) if kv else 0
    L: List[CardLine] = []
    if total:
        bw = max(min(w - 38, 30), 8)
        share = total / v.total_mem if v.total_mem else 0
        L.append(row("server", f"{bar(share, bw)}  {memory_pair(total, v.total_mem)}"))
        if not d.up:                                     # the model loads: no parts yet (not "model 0 KiB")
            L.append(row("model", f"{YEL}loading{R}"))
        elif d.router is not None and not d.alias:       # a router with no model loaded
            L.append(row("model", f"{DIM}none loaded{R}"))
        else:
            L.append(row("model", f"{memory(weights):>9}" if weights else f"{'–':>9}"))   # (size not known)
            if v.drafter_size:
                L.append(row("drafter", f"{memory(v.drafter_size):>9}"))
            L.append(row("context memory", f"{memory(ctx):>9}"))
            L.append(row("buffers", f"{memory(max(total - weights - v.drafter_size - ctx, 0)):>9}"))
    else:
        L.append(f"{DIM}No server process.{R}")
    if v.full:
        L.append("")
        if d.footprint and total != own:                 # the process without the mapped files (Activity Monitor's)
            L.append(row("server process", memory(own)))
        if v.gpu_limit:
            L.append(row("GPU limit", memory(v.gpu_limit[0]) + ("" if "Metal" in v.gpu_limit[1] else
                                                                  f"   {DIM}{v.gpu_limit[1]}{R}")))
        if s.gpumem:
            L.append(row("GPU, all apps", memory(s.gpumem)))
        L.append(row("server CPU", f"{d.cpu:.0f}%"))
    return Card("MEMORY", f"server {memory(total)}" if total else "", L)


def gpu_label(g: GpuUse, i: int, many: bool) -> str:
    """A GPU's row label: GPU, or GPU 0, GPU 1 when there are several (CARL's numbers, as carl gpu lists them)."""
    return f"GPU {i}" if many else "GPU"


def model_split(d: ServerData, file: int) -> Tuple[int, int, int, int]:
    """(layers with experts that loaded, the experts' bytes in the RAM, the bytes of the model in the RAM, the bytes of
    the MTP head that did not load) of the running server, from its header and its --n-cpu-moe: the same parts as
    Settings' plan (offload.ram_side less the CPU buffer; loaded.head_bytes, A52). (0, 0, 0, 0) without experts in the
    RAM or a header. file: the model file's size."""
    n = flag(d.cmd, *CPU_MOE_FLAGS)
    if not d.shape or not n or not n.isdigit() or not int(n):
        return 0, 0, 0, 0
    mtp = "draft-mtp" in (flag(d.cmd, "--spec-type") or "").split(",")
    calib = server_calib(d)
    shape = loaded.trimmed(d.shape, mtp, calib)                                # the head loads only with MTP (A52)
    experts = offload.experts_of(shape)
    k = min(int(n), len(experts))
    embd = float(shape.get("embd_bytes", 0) or 0) if calib.gpu_weights else 0.0
    head = loaded.head_bytes(d.shape, float(file), mtp, calib) if file else 0.0
    return len(experts), int(sum(experts[:k])), int(sum(experts[:k]) + embd + calib.ram_bytes(shape)), int(head)


def server_calib(d: ServerData) -> Calibration:
    """The calibration of the backend the server runs on: its -dev devices' (CUDA0, Vulkan0: calibration.for_devices),
    else CUDA (a start on an NVIDIA card names no device; the PCs whose cards the dashboard draws)."""
    devices = [x for x in (flag(d.cmd, "-dev", "--device") or "").split(",") if x.strip()]
    return for_devices(devices) if devices else CUDA


def card_memory_pc(v: View, d: ServerData, w: int = 66) -> Card:
    """A PC's server memory (W6.8; W1.6 A-M4): the server on each GPU of the part for a model there and in the RAM of
    the RAM less the part kept, each with a bar (the same parts Settings' MEMORY draws against); the MoE experts kept in
    the RAM (--n-cpu-moe, with the words Settings uses: 40 of 40 layers), the model split into its GPU part and its RAM
    part, the context memory; at full where the card's and the RAM's memory go (they add up), the flag and whose count
    it is (the start's record), and the server's CPU."""
    s = d.system
    assert isinstance(s, PcStats)
    kv = kv_info(d)
    bw = max(min(w - 40, 30), 8)
    ram = d.rss or 0
    gpus, many = s.gpus, len(s.gpus) > 1
    vram = sum(s.server.values())
    reserve = RESERVE_GB * GIB                         # the usual reserve (VM not counted), as Settings' MEMORY
    room_ram = max(v.total_mem - reserve, 0) if v.total_mem else 0
    limit = v.gpu_limit[0] if v.gpu_limit else 0
    layers, experts, in_ram, head = model_split(d, v.model_size or 0)
    who = {True: "auto", False: "your setting"}.get(d.start.get("cpu_moe_auto")) if isinstance(
        d.start.get("cpu_moe_auto"), bool) else ""
    L: List[CardLine] = []
    if not d.pid:
        L.append(f"{DIM}No server process.{R}")
    else:
        for i, g in enumerate(gpus):
            own = s.server.get(g.key, 0) if g.key else 0
            part = limit if limit and not many else g.total
            L.append(row(gpu_label(g, i, many), f"{bar(own / part if part else 0, bw)}   {memory_pair(own, part)}"))
        L.append(row("RAM", f"{bar(ram / room_ram if room_ram else 0, bw)}  "
                            f"{memory_pair(ram, room_ram) if room_ram else memory(ram)}"))
    n = flag(d.cmd, *CPU_MOE_FLAGS)
    if n and n.isdigit() and int(n):
        of = f" of {layers}" if layers else ""
        L.append(row("experts in RAM", f"{n}{of} layers" + (f", {memory(experts)}" if experts else "") +
                     (f"   {DIM}{who}{R}" if who and w >= 70 else "")))
    if d.pid:
        if not d.up:
            L.append(row("model", f"{YEL}loading{R}"))
        elif d.router is not None and not d.alias:
            L.append(row("model", f"{DIM}none loaded{R}"))
        else:
            L.append(row("model", f"{memory(v.model_size):>9}" if v.model_size else f"{'–':>9}"))
            if in_ram and v.model_size:
                wide = w >= 70
                L.append(row("  On the GPU", f"{memory(max(v.model_size - in_ram - head, 0)):>9}" +
                             (f"   {DIM}the model less the experts in the RAM{R}" if wide else "")))
                L.append(row("  In the RAM", f"{memory(in_ram):>9}" +
                             (f"   {DIM}the experts and the token embeddings{R}" if wide else "")))
                if head:
                    L.append(row("  Not loaded", f"{memory(head):>9}" +
                                 (f"   {DIM}the MTP layer: it loads only with MTP{R}" if wide else "")))
            if v.drafter_size:
                L.append(row("drafter", f"{memory(v.drafter_size):>9}"))
            L.append(row("context memory", f"{memory((kv.kv + kv.rs) if kv else 0):>9}" +
                         (f"   {DIM}on the GPU{R}" if in_ram and w >= 70 else "")))
    if v.full:
        L.append("")
        for i, g in enumerate(gpus):
            if g.used is None:
                continue
            own = s.server.get(g.key, 0) if g.key else 0
            label = "The GPU" if not many else f"GPU {i}"
            L.append(row(label, f"{memory(g.total):>9}" + (f"   {DIM}{g.name}{R}" if w >= 70 else "")))
            L.append(row("  In use", f"{memory(max(g.used - own, 0)):>9}   {DIM}not the server{R}"))
            if v.vram_keep is not None:
                L.append(row("  Kept free", f"{memory(v.vram_keep):>9}" +
                             (f"   {DIM}the setting GPU memory kept{R}" if w >= 70 else "")))
            if limit and not many:
                L.append(row("  For a model", f"{memory(limit):>9}   {DIM}the server {memory(own).replace(' GiB', '')}"
                                              + (f", free {memory(max(limit - own, 0)).replace(' GiB', '')}"
                                                 if w >= 70 else "") + f"{R}"))
        if v.total_mem:
            L.append(row("The RAM", f"{memory(v.total_mem):>9}"))
            L.append(row("  Kept", f"{memory(reserve):>9}   {DIM}for {os_and_apps() if w >= 70 else os_label()}"
                                   f"{R}" if gpus else f"{memory(reserve):>9}"))
            L.append(row("  For a model", f"{memory(room_ram):>9}   {DIM}the server {memory(ram).replace(' GiB', '')}"
                                          + (f" (free now: {memory(s.available)})" if s.available and w >= 70 else "")
                                          + f"{R}"))
        if n and n.isdigit() and int(n):
            plan = ": CARL's plan at the start" if who == "auto" and w >= 70 else ""
            L.append(row("flag", f"{CPU_MOE_FLAGS[0]} {n}" + (f"  ({who}{plan})" if who else "")))
        L.append(row("server CPU", f"{d.cpu:.0f}%"))
    summary = (f"GPU {memory(vram)}, RAM {memory(ram)}" if vram else f"RAM {memory(ram)}") if d.pid and ram else ""
    return Card("MEMORY", summary, L)
