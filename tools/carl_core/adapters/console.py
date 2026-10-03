"""Terminal output (implements Console and Progress)."""
from __future__ import annotations

import sys


class StdConsole:
    """Results on stdout, problems on stderr; flushed so a log follower sees them at once."""

    def info(self, text: str) -> None:
        print(text, flush=True)

    def error(self, text: str) -> None:
        print(text, file=sys.stderr, flush=True)


class StepPrinter:
    """Auto-tune progress in the format the monitor parses: "STEP i/N text" and "  note"."""

    def step(self, text: str) -> None:
        print(f"STEP {text}", flush=True)

    def note(self, text: str) -> None:
        print(f"  {text}", flush=True)
