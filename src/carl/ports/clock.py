"""The date (the domain never reads the clock itself, R1)."""
from __future__ import annotations

from typing import Protocol


class Clock(Protocol):
    """The date. Contract C-009."""

    def today(self) -> str:
        """YYYY-MM-DD."""
        ...
