"""Keeps this Mac awake while the server runs (implements KeepAwake, contract C-021): `caffeinate -i -w PID`, which
ends by itself with the process. A sleeping Mac freezes requests in the middle of a prompt (2026-09-25, sleep after
1 min on battery). caffeinate by its name, in a process group of its own and deaf to a closed terminal (the
launcher's `nohup caffeinate ... &`), so Ctrl-C in the dashboard does not end it.
"""
from __future__ import annotations

import os
import signal
import subprocess
from typing import Optional


def _own_group() -> None:
    os.setpgid(0, 0)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)


class Caffeinate:
    def __init__(self) -> None:
        self.proc: Optional["subprocess.Popen[bytes]"] = None

    def hold(self, pid: int) -> None:
        try:
            self.proc = subprocess.Popen(["caffeinate", "-i", "-w", str(pid)], stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                         preexec_fn=_own_group)
        except OSError:
            self.proc = None                 # no caffeinate: the server runs anyway

    def release(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None
