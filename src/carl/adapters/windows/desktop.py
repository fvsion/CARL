"""This Windows computer's desktop (implements Desktop, contract C-034; W6.8): the clipboard (the Win32 clipboard as
Unicode text, through ctypes) and showing a file in File Explorer (`explorer /select,` on the file, its folder for a
folder). A session with no desktop (ssh, a service) has no clipboard to open: copy answers False, as the port says.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import time
from typing import Any, Callable, List, Optional

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
EXPLORER = "explorer"
TRIES = 5                                   # another program may hold the clipboard for a moment


def explorer_argv(path: str, isdir: Callable[[str], bool] = os.path.isdir) -> List[str]:
    """File Explorer on path: the folder itself, or the file's folder with the file selected."""
    full = os.path.abspath(path)
    return [EXPLORER, full] if isdir(full) else [EXPLORER, f"/select,{full}"]


def _user32_kernel32() -> Optional[Any]:
    try:
        u = ctypes.WinDLL("user32", use_last_error=True)                # type: ignore[attr-defined]
        k = ctypes.WinDLL("kernel32", use_last_error=True)              # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return None
    vp = ctypes.c_void_p
    u.OpenClipboard.argtypes = [vp]
    u.SetClipboardData.restype = vp
    u.SetClipboardData.argtypes = [ctypes.c_uint, vp]
    k.GlobalAlloc.restype = vp
    k.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    k.GlobalLock.restype = vp
    k.GlobalLock.argtypes = [vp]
    k.GlobalUnlock.argtypes = [vp]
    k.GlobalFree.argtypes = [vp]
    return u, k


def copy_to_clipboard(text: str) -> bool:
    """Put text on the clipboard as Unicode text; False when the clipboard cannot be opened or written."""
    found = _user32_kernel32()
    if found is None:
        return False
    u, k = found
    data = (text + "\0").encode("utf-16-le")
    for _ in range(TRIES):
        if u.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return False
    try:
        u.EmptyClipboard()
        mem = k.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not mem:
            return False
        ptr = k.GlobalLock(mem)
        if not ptr:
            k.GlobalFree(mem)
            return False
        ctypes.memmove(ptr, data, len(data))
        k.GlobalUnlock(mem)
        if not u.SetClipboardData(CF_UNICODETEXT, mem):
            k.GlobalFree(mem)                      # still ours when the clipboard did not take it
            return False
        return True
    finally:
        u.CloseClipboard()


def reveal_in_explorer(path: str) -> bool:
    """Show path in File Explorer; False if it could not start (explorer's exit code says nothing: it is 1 on
    success too)."""
    try:
        subprocess.Popen(explorer_argv(path), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except (OSError, ValueError):
        return False
    return True


class WindowsDesktop:
    """The clipboard and File Explorer."""

    def copy(self, text: str) -> bool:
        return copy_to_clipboard(text)

    def reveal(self, path: str) -> bool:
        return reveal_in_explorer(path)
