"""This Mac's memory from macOS commands: `vm_stat` (the page counters), `sysctl -n vm.swapusage` (the swap) and
`sysctl -n kern.memorystatus_vm_pressure_level` (the memory pressure). The parse_* functions are pure and take the
commands' text output; system_stats.py runs the commands."""
from __future__ import annotations

import re
from typing import Dict, Tuple

ARGS = ["vm_stat"]
SWAP_ARGS = ["sysctl", "-n", "vm.swapusage"]
PRESSURE_ARGS = ["sysctl", "-n", "kern.memorystatus_vm_pressure_level"]


def parse_vm_stat(text: str, page: int) -> Dict[str, int]:
    """vm_stat counters in bytes, by name ("Pages wired down", ...)."""
    vm = {}
    for line in text.splitlines():
        if ":" in line:
            key, v = line.split(":", 1)
            v = v.strip().rstrip(".")
            if v.isdigit():
                vm[key.strip()] = int(v) * page
    return vm


PAGE_SIZE = re.compile(r"page size of (\d+) bytes")
FREE_PAGES = ("Pages free", "Pages inactive", "Pages speculative")   # what macOS hands back at once


def available_bytes(text: str) -> int:
    """The memory free now from `vm_stat`: the free, inactive and speculative pages (0 when the text has none)."""
    m = PAGE_SIZE.search(text)
    vm = parse_vm_stat(text, int(m.group(1)) if m else 4096)
    return sum(vm.get(k, 0) for k in FREE_PAGES)


def parse_swap(text: str) -> Tuple[float, float]:
    """(used, total) bytes from `sysctl -n vm.swapusage`."""
    m = re.search(r"total = ([\d.]+)M\s+used = ([\d.]+)M", text)
    return (float(m.group(2)) * 2**20, float(m.group(1)) * 2**20) if m else (0, 0)


def pressure_name(level: str) -> str:
    """kern.memorystatus_vm_pressure_level as a word."""
    return {"1": "normal", "2": "WARNING", "4": "CRITICAL"}.get(level, level or "?")
