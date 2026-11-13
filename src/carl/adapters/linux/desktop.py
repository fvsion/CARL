"""This Linux computer's desktop (implements Desktop, contract C-034): the clipboard (wl-copy on Wayland, else xclip,
else xsel) and showing a file in the file manager (xdg-open on the file's folder; the freedesktop tools cannot select
a file). A computer with none of the tools (a server, WSL without WSLg) answers False, as the port says.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
from typing import Callable, List, Optional, Sequence

COPY_TOOLS = (("wl-copy",), ("xclip", "-selection", "clipboard"), ("xsel", "--clipboard", "--input"))
OPEN = "xdg-open"
TIMEOUT = 3


def copy_to_clipboard(text: str, tools: Sequence[Sequence[str]] = COPY_TOOLS,
                      which: Callable[[str], Optional[str]] = shutil.which) -> bool:
    """Put text on the clipboard with the first tool that is installed and works (wl-copy fails without a Wayland
    display, then X's tools are tried); False when none did. The tools keep the selection alive in a process of
    their own, so their output is not a pipe we wait on."""
    for tool in tools:
        program = which(tool[0])
        if not program:
            continue
        try:
            subprocess.run([program, *tool[1:]], input=text.encode("utf-8"), stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=TIMEOUT, check=True)
            return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False


def folder_of(path: str) -> str:
    """The folder to show for path: a folder itself, a file's folder."""
    return path if os.path.isdir(path) else (os.path.dirname(os.path.abspath(path)) or ".")


def reveal_in_files(path: str, which: Callable[[str], Optional[str]] = shutil.which) -> bool:
    """Show path's folder in the file manager (xdg-open); False if xdg-open is missing or could not start."""
    program = which(OPEN)
    if not program:
        return False
    args: List[str] = [program, folder_of(path)]
    try:
        proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
    except OSError:
        return False
    threading.Thread(target=proc.wait, daemon=True).start()           # reaped when it ends: no zombie in a long dashboard
    return True


class LinuxDesktop:
    """The clipboard and the file manager."""

    def copy(self, text: str) -> bool:
        return copy_to_clipboard(text)

    def reveal(self, path: str) -> bool:
        return reveal_in_files(path)
