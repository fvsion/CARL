"""Keeps a Windows computer awake while the server runs (implements KeepAwake, contract C-021): a small child holds
SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED) for as long as the server's process runs, as caffeinate
-w does on a Mac. Windows keeps that state only while the thread that set it lives, so it is a process of its own,
with a hidden console and out of the caller's job like the server: a closed window or an ended ssh session leaves it
with the server. It writes a state file while it holds, ~/models/logs/.keep-awake-PID.json ({"pid": its pid,
"server": PID, "since": the local time}), and removes it when the server ends. `powercfg /requests` (an elevated
prompt) shows its request under SYSTEM.

  python -I keep_awake.py PID STATE_FILE

Run as a program it imports only the standard library.
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from typing import Callable, List, Optional

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
SYNCHRONIZE = 0x00100000
INFINITE = 0xFFFFFFFF
STATE = ".keep-awake-{pid}.json"
NO_HOLD = "note: CARL cannot keep this computer awake while the server runs. Turn off its sleep in the system settings."


def state_file(logs_dir: str, server_pid: int) -> str:
    """The state file of a hold for the server's pid, in the logs folder."""
    return os.path.join(logs_dir, STATE.format(pid=int(server_pid)))


def waiter_argv(python: str, server_pid: int, state: str) -> List[str]:
    """The holding child's command line: this file in isolated mode (-I: its folder is not on sys.path)."""
    return [python, "-I", os.path.abspath(__file__), str(int(server_pid)), state]


class WindowsKeepAwake:
    """logs_dir: where the state file goes (~/models/logs); python: the Python that runs the child; note: where the
    one note goes when the child cannot start."""

    def __init__(self, logs_dir: str, python: str = "", note: Optional[Callable[[str], None]] = None) -> None:
        self.logs_dir, self.python, self.note = logs_dir, python or sys.executable, note
        self.proc: Optional["subprocess.Popen[bytes]"] = None
        self.state = ""

    def hold(self, pid: int) -> None:
        from .process import popen                      # in the package only (the child runs this file alone)
        self.state = state_file(self.logs_dir, pid)
        try:
            os.makedirs(self.logs_dir, exist_ok=True)
            self.proc = popen(waiter_argv(self.python, pid, self.state), None, subprocess.DEVNULL,
                              subprocess.DEVNULL, subprocess.DEVNULL)
        except (OSError, ValueError):
            self.proc = None
            if self.note is not None:
                self.note(NO_HOLD)

    def release(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                pass
        self.proc = None
        if self.state:
            _remove(self.state)
            self.state = ""


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def write_state(path: str, server_pid: int) -> None:
    """The state file: this process holds the computer awake for server_pid (replaced whole)."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"pid": os.getpid(), "server": int(server_pid),
                            "since": time.strftime("%Y-%m-%dT%H:%M:%S")}, indent=2) + "\n")
    os.replace(tmp, path)


def main(argv: List[str]) -> int:
    """Hold the computer awake until process PID ends (at once when it cannot be opened)."""
    pid, state = int(argv[0]), argv[1]
    k = ctypes.WinDLL("kernel32", use_last_error=True)       # type: ignore[attr-defined]
    k.OpenProcess.restype = ctypes.c_void_p
    k.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    k.SetThreadExecutionState.restype = ctypes.c_uint32
    k.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
    h = k.OpenProcess(SYNCHRONIZE, False, pid)
    if not h:
        return 1
    try:
        if not k.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED):
            return 1
        try:
            write_state(state, pid)
        except OSError:
            pass
        k.WaitForSingleObject(h, INFINITE)
        return 0
    finally:
        k.SetThreadExecutionState(ES_CONTINUOUS)
        k.CloseHandle(h)
        _remove(state)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
