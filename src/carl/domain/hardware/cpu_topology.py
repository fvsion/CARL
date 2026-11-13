"""The CPU's cores and threads, and the threads pinned one per physical core (decision A54; MoE design section 10).

With the MoE experts of some layers in the RAM, the CPU runs them, and llama-server's threads decide the speed. One
thread on each physical core, pinned (llama.cpp's --cpu-mask with the first logical processor of each core), was 14%
faster than the default on a Ryzen 9 3900X for the A3B IQ3 (30.3 against 26.5 tok/s) and 12% for Flash-Next, and lost
nothing for the K-quant and Gemma models. The speed is flat from 8 to 16 threads and falls at 24 (two threads on a
core). --cpu-strict gained nothing (29.7 against 30.3 tok/s), so the plan leaves it out.

The adapters read the CPU (Windows: GetLogicalProcessorInformationEx; Linux: /sys/devices/system/cpu/*/topology) and give
a CpuTopology; this module is the one place that turns it into a thread count and a mask. Pure.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from ..shared.contracts import ensure

MASK_BITS = 512                 # llama.cpp's most threads (GGML_MAX_N_THREADS): a logical processor above it has no bit
MIN_THREADS = 2                 # one core, or none known, has nothing to pin
PINNED_WORDS = "one per core (pinned)"
OPT_THREADS = ("-t", "--threads", "-tb", "--threads-batch")
OPT_MASK = ("-C", "--cpu-mask", "-Cr", "--cpu-range", "-Cb", "--cpu-mask-batch", "-Crb", "--cpu-range-batch")


@dataclass(frozen=True)
class LogicalCpu:
    """One logical processor. index: llama.cpp's number for it (its bit in --cpu-mask); core: the physical core it
    belongs to (the logical processors with the same core are the threads of one core; an id unique on the computer);
    rank: how fast its core is against the others (a P-core above an E-core; 0 on a CPU with one kind of core)."""
    index: int
    core: int
    rank: int = 0


@dataclass(frozen=True)
class CpuTopology:
    """The logical processors CARL may run on, in any order. Empty when the computer cannot say."""
    cpus: Tuple[LogicalCpu, ...] = ()

    @property
    def logical(self) -> int:
        return len(self.cpus)

    @property
    def cores(self) -> int:
        return len({c.core for c in self.cpus})


@dataclass(frozen=True)
class ThreadPin:
    """The threads of a pinned start: one per physical core, on the core's first logical processor. cores: the
    physical cores in the machine (all kinds); the threads are only those of the fastest kind (a hybrid CPU's
    P-cores), and logical counts every logical processor in the machine."""
    threads: int
    mask: int
    cores: int
    logical: int

    def mask_text(self) -> str:
        """The mask as --cpu-mask takes it: 0x555555 for the first logical processors of 12 cores of 2 threads."""
        return hex(self.mask)

    def words(self) -> str:
        """12, one per core (pinned): the MODEL card's and the start line's words after the label."""
        return f"{self.threads}, {PINNED_WORDS}"

    @property
    def hybrid(self) -> bool:
        """The CPU has cores of two speeds, and the threads sit on the faster ones only."""
        return self.threads < self.cores


def checked(topology: CpuTopology) -> CpuTopology:
    """The answer of a CpuLayout (contract C-058): a topology whose logical processors have a number of 0 or more, each
    once. A programming error in an adapter is a ContractError."""
    ensure(isinstance(topology, CpuTopology) and all(c.index >= 0 for c in topology.cpus)
           and len({c.index for c in topology.cpus}) == len(topology.cpus), "C-058",
           "the CPU layout gives each logical processor once, with a number of 0 or more")
    return topology


def pin_for(topology: CpuTopology) -> Optional[ThreadPin]:
    """The pin of this CPU, or None when there is nothing to pin (no topology, fewer than two cores).

    One thread for each physical core, on the core's first logical processor (its lowest number). On a CPU with
    cores of two speeds (Intel's P and E cores) the threads sit on the faster cores only: the threads of a layer wait
    for the slowest one, so a slow core holds the fast ones back (not measured; the same idea as one thread a core)."""
    usable = [c for c in topology.cpus if 0 <= c.index < MASK_BITS]
    if not usable:
        return None
    top = max(c.rank for c in usable)
    first: Dict[int, int] = {}
    for c in usable:
        if c.rank == top and (c.core not in first or c.index < first[c.core]):
            first[c.core] = c.index
    if len(first) < MIN_THREADS:
        return None
    mask = 0
    for index in first.values():
        mask |= 1 << index
    return ThreadPin(len(first), mask, topology.cores, topology.logical)


def typed_options(extra: Sequence[str]) -> str:
    """The thread or CPU-mask option in llama-server's extra options, as typed ("-t 8", "--cpu-mask=0xff"; the first
    one found), "" when there is none. The user's own win: the start passes none of its own."""
    for i, a in enumerate(extra):
        name, eq, val = a.partition("=")
        if name in OPT_THREADS + OPT_MASK:
            return a if eq or i + 1 >= len(extra) else f"{a} {extra[i + 1]}"
    return ""


def pin_options(pin: ThreadPin) -> List[str]:
    """llama-server's arguments for the pin: the threads and the mask (no --cpu-strict: it gained nothing)."""
    return ["--threads", str(pin.threads), "--cpu-mask", pin.mask_text()]


def has_pin(threads: int, mask: int) -> bool:
    """A running server's threads are the start's pin: a mask with one bit for each thread (--cpu-mask with --threads
    as the start passes them)."""
    return bool(mask) and bin(mask).count("1") == threads


def pinned_words(threads: int, mask: int) -> str:
    """The words for a running server's threads: "12, one per core (pinned)" when the mask has one bit for each thread
    (has_pin); "12" with threads alone."""
    return f"{threads}, {PINNED_WORDS}" if has_pin(threads, mask) else str(threads)


def parse_mask(text: str) -> int:
    """A --cpu-mask value (hex, with or without 0x) as a number; 0 for a value that is not one."""
    try:
        return int(text, 16)
    except ValueError:
        return 0
