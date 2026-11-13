"""The MEMORY card: a Mac's (unified memory) and a PC's (RAM and every GPU).
Pure: they render a ServerData snapshot and a View."""
from __future__ import annotations

from typing import List, Tuple

from ....domain.server.cmdline import CPU_MOE_FLAGS
from ....domain.server.snapshot import GpuUse, PcStats, ServerData, flag
from ....domain.shared.units import memory, memory_pair
from ....ui.fmt import (DIM, R, YEL, Card, CardLine, bar, row)
from ....ui.words import plural
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


def card_memory_pc(v: View, d: ServerData, w: int = 66) -> Card:
    """A PC's server memory (W6.8): the server in the RAM and on each GPU, each with a bar, the memory kept free on each
    GPU, the MoE experts kept in the RAM (--n-cpu-moe), then the model's parts; at full each GPU's memory in use by all
    apps, the GPU memory limit and the server's CPU."""
    s = d.system
    assert isinstance(s, PcStats)
    kv = kv_info(d)
    bw = max(min(w - 40, 30), 8)
    ram = d.rss or 0
    gpus, many = s.gpus, len(s.gpus) > 1
    vram = sum(s.server.values())
    L: List[CardLine] = []
    if not d.pid:
        L.append(f"{DIM}No server process.{R}")
    else:
        L.append(row("server RAM", f"{bar(ram / v.total_mem if v.total_mem else 0, bw)}  "
                                   f"{memory_pair(ram, v.total_mem) if v.total_mem else memory(ram)}"))
        for i, g in enumerate(gpus):
            own = s.server.get(g.key, 0) if g.key else 0
            L.append(row(f"server, {gpu_label(g, i, many)}", f"{bar(own / g.total if g.total else 0, bw)}  "
                                                              f"{memory_pair(own, g.total)}"))
    if gpus and v.vram_keep is not None:
        L.append(row("kept free", f"{memory(v.vram_keep)} on {'each GPU' if many else 'the GPU'}"))
    n = flag(d.cmd, *CPU_MOE_FLAGS)
    if n and n.isdigit() and int(n):
        L.append(row("experts in RAM", f"{plural(int(n), 'layer')}   {DIM}{CPU_MOE_FLAGS[0]} {n}{R}"))
    if d.pid:
        if not d.up:
            L.append(row("model", f"{YEL}loading{R}"))
        elif d.router is not None and not d.alias:
            L.append(row("model", f"{DIM}none loaded{R}"))
        else:
            L.append(row("model", f"{memory(v.model_size):>9}" if v.model_size else f"{'–':>9}"))
            if v.drafter_size:
                L.append(row("drafter", f"{memory(v.drafter_size):>9}"))
            L.append(row("context memory", f"{memory((kv.kv + kv.rs) if kv else 0):>9}"))
    if v.full:
        L.append("")
        for i, g in enumerate(gpus):
            if g.used is not None:
                L.append(row(f"{gpu_label(g, i, many)}, all apps", memory(g.used)))
        if v.gpu_limit:
            L.append(row("GPU limit", f"{memory(v.gpu_limit[0])}   {DIM}{v.gpu_limit[1]}{R}"))
        L.append(row("server CPU", f"{d.cpu:.0f}%"))
    summary = f"RAM {memory(ram)}" + (f", GPU {memory(vram)}" if vram else "") if d.pid and ram else ""
    return Card("MEMORY", summary, L)
