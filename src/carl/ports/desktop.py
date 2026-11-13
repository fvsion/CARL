"""The desktop: the clipboard and showing a file in the file manager."""
from __future__ import annotations

from typing import Protocol


class Desktop(Protocol):
    """The clipboard and the file manager. Contract C-034."""

    def copy(self, text: str) -> bool:
        """Put text on the clipboard; False when this computer has no clipboard tool."""
        ...

    def reveal(self, path: str) -> bool:
        """Show the file in the file manager; False when it can't."""
        ...
