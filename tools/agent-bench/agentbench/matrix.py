"""The matrix of runs: models x clients x thinking x runs x prompts, grouped by model (one server per model).
Pure."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Set

from .events import MEASURE
from .prompts import Prompt
from .results import Key

THINKING_DEFAULT = "default"
THINKING_OFF = "off"


@dataclass(frozen=True)
class Cell:
    mode: str           # decision | full
    model: str
    client: str
    thinking: str
    variant: str
    prompt: Prompt
    run: int
    measure: str = MEASURE

    def key(self) -> Key:
        return (self.mode, self.model, self.client, self.thinking, self.variant, self.prompt.id, self.run,
                self.measure)

    def label(self) -> str:
        return f"{self.model} {self.client} thinking={self.thinking} {self.prompt.id} run {self.run}"


def build(models: Sequence[str], clients: Sequence[str], thinking: Sequence[str], runs: int, variant: str,
          prompts: Sequence[Prompt], mode: str = "decision") -> List[Cell]:
    """Every cell, in the order they run: model, client, thinking, run, prompt."""
    if runs < 1:
        raise ValueError("runs must be 1 or more")
    return [Cell(mode, m, c, t, variant, p, r)
            for m in models for c in clients for t in thinking for r in range(1, runs + 1) for p in prompts]


def pending(cells: Iterable[Cell], done: Set[Key]) -> List[Cell]:
    """The cells that have no result yet."""
    return [c for c in cells if c.key() not in done]


def by_model(cells: Iterable[Cell]) -> Dict[str, List[Cell]]:
    """The cells of each model, in the models' first order."""
    out: Dict[str, List[Cell]] = {}
    for c in cells:
        out.setdefault(c.model, []).append(c)
    return out


def eta_seconds(done_seconds: Sequence[float], remaining: int) -> float:
    """The time left: the mean time of the runs so far times the runs left (0 when nothing ran yet)."""
    if not done_seconds or remaining <= 0:
        return 0.0
    return sum(done_seconds) / len(done_seconds) * remaining


def fmt_duration(seconds: float) -> str:
    s = int(round(seconds))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    return f"{s // 3600} h {(s % 3600) // 60:02d} min"
