"""Starting, waiting for and stopping the processes CARL owns on macOS and Linux (implements ProcessControl, contract
C-019), and the bits of POSIX process state a start sets up.

A stop asks the process's group to end (SIGTERM to the group: the server and its log filter), waits, then ends it
(SIGKILL) after the timeout. CARL stops only processes it started: the callers pass the pids they got from spawn,
or the server pid of a start they own.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from typing import Dict, Mapping, Optional, Sequence

from ...domain.shared.contracts import require

POLL = 0.05
DEFAULT_SIGNALS = ("SIGPIPE", "SIGXFSZ")          # Python ignores them; an ignored signal stays ignored across exec


def default_signals() -> None:
    """The signals Python ignores back to their default before an exec: llama-server and the launchers' pipes need
    SIGPIPE to end a writer whose reader went away."""
    for name in DEFAULT_SIGNALS:
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, signal.SIG_DFL)


def own_group() -> None:
    """In the child, before exec: a process group of its own, deaf to a closed terminal (the launcher's `set -m`
    and nohup). The terminal's session stays its session."""
    os.setpgid(0, 0)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)


def date_text(fmt: str) -> str:
    """The time as `date +FMT` gives it: the clock the bash launcher read for the log's name and remote.json, so a
    start's files are the same as its (the goldens pin that clock). The local time when date cannot run."""
    try:
        p = subprocess.run(["date", "+" + fmt], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", timeout=10)
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return time.strftime(fmt, time.localtime())


class PosixProcesses:
    """spawn, wait and stop on macOS and Linux."""

    def __init__(self) -> None:
        self.children: Dict[int, "subprocess.Popen[bytes]"] = {}

    def spawn(self, argv: Sequence[str], env: Mapping[str, str], log_path: str, new_session: bool) -> int:
        with open(log_path, "ab") as log:
            p = subprocess.Popen(list(argv), env=dict(env), stdin=subprocess.DEVNULL, stdout=log,
                                 stderr=subprocess.STDOUT, start_new_session=new_session)
        self.children[p.pid] = p
        return p.pid

    def wait(self, pid: int, timeout: float) -> Optional[int]:
        require(pid > 1, "C-019", "the pid of a process CARL started")
        child = self.children.get(pid)
        if child is not None:
            try:
                return child.wait(timeout)
            except subprocess.TimeoutExpired:
                return None
        end = time.monotonic() + timeout
        while True:
            try:
                done, status = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                return None if self._alive(pid) else 0       # not ours to reap: gone or still there
            if done:
                return os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else status >> 8
            if time.monotonic() >= end:
                return None
            time.sleep(POLL)

    def stop(self, pid: int, timeout: float) -> bool:
        require(pid > 1, "C-019", "the pid of a process CARL started")      # 0 and -1 would signal CARL's group, all
        if not self._signal(pid, signal.SIGTERM):
            return True
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self._gone(pid):
                return True
            time.sleep(POLL)
        self._signal(pid, signal.SIGKILL)
        end = time.monotonic() + 5
        while time.monotonic() < end:
            if self._gone(pid):
                return True
            time.sleep(POLL)
        return self._gone(pid)

    def _signal(self, pid: int, sig: int) -> bool:
        """sig to the process's group when it leads one, else to the process; False when it is gone."""
        try:
            if os.getpgid(pid) == pid:
                os.killpg(pid, sig)
            else:
                os.kill(pid, sig)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return False

    def _gone(self, pid: int) -> bool:
        child = self.children.get(pid)
        if child is not None:
            return child.poll() is not None
        try:
            done, _ = os.waitpid(pid, os.WNOHANG)
            if done:
                return True
        except ChildProcessError:
            pass
        return not self._alive(pid)

    @staticmethod
    def _alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
