"""What drawing a screen tells the dashboard about it.

The renderers (dashboard/render) are pure: they read the UI state and the snapshot and return rows; they never write
the UI state. What the screen they drew means for the dashboard comes back in a Shown: the keys of the footer and of
the ? card, the sections that Tab goes through, whether the page goes on below, and the corrections of positions
(a scroll or a selected row kept inside its list). The app applies it once, before it draws the parts that depend on
it (the header, the footer, the ? card). Nothing here depends on another module of the dashboard.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, TypeVar

Key = Tuple[str, str]       # a key and what it does (the footer, the ? card)
T = TypeVar("T")


@dataclass
class Shown:
    """The outcome of drawing one screen."""
    keys: List[Key] = field(default_factory=list)          # the footer's keys
    keys_more: List[str] = field(default_factory=list)     # what the ? card adds
    sections: Optional[List[str]] = None                   # the sections of the screen, in order (None: not a page)
    more: bool = False                                     # the page goes on below the screen
    revealed: Optional[str] = None                         # the section the page scrolled to (None: unchanged)
    fixes: Dict[str, object] = field(default_factory=dict)  # UIState field -> its corrected value
    levels: List[str] = field(default_factory=list)        # sections of a plugin that need a level (simple at first)

    def fix(self, attr: str, value: T) -> T:
        """Record that UIState.<attr> should become value (a scroll or a row kept in range); the value comes back, so
        the renderer goes on with it."""
        self.fixes[attr] = value
        return value


def apply(ui: object, shown: Shown) -> None:
    """Make the UI state what the drawn screen says: its keys, sections and corrections. A screen that is no page
    (a dialog) leaves the sections of the page behind it."""
    setattr(ui, "keys", list(shown.keys))
    setattr(ui, "keys_more", list(shown.keys_more))
    setattr(ui, "more", shown.more)
    if shown.sections is not None:
        setattr(ui, "sections", list(shown.sections))
    if shown.revealed is not None:
        setattr(ui, "revealed", shown.revealed)
    for attr, value in shown.fixes.items():
        setattr(ui, attr, value)
    levels = getattr(ui, "levels", None)
    if isinstance(levels, dict):
        for name in shown.levels:
            levels.setdefault(name, 1)
