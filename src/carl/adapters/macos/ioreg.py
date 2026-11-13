"""This Mac's GPU from `ioreg -r -d 1 -c IOAccelerator`: its use and the memory it holds. parse_gpu is pure and takes
the command's text output; system_stats.py runs the command."""
from __future__ import annotations

import re
from typing import Optional, Tuple

ARGS = ["ioreg", "-r", "-d", "1", "-c", "IOAccelerator"]


def parse_gpu(text: str) -> Tuple[Optional[int], Optional[int]]:
    """(utilisation %, bytes in use) from `ioreg -r -d 1 -c IOAccelerator`."""
    g = re.search(r'"Device Utilization %"=(\d+)', text)
    gm = re.search(r'"In use system memory"=(\d+)', text)
    return (int(g.group(1)) if g else None), (int(gm.group(1)) if gm else None)
