"""The context memory type of a start on the CPU alone (decision A71). Pure.

On the CPU llama.cpp reads prompts much faster with an f16 context memory than with a quantized one (measured on a
Ryzen 9 3900X, a Qwen3 0.6B, llama-bench at 4K tokens: f16 267 tok/s, q8_0 103, q4_0 81), and a computer that runs
a model from its RAM usually has the RAM the larger type needs. The default q4_0 saves memory that a GPU may lack;
the CPU does not lack it. So a start on the CPU alone chooses the type for speed: f16 when the free RAM covers the
start's need with f16 and the load reserve (the rule of the load mode, fit.LoadMode), else q8_0 on the same test,
else q4_0. The start's window and slots stay as they are: only the type changes.

The types a person or Auto-tune chose are never changed: a flag, the environment, config.json, or an Auto-tune record
that measured the types (Auto-tune measures them on a CPU: domain/tune/scoring.py best_kv). The GPU backends and a
Mac keep today's type. The same test says which types fit, for Auto-tune's choice (covers).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict

from .gguf import GIB

CPU_KV = ("f16", "q8_0", "q4_0")      # the fastest to read prompts first, the smallest last
NAMES: Dict[str, str] = {"f16": "f16", "q8_0": "q8", "q4_0": "q4"}


def name(kv: str) -> str:
    """The type in the words of the start lines: f16, q8, q4."""
    return NAMES.get(kv, kv)


def _gib(n: float) -> str:
    return f"{n / GIB:.1f} GiB"


def covers(need: float, free: float, reserve: float) -> bool:
    """The free RAM covers a start that needs `need` bytes in the RAM and the load reserve (the test of fit.LoadMode)."""
    return need + reserve <= free


@dataclass(frozen=True)
class KvChoice:
    """The type of a CPU start and why. kv: the type; need: the bytes the start needs in the RAM with it; first, second:
    the need with f16 and with q8_0 (the types the rule tries first); free: the RAM it was judged against, basis: what
    that is in words (the free RAM, or the RAM less the reserve when the free RAM is not known); reserve: the load
    reserve (bytes); cap: what a model may use (the memory check; a type over it is not taken); fits: the RAM covers the
    start with kv (False for q4_0 when nothing is covered: the memory check then says)."""
    kv: str
    need: float
    first: float
    free: float
    reserve: float
    basis: str
    fits: bool
    second: float = 0.0
    cap: float = float("inf")

    def _refused(self, kv: str, need: float) -> str:
        """Why this type was not taken: the RAM does not cover it with the load reserve, else the memory check's cap."""
        gib = _gib
        if not covers(need, self.free, self.reserve):
            return f"does not cover {name(kv)}'s {gib(need)} and the load reserve of {gib(self.reserve)}"
        return f"covers {name(kv)}'s {gib(need)} and the load reserve, but {name(kv)} is over the {gib(self.cap)} a model may use"

    def short(self) -> str:
        """The same in a few words, for a line of router mode's presets."""
        if self.kv == CPU_KV[0]:
            return "for the CPU: it reads prompts faster than q8 or q4"
        if self.kv == CPU_KV[1]:
            return "for the CPU: f16 does not fit the RAM, and q8 reads faster than q4"
        return "for the CPU: the RAM covers no larger type"

    def why(self) -> str:
        """The start line's words, after "Context memory type: f16 (": for the CPU, and the numbers behind the choice."""
        gib = _gib
        have = f"the {self.basis}, {gib(self.free)},"
        reserve = f"the load reserve of {gib(self.reserve)}"
        if self.kv == CPU_KV[0]:
            return (f"for the CPU: {have} covers its {gib(self.need)} and {reserve}, and the CPU reads prompts faster "
                    f"with f16 than with q8 or q4")
        if self.kv == CPU_KV[1]:
            fastest = "the RAM covers" if not covers(self.first, self.free, self.reserve) else "that fits"
            return (f"for the CPU: {have} {self._refused(CPU_KV[0], self.first)}, so CARL takes q8, the "
                    f"fastest type {fastest} ({gib(self.need)})")
        if all(not covers(n, self.free, self.reserve) for n in (self.first, self.second)):
            return (f"for the CPU: {have} does not cover f16's {gib(self.first)} or q8 with {reserve}, so CARL keeps q4, "
                    f"the smallest ({gib(self.need)})")
        return (f"for the CPU: {have} {self._refused(CPU_KV[0], self.first)}, and {self._refused(CPU_KV[1], self.second)}, "
                f"so CARL keeps q4, the smallest ({gib(self.need)})")


def choose(need_of: Callable[[str], float], free: float, reserve: float, basis: str = "free RAM",
           cap: float = float("inf")) -> KvChoice:
    """The type of a start on the CPU alone: the first of CPU_KV whose need the free RAM covers with the load reserve
    (and that the memory check lets through: cap, what a model may use); q4_0 when none does. need_of(kv): the bytes the
    start needs in the RAM with that type."""
    first, second = need_of(CPU_KV[0]), need_of(CPU_KV[1])
    for kv in CPU_KV:
        need = need_of(kv)
        if covers(need, free, reserve) and need <= cap:
            return KvChoice(kv, need, first, free, reserve, basis, True, second, cap)
    last = CPU_KV[-1]
    return KvChoice(last, need_of(last), first, free, reserve, basis, False, second, cap)
