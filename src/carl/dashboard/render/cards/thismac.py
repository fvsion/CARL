"""The card of the computer itself: THIS MAC, and THIS PC on Windows and Linux.
Pure: they render a ServerData snapshot and a View."""
from __future__ import annotations

from typing import List

from ....domain.server.snapshot import PcStats, ServerData
from ....domain.shared.units import file_size, memory, memory_pair
from ....ui.fmt import (DIM, GRN, R, RED, YEL, Card, CardLine, bar, row)
from ....ui.words import computer_cap
from .memory import gpu_label
from .view import View

HEAT = {"nominal": "normal", "fair": "warm", "serious": "hot", "critical": "very hot"}


def card_thismac(v: View, d: ServerData, w: int = 66) -> Card:
    """This Mac: its memory with a bar (as Activity Monitor's Memory Used), the memory pressure, swap, the GPU with a
    bar, power and heat; at full the parts of the memory, the cached files, the load and the free disk. Shown with or
    without a server. A PC's is card_this_pc."""
    if isinstance(d.system, PcStats):
        return card_this_pc(v, d, w)
    s = d.system
    bw = max(min(w - 38, 30), 8)
    pc = GRN if s.pressure == "normal" else YEL if s.pressure == "WARNING" else RED
    L: List[CardLine] = []
    if v.total_mem:
        L.append(row("memory", f"{bar(s.used / v.total_mem, bw)}  {memory_pair(s.used, v.total_mem)}"))
    L.append(row("pressure", f"{pc}{s.pressure.lower() if s.pressure != '?' else '–'}{R}"))
    su, st = s.swap
    L.append(row("swap", memory_pair(su, st) if st else f"{DIM}none (no swap file){R}"))
    if s.gpu is not None:
        L.append(row("GPU", f"{bar(s.gpu / 100, bw)}  {s.gpu}% busy"))
    else:
        L.append(row("GPU", "–"))
    batt = "battery" in (s.power or "").lower()
    L.append(row("power", f"{YEL}{s.power}{R}" if batt else (s.power or "–")))
    th = v.slow.thermal or ""
    hc = "" if th in ("", "nominal") else YEL if th == "fair" else RED
    L.append(row("heat", f"{hc}{HEAT.get(th, th or '–')}{R}" if th else "–"))
    if v.full:
        L.append("")
        L.append(row("app memory", f"{memory(s.app):>9}"))           # the three parts of the memory row
        L.append(row("wired", f"{memory(s.wired):>9}"))
        L.append(row("compressed", f"{memory(s.comp):>9}"))
        L.append(row("cached files", f"{memory(s.cached):>9}"))       # not in the memory row: macOS frees them
        L.append(row("free", f"{memory(s.free):>9}"))
        L.append(row("load", f"{s.load[0]:.1f}   {s.load[1]:.1f}   {s.load[2]:.1f}   {DIM}(1, 5, 15 min){R}"))
        if v.slow.disk:
            L.append(row("disk free", f"{file_size(v.slow.disk[0])} of {file_size(v.slow.disk[1])}"))
    if s.gpu is not None and s.gpu >= 90:
        summary = f"{RED}GPU {s.gpu}%{R}"
    elif s.pressure not in ("normal", "?"):
        summary = f"{pc}pressure {s.pressure.lower()}{R}"
    else:
        summary = f"pressure {s.pressure.lower()}" if s.pressure != "?" else ""
    return Card(computer_cap().upper(), summary, L)            # THIS MAC (THIS COMPUTER on Linux)


def card_this_pc(v: View, d: ServerData, w: int = 66) -> Card:
    """This PC (W6.8): its memory with a bar, the page file, the processor's use with a bar, each GPU's use (with its
    heat) and memory with bars, and the power; at full the available memory, the standby files and the free disk.
    Shown with or without a server."""
    s = d.system
    assert isinstance(s, PcStats)
    bw = max(min(w - 38, 30), 8)
    total = v.total_mem
    L: List[CardLine] = []
    if total:
        L.append(row("memory", f"{bar(s.used / total, bw)}  {memory_pair(s.used, total)}"))
    su, st = s.swap
    L.append(row("page file", memory_pair(su, st) if st else f"{DIM}none{R}"))
    L.append(row("CPU", f"{bar(s.cpu / 100, bw)}  {s.cpu:.0f}% busy" if s.cpu is not None else "–"))
    many = len(s.gpus) > 1
    for i, g in enumerate(s.gpus):
        lab = gpu_label(g, i, many)
        heat = f", {g.temp} °C" if g.temp is not None else ""
        if g.util is not None:
            L.append(row(lab, f"{bar(g.util / 100, bw)}  {g.util}% busy{heat}"))
        else:
            L.append(row(lab, g.name + heat))
        if g.used is not None and g.total:
            L.append(row(f"{lab} memory", f"{bar(g.used / g.total, bw)}  {memory_pair(g.used, g.total)}"))
    if not s.gpus:
        L.append(row("GPU", "–"))
    batt = "battery" in (s.power or "").lower()
    L.append(row("power", f"{YEL}{s.power}{R}" if batt else (s.power or "–")))
    if v.full:
        L.append("")
        for i, g in enumerate(s.gpus):
            L.append(row(gpu_label(g, i, many), g.name))
        L.append(row("available", f"{memory(s.available):>9}"))
        L.append(row("standby files", f"{memory(s.files):>9}"))
        if v.slow.disk:
            L.append(row("disk free", f"{file_size(v.slow.disk[0])} of {file_size(v.slow.disk[1])}"))
    busy = max((g.util for g in s.gpus if g.util is not None), default=None)
    if busy is not None and busy >= 90:
        summary = f"{RED}GPU {busy}%{R}"
    elif total and s.used / total >= 0.9:
        summary = f"{YEL}memory {s.used / total * 100:.0f}%{R}"
    else:
        summary = f"CPU {s.cpu:.0f}%" if s.cpu is not None else ""
    return Card(computer_cap().upper(), summary, L)
