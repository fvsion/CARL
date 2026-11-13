"""The terminal: cbreak mode, the alternate screen and SGR mouse reporting (the logo and the placing of the lines are
ui/screen.py). Mouse reporting is always turned off again on exit (atexit, the main loop's finally, SIGTERM / SIGHUP),
so the user's shell never gets it.

Every write goes straight to the file descriptor (os.write), never through sys.stdout's buffer: a SIGTERM that
lands while a frame is written runs its handler between two writes, and the handler's own write of the leave
sequence cannot enter the buffer a second time ("reentrant call inside BufferedWriter", found in W0.2)."""
from __future__ import annotations

import atexit
import os
import select
import shutil
import signal
import sys
import termios
import tty
from types import FrameType
from typing import Callable, Optional, Tuple

ENTER = "\x1b[?1049h\x1b[?25l\x1b[?1000h\x1b[?1006h"     # alt screen, hide cursor, mouse (SGR)
LEAVE = "\x1b[?1000l\x1b[?1006l\x1b[?25h\x1b[?1049l"
DEFAULT_SIZE = (120, 36)                                # when the size can't be read


class Terminal:
    """The interactive terminal on stdin / stdout (implements Terminal, contract C-032). A self-pipe wakes a
    waiting read_key. exit ends the process after SIGTERM / SIGHUP (a test gives its own)."""

    def __init__(self, fd_in: Optional[int] = None, fd_out: Optional[int] = None,
                 exit: Callable[[int], None] = os._exit) -> None:
        self.fd = sys.stdin.fileno() if fd_in is None else fd_in
        self.out = sys.stdout.fileno() if fd_out is None else fd_out
        self.saved = termios.tcgetattr(self.fd)
        self.exit = exit
        self._wake_r, self._wake_w = os.pipe()
        for fd in (self._wake_r, self._wake_w):
            os.set_blocking(fd, False)

    def size(self) -> Tuple[int, int]:
        cols, rows = shutil.get_terminal_size(DEFAULT_SIZE)
        return cols, rows

    def write(self, text: str) -> None:
        data = memoryview(text.encode("utf-8", "replace"))
        while data:
            data = data[os.write(self.out, data):]

    def leave(self) -> None:
        """Mouse off, cursor on, main screen, the saved tty mode. Safe to call more than once."""
        self.write(LEAVE)
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)

    def enter(self, on_interrupt: Callable[[], None]) -> None:
        """cbreak mode, alternate screen, mouse on. Ctrl-C calls on_interrupt and wakes read_key; SIGTERM / SIGHUP
        restore the terminal and exit at once."""
        def interrupted(_signum: int, _frame: Optional[FrameType]) -> None:
            on_interrupt()
            self.wake()

        signal.signal(signal.SIGINT, interrupted)
        for sig in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, self.terminated)
        atexit.register(self.leave)            # never leave mouse reporting on in the user's shell
        tty.setcbreak(self.fd)
        self.write(ENTER)

    def terminated(self, _signum: int, _frame: Optional[FrameType]) -> None:
        """SIGTERM / SIGHUP: the terminal given back, then the process ends (no atexit, no buffered output)."""
        self.leave()
        self.exit(0)

    def read_key(self, timeout: float) -> Optional[str]:
        ready, _, _ = select.select([self.fd, self._wake_r], [], [], max(timeout, 0.0))
        if self._wake_r in ready:
            try:
                os.read(self._wake_r, 64)
            except BlockingIOError:
                pass
        if self.fd in ready:
            return os.read(self.fd, 4096).decode(errors="replace")
        return None

    def wake(self) -> None:
        try:
            os.write(self._wake_w, b"x")
        except BlockingIOError:                 # the pipe is full: read_key wakes anyway
            pass
