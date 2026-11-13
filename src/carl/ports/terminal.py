"""The terminal the dashboard draws on."""
from __future__ import annotations

from typing import Callable, Optional, Protocol, Tuple


class Terminal(Protocol):
    """Raw keys in, text out, and the size. Contract C-032."""

    def size(self) -> Tuple[int, int]:
        """(columns, rows)."""
        ...

    def enter(self, on_interrupt: Callable[[], None]) -> None:
        """Raw keys, the alternate screen and mouse reports. Ctrl-C calls on_interrupt (and wakes read_key); the
        signals that end the process give the terminal back first."""
        ...

    def leave(self) -> None:
        """The terminal as it was before enter(). Safe to call more than once, and from a signal handler."""
        ...

    def read_key(self, timeout: float) -> Optional[str]:
        """What was typed since the last call (one key or more, as it came in), None after timeout or after
        wake()."""
        ...

    def wake(self) -> None:
        """Make a waiting read_key() return (from another thread or a signal handler)."""
        ...

    def write(self, text: str) -> None: ...
