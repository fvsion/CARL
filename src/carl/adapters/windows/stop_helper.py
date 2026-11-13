"""The Windows stop helper: Ctrl-C to the console of a server CARL started, so llama-server's own handler ends it
cleanly (the stop design of plan 5.2, confirmed on the Windows test PC with b11429 and the 0.6B on 2026-10-26: it ends in about
0.2 s with exit code 0). It runs as a small program of its own, because it gives up its console:

  python stop_helper.py PID

  1. FreeConsole: leave its own (hidden) console;
  2. AttachConsole(PID): join the server's console (exit 2 when it cannot: the server is gone or has none);
  3. SetConsoleCtrlHandler(NULL, TRUE): the helper itself ignores the Ctrl-C it is about to send;
  4. GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0): Ctrl-C to every process of that console (exit 3 when it fails).

send_ctrl_c runs it in a child Python with a hidden console. Run as a program it imports only the standard library.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys

CTRL_C_EVENT = 0
CREATE_NO_WINDOW = 0x08000000
TIMEOUT = 15                                  # seconds for the helper
NOT_ATTACHED, NOT_SENT = 2, 3


def helper_argv(pid: int, python: str = "") -> list:
    """The helper's command line: this file, run by python (default: this Python) in isolated mode (-I: its folder,
    which holds secrets.py, is not on sys.path)."""
    return [python or sys.executable, "-I", os.path.abspath(__file__), str(int(pid))]


def send_ctrl_c(pid: int) -> bool:
    """Ctrl-C to the console of process pid, through the helper; True when the helper sent it."""
    try:
        r = subprocess.run(helper_argv(pid), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return r.returncode == 0


def main(argv: list) -> int:
    pid = int(argv[0])
    k = ctypes.WinDLL("kernel32", use_last_error=True)       # type: ignore[attr-defined]
    k.FreeConsole()
    if not k.AttachConsole(ctypes.c_uint32(pid)):
        return NOT_ATTACHED
    k.SetConsoleCtrlHandler(None, True)
    sent = k.GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0)
    k.FreeConsole()
    return 0 if sent else NOT_SENT


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
