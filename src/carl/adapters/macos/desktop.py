"""This Mac's desktop: the clipboard (pbcopy) and showing a file in the Finder (open -R)."""
from __future__ import annotations

import subprocess


def copy_to_clipboard(text: str) -> bool:
    """Put text on the clipboard (pbcopy); False if that failed."""
    try:
        subprocess.run(["pbcopy"], input=text, text=True, encoding="utf-8", errors="replace", timeout=3, check=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def reveal_in_finder(path: str) -> bool:
    """Show path in the Finder (open -R); False if open could not run."""
    try:
        subprocess.Popen(["open", "-R", path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


class MacDesktop:
    """The clipboard and the Finder (implements Desktop, contract C-034)."""

    def copy(self, text: str) -> bool:
        return copy_to_clipboard(text)

    def reveal(self, path: str) -> bool:
        return reveal_in_finder(path)
