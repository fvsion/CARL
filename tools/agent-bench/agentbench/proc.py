"""Child processes: start one in its own process group, read its lines with a time stamp, stop the whole group
(the client and everything it started: a background coder, an MCP server) by the group's PID."""
from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import time
from collections import deque
from typing import IO, Deque, List, Mapping, Optional, Sequence, Tuple

Line = Tuple[str, str, float]          # (stream: out | err | eof-out | eof-err, text, seconds since start)
_LIVE: "set[Child]" = set()            # the children that are not stopped yet (stop_all() on Ctrl-C)


class Child:
    """A child process in its own session (process group). lines() gives its stdout and stderr lines."""

    def __init__(self, argv: Sequence[str], cwd: str, env: Mapping[str, str], stdin: bool = False,
                 keep_err: int = 200) -> None:
        self.argv = list(argv)
        self.t0 = time.monotonic()
        self.proc = subprocess.Popen(self.argv, cwd=cwd, env=dict(env), stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
                                     text=True, encoding="utf-8", errors="replace", bufsize=1,
                                     start_new_session=True)
        self.pid = self.proc.pid
        _LIVE.add(self)
        self._q: "queue.Queue[Line]" = queue.Queue()
        self.err_tail: Deque[str] = deque(maxlen=keep_err)
        self._open = 2
        for name, stream in (("out", self.proc.stdout), ("err", self.proc.stderr)):
            assert stream is not None
            threading.Thread(target=self._pump, args=(name, stream), daemon=True).start()

    def elapsed(self) -> float:
        return time.monotonic() - self.t0

    def _pump(self, name: str, stream: IO[str]) -> None:
        try:
            for line in stream:
                if name == "err":
                    self.err_tail.append(line.rstrip("\n"))
                self._q.put((name, line, self.elapsed()))
        except (OSError, ValueError):
            pass
        self._q.put(("eof-" + name, "", self.elapsed()))

    def next_line(self, timeout: float) -> Optional[Line]:
        """The next line (or an end-of-stream mark), or None after timeout seconds."""
        try:
            item = self._q.get(timeout=timeout)
        except queue.Empty:
            return None
        if item[0].startswith("eof-"):
            self._open -= 1
        return item

    def streams_open(self) -> bool:
        return self._open > 0

    def send(self, text: str) -> None:
        """Write one line to the child's stdin."""
        if self.proc.stdin is None:
            raise RuntimeError("the child has no stdin")
        self.proc.stdin.write(text + "\n")
        self.proc.stdin.flush()

    def running(self) -> bool:
        return self.proc.poll() is None

    def stop(self, grace: float = 3.0) -> Optional[int]:
        """Stop the child's process group: SIGINT (a client ends as on Ctrl-C and frees its locks), then SIGTERM,
        then SIGKILL, grace seconds apart. The return code."""
        stop_group(self.pid, grace, (signal.SIGINT, signal.SIGTERM, signal.SIGKILL))
        _LIVE.discard(self)
        try:
            return self.proc.wait(timeout=grace + 2)
        except subprocess.TimeoutExpired:
            return None
        finally:
            for s in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                try:
                    if s is not None:
                        s.close()
                except OSError:
                    pass

    def err_text(self, lines: int = 30) -> str:
        return "\n".join(list(self.err_tail)[-lines:])


def stop_all() -> None:
    """Stop every child that is not stopped yet (the harness stops: Ctrl-C or SIGTERM)."""
    for child in list(_LIVE):
        child.stop(grace=1.0)


def stop_group(pid: int, grace: float = 3.0,
               signals: Sequence[signal.Signals] = (signal.SIGTERM, signal.SIGKILL)) -> None:
    """Stop the process group that the process pid leads (it was started with start_new_session): each signal
    in turn, grace seconds apart, until the group is gone."""
    if pid <= 1:
        raise ValueError("refusing to signal pid <= 1")
    for sig in signals:
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            return
        except PermissionError:
            return
        end = time.monotonic() + grace
        while time.monotonic() < end:
            if not group_alive(pid):
                return
            time.sleep(0.1)


def group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def run(argv: Sequence[str], cwd: str, env: Mapping[str, str], timeout: float) -> Tuple[int, str]:
    """Run a command to its end: (return code, stdout + stderr). Return code 124 on a timeout."""
    try:
        p = subprocess.run(list(argv), cwd=cwd, env=dict(env), capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, errors="replace")
    except subprocess.TimeoutExpired as e:
        out = e.stdout if isinstance(e.stdout, str) else ""
        return 124, out + f"\n(time limit: {timeout:.0f} s)"
    except OSError as e:
        return 127, str(e)
    return p.returncode, p.stdout + p.stderr


def tail(text: str, lines: int = 30) -> str:
    parts: List[str] = text.rstrip().splitlines()
    return "\n".join(parts[-lines:])
