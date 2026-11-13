"""Text for the person at the terminal."""
from __future__ import annotations

from typing import Protocol


class Console(Protocol):
    """Progress and results for the person at the terminal. Contract C-010."""

    def info(self, text: str) -> None: ...

    def error(self, text: str) -> None: ...
