"""Keeps a Linux computer awake while the server runs (implements KeepAwake, contract C-021): systemd-inhibit holds a
sleep and idle lock for as long as a small shell waits for the server's pid. Without systemd-inhibit, or when it
cannot take a lock (no logind: a container, WSL without systemd), it does nothing and says so once.
"""
from __future__ import annotations

import shutil
import subprocess
from typing import Callable, Optional

from ..posix.process import own_group

INHIBIT = "systemd-inhibit"
WHO = "--who=CARL"                  # the lock's owner: the dashboard finds the locks that are CARL's by it
WAIT = 'while kill -0 "$1" 2>/dev/null; do sleep 5; done'
RELEASE = 5                         # seconds the lock's process has to end when it is asked to
SETTLE = 0.3                        # seconds: a lock that cannot be taken ends in this time
NO_INHIBIT = ("note: systemd-inhibit is not installed, so CARL cannot keep this computer awake while the server "
              "runs. Turn off its sleep in the system settings.")
NO_LOCK = ("note: systemd-inhibit could not take a lock{why}, so CARL cannot keep this computer awake while the "
           "server runs. Turn off its sleep in the system settings.")


class SystemdInhibit:
    """note: where the one note goes when there is no systemd-inhibit, or it cannot take a lock (stderr)."""

    def __init__(self, note: Callable[[str], None]) -> None:
        self.note = note
        self.proc: Optional["subprocess.Popen[bytes]"] = None

    def hold(self, pid: int) -> None:
        program = shutil.which(INHIBIT)
        if program is None:
            self.note(NO_INHIBIT)
            return
        try:
            self.proc = subprocess.Popen(
                [program, "--what=sleep:idle", WHO, "--why=The model server runs.", "--mode=block",
                 "sh", "-c", WAIT, "sh", str(pid)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, preexec_fn=own_group)
        except OSError:
            self.proc = None
            return
        try:
            code = self.proc.wait(SETTLE)
        except subprocess.TimeoutExpired:
            return                                              # still there: the lock is held
        reason = self._reason()
        self.proc = None
        if code != 0:                                           # (0: the server was gone already)
            self.note(NO_LOCK.format(why=f" ({reason})" if reason else ""))

    def _reason(self) -> str:
        """The first line systemd-inhibit wrote to stderr ("Failed to inhibit: Access denied"), and its pipe closed."""
        if self.proc is None or self.proc.stderr is None:
            return ""
        try:
            text = self.proc.stderr.read().decode("utf-8", errors="replace")
        except OSError:
            text = ""
        finally:
            self.proc.stderr.close()
        return next((line.strip() for line in text.splitlines() if line.strip()), "")

    def release(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(RELEASE)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if self.proc is not None and self.proc.stderr is not None:
            self.proc.stderr.close()
        self.proc = None
