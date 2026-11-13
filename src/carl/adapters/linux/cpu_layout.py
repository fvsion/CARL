"""This Linux computer's logical processors and physical cores (implements CpuLayout, contract C-058): the kernel's
topology files under /sys/devices/system/cpu, parsed by pure functions. A file that is missing gives nothing, never an
error.

  cpuN/topology/thread_siblings_list   the logical processors of the same core ("0,12", "0-1"): a core is its lowest
  cpuN/topology/core_id, physical_package_id   used only when that file is missing
  cpuN/online                          0: the processor is off (cpu0 has no file)
  /sys/devices/cpu_atom/cpus           Intel hybrid: the E-cores (the rest are P-cores)
  cpuN/cpu_capacity                    Arm and others: the core's speed; a core below 85% of the best is a slower kind

Only the processors this process may run on count (sched_getaffinity: a container's or taskset's CPUs).
"""
from __future__ import annotations

import os
from typing import Callable, Dict, Optional, Set

from ...domain.hardware.cpu_topology import CpuTopology, LogicalCpu

SYSTEM = "/sys/devices/system/cpu"
ATOM = "/sys/devices/cpu_atom/cpus"
CAPACITY_SHARE = 0.85               # a core with at least this share of the best core's capacity is of the same kind
PACKAGE_STEP = 1 << 16              # a core id made from the package and the core id: above any processor number


def read(path: str) -> str:
    """The text of a file, "" when it can't be read."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def parse_cpu_list(text: str) -> Set[int]:
    """A kernel CPU list ("0-3,8,10-11") as a set of numbers; what is not a number or a range is skipped."""
    out: Set[int] = set()
    for part in text.replace("\n", ",").split(","):
        lo, _, hi = part.strip().partition("-")
        if lo.isdigit() and (not hi or hi.isdigit()):
            out.update(range(int(lo), int(hi or lo) + 1))
    return out


def own_cpus() -> Optional[Set[int]]:
    """The CPUs this process may run on (None: the OS cannot say)."""
    try:
        return set(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return None


def rank_of(n: int, small: Set[int], capacity: Dict[int, int]) -> int:
    """How fast logical processor n's core is: 1 for the fast kind, 0 for the slow kind. Intel hybrid: an E-core (in
    `small`) is 0 and a P-core 1. Else a core with at least CAPACITY_SHARE of the best capacity is 1 and a slower one 0.
    Without either source every core is 0 (one kind)."""
    if small:
        return 0 if n in small else 1
    if not capacity:
        return 0
    return 1 if capacity.get(n, 0) >= CAPACITY_SHARE * max(capacity.values()) else 0


class LinuxCpuLayout:
    """CpuLayout over /sys. system, atom: the files (tests give a made-up tree); allowed: the CPUs the process may use."""

    def __init__(self, system: str = SYSTEM, atom: str = ATOM,
                 allowed: Callable[[], Optional[Set[int]]] = own_cpus) -> None:
        self.system, self.atom, self.allowed = system, atom, allowed

    def _numbers(self) -> Set[int]:
        try:
            names = os.listdir(self.system)
        except OSError:
            return set()
        return {int(n[3:]) for n in names if n.startswith("cpu") and n[3:].isdigit()}

    def _core(self, n: int) -> int:
        """The core of logical processor n: the lowest of its siblings, else its package and core id, else itself."""
        base = os.path.join(self.system, f"cpu{n}", "topology")
        siblings = parse_cpu_list(read(os.path.join(base, "thread_siblings_list")))
        if siblings:
            return min(siblings)
        core, package = read(os.path.join(base, "core_id")), read(os.path.join(base, "physical_package_id"))
        if core.isdigit() and package.isdigit():
            return PACKAGE_STEP * (1 + int(package)) + int(core)
        return n

    def topology(self) -> CpuTopology:
        allowed = self.allowed()
        numbers = sorted(n for n in self._numbers() if allowed is None or n in allowed)
        online = [n for n in numbers if n == 0 or read(os.path.join(self.system, f"cpu{n}", "online")) != "0"]
        small = parse_cpu_list(read(self.atom))
        capacity = {n: int(c) for n in online
                    for c in [read(os.path.join(self.system, f"cpu{n}", "cpu_capacity"))] if c.isdigit()}
        return CpuTopology(tuple(LogicalCpu(n, self._core(n), rank_of(n, small, capacity)) for n in online))
