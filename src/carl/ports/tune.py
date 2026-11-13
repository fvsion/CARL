"""Ports of Auto-tune: the server it measures and its progress lines."""
from __future__ import annotations

from typing import Dict, Optional, Protocol, Sequence, Tuple

from ..domain.shared.constants import UBATCH
from ..domain.shared.types import JsonObject


class TuneServer(Protocol):
    """A llama-server Auto-tune starts once per speculation mode. Contract C-011."""

    def start(self, spec: str, n: int, ctx: int, ub: Optional[int] = None, slots: int = 1,
              pin: Optional[bool] = None, kv: Optional[str] = None) -> float:
        """Start (stopping a previous one) and wait until it serves; seconds it took. ub: its -ub (None: the start's
        own); slots: its slots (the batch size step starts at the setup every -ub keeps, A46); pin: the threads with
        experts in the RAM, True pinned one per core, False llama.cpp's own (None: the start's own rule, A57); kv: its
        context memory type for K and V, q4_0 f16 or q8_0 (None: q4_0; the CPU alone measures the types, A71)."""
        ...

    def load_mode(self) -> str:
        """The load mode the last start used: none, mapped, or "" when it did not say (A44)."""
        ...

    def stop(self) -> None: ...

    def timings(self, body: JsonObject, timeout: float) -> Dict[str, float]:
        """POST a chat completion; the numeric "timings" of the answer."""
        ...

    def count_tokens(self, text: str) -> Optional[int]:
        """Tokens in a text (None when the server can't tell)."""
        ...

    def parallel(self, counts: Sequence[int], prompt: int, gen: int, kv: str,
                 ub: int = UBATCH) -> Dict[int, Tuple[float, float]]:
        """With the server stopped: {n: (decode tok/s, prompt tok/s)} in total for n requests at once,
        each reading `prompt` tokens and writing `gen` (llama-batched-bench, with the batch size -ub);
        {} when it can't run."""
        ...


class Progress(Protocol):
    """Auto-tune's progress lines ("STEP i/N ..." and indented notes the monitor shows). Contract C-012."""

    def step(self, text: str) -> None: ...

    def note(self, text: str) -> None: ...
