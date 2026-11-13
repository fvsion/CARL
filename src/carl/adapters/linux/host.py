"""This Linux computer (implements HostInfo, contract C-015, and HostMemory, C-004): its RAM from /proc/meminfo and its
processor's name from /proc/cpuinfo, for the reports and the Auto-tune record, and the kernel's memory counters that
the dashboard's stats read (system_stats.py). The files are read as text and parsed by pure functions; a file that is
missing gives nothing, never an error.
"""
from __future__ import annotations

import os
from typing import Dict

PROC = "/proc"
CPU_KEYS = ("model name", "Model Name", "Hardware", "Model", "cpu model")      # x86, then arm and the others


def read(path: str) -> str:
    """The text of a file, "" when it can't be read."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def parse_meminfo(text: str) -> Dict[str, int]:
    """/proc/meminfo as bytes by name ("MemTotal", "MemAvailable", ...); a line with no unit (HugePages_Total) is a
    count."""
    out: Dict[str, int] = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        f = rest.split()
        if not f or not f[0].isdigit():
            continue
        out[name.strip()] = int(f[0]) * (1024 if len(f) > 1 and f[1].lower() == "kb" else 1)
    return out


def ram_bytes(proc: str = PROC) -> int:
    """Installed RAM (0 when unknown)."""
    return parse_meminfo(read(os.path.join(proc, "meminfo"))).get("MemTotal", 0)


def available_bytes(proc: str = PROC) -> int:
    """The RAM free now: MemAvailable (the kernel's estimate with the page cache it can drop; 0 when unknown)."""
    return parse_meminfo(read(os.path.join(proc, "meminfo"))).get("MemAvailable", 0)


def parse_cpu_name(text: str) -> str:
    """The processor's name from /proc/cpuinfo: the first line of CPU_KEYS ("" when there is none)."""
    for key in CPU_KEYS:
        for line in text.splitlines():
            name, _, value = line.partition(":")
            if name.strip() == key and value.strip():
                return " ".join(value.split())
    return ""


def machine_text(cpu: str, ram: int) -> str:
    """The processor and the RAM in words, as a Mac's record writes them ("AMD Ryzen 9 3900X 128 GB": the RAM in
    whole GiB, written GB)."""
    return f"{cpu} {ram // 2 ** 30} GB".strip() if cpu or ram else ""


def page_size() -> int:
    """The memory page size in bytes."""
    try:
        return int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, ValueError, OSError):
        return 4096


class LinuxHostInfo:
    """The RAM and the processor of this computer (implements HostInfo). proc: the folder of the kernel's files (a
    test gives a made-up one)."""

    def __init__(self, proc: str = PROC) -> None:
        self.proc = proc

    def ram_bytes(self) -> int:
        return ram_bytes(self.proc)

    def cpu_name(self) -> str:
        """The processor's name alone (Auto-tune's first line)."""
        return parse_cpu_name(read(os.path.join(self.proc, "cpuinfo")))

    def machine(self) -> str:
        return machine_text(self.cpu_name(), self.ram_bytes())


class LinuxHost:
    """The RAM of this computer (implements HostMemory). A Linux computer has no VM network of CARL's."""

    def __init__(self, proc: str = PROC) -> None:
        self.proc = proc

    def ram_bytes(self) -> int:
        return ram_bytes(self.proc)

    def vm_network_up(self) -> bool:
        return False

    def free_bytes(self) -> int:
        return available_bytes(self.proc)
