"""The MODEL card: the loaded file, its speculation and (experts in the RAM) its pinned threads.
Pure: they render a ServerData snapshot and a View."""
from __future__ import annotations

import os
from typing import List

from ....domain.hardware.cpu_topology import has_pin, parse_mask, pinned_words
from ....domain.server.snapshot import ServerData, flag
from ....domain.shared.units import memory
from ....ui.fmt import (DIM, R, Card, CardLine, row)
from ....ui.words import (plural, spec_name)
from .memory import load_text
from .view import View

def card_model(v: View, d: ServerData) -> Card:
    """The loaded file and its speculation; at full the weights, the MTP drafter, the layers, the experts,
    thinking, the batch and the process."""
    shp, cmd = d.shape, d.cmd
    drafter = flag(cmd, "-md")
    L: List[CardLine] = []
    if shp:
        L.append(row("file", os.path.basename(v.model_path or "")))
        L.append(row("speculation", spec_name(flag(cmd, '--spec-type', default='none'), flag(cmd, '--spec-draft-n-max'))))
        threads = flag(cmd, "--threads", "-t")
        mask = parse_mask(flag(cmd, "--cpu-mask", "-C") or "")
        if threads and threads.isdigit() and has_pin(int(threads), mask):   # the start's pin (A57), not a typed -t
            L.append(row("threads", pinned_words(int(threads), mask)))
        if v.full:
            L.append("")
            L.append(row("weights", memory(v.model_size)))
            if drafter:
                L.append(row("MTP drafter", f"{os.path.basename(drafter)}, {memory(v.drafter_size)}"))
            L.append(row("layers", f"{shp['blocks']}: {shp['attn_layers']} attention, {shp['rec_layers']} recurrent"))
            L.append(row("MTP", "a separate drafter" if drafter else
                         (plural(shp['nextn'], "MTP layer") if shp["nextn"] else "none")))
            L.append(row("experts", f"{shp['experts_used']} of {shp['experts']} per token (MoE)"
                         if shp.get("experts") else "none (dense)"))
            L.append(row("thinking", "levels and off" if shp.get("effort_levels") else "on / off"))
            L.append(row("batch", flag(cmd, '-ub', default='–')))
            L.append(row("load mode", load_text(cmd, v.own_load)))       # A44, A45: which load mode and why
            L.append(row("flash attention", flag(cmd, '-fa', default='–')))
            L.append(row("architecture", shp["arch"]))
            L.append(row("process", f"pid {d.pid}"))
    else:
        L.append(f"{DIM}The model file is not known: {'an unknown model' if d.pid else 'no server process'}.{R}")
    quant = v.model_quant or (shp["ftype"] if shp else "")
    return Card("MODEL", quant, L)
