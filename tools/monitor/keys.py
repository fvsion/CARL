"""Terminal input: SGR mouse reports and escape sequences that arrive split across reads. Pure."""
from __future__ import annotations

import re
from typing import Callable, List, NamedTuple, Tuple

MOUSE = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])")      # SGR (1006) report: button;column;row, M press / m release
ESCAPE = re.compile(r"\x1b\[[0-9;]*[A-Za-z~]")              # a CSI key sequence (arrows, PgUp, Home, ...)
_UNFINISHED = re.compile(r"\x1b(\[[<0-9;]*)?$")             # an escape sequence cut off at the end of a read
WHEEL_UP, WHEEL_DOWN, LEFT = 64, 65, 0

UP, DOWN, RIGHT, LEFTKEY = "\x1b[A", "\x1b[B", "\x1b[C", "\x1b[D"
PGUP, PGDN, END, END_ALT = "\x1b[5~", "\x1b[6~", "\x1b[F", "\x1b[4~"
ENTER = ("\r", "\n")
ESC = "\x1b"
BACKSPACE = "\x7f\x08"
SCROLL_KEYS = {UP: 1, DOWN: -1, PGUP: 10, PGDN: -10}       # scroll steps (> 0: up)
PANEL_PASSTHROUGH = ("q", "Q", "\x03", "\t")       # keys a Settings panel leaves to the app: quit, Ctrl-C, Tab


class Click(NamedTuple):
    """A mouse button press: button code, 1-based column and row."""
    button: int
    x: int
    y: int


def split_mouse(data: str) -> Tuple[List[Click], str]:
    """The button presses in data (releases are dropped), and data without any mouse report."""
    clicks = [Click(int(m.group(1)), int(m.group(2)), int(m.group(3))) for m in MOUSE.finditer(data) if m.group(4) == "M"]
    return clicks, MOUSE.sub("", data)


def strip_escapes(data: str) -> str:
    """data without CSI key sequences: what is left are plain key presses."""
    return ESCAPE.sub("", data)


class InputBuffer:
    """Joins input read in pieces. A mouse report or key sequence can arrive split across
    reads: an unfinished escape sequence at the end is kept for the next read instead of
    its tail ("5M", "12;40m") being read as key presses. A lone Esc (nothing follows it)
    is passed on."""

    MAX_PENDING = 32        # a longer "unfinished" tail is garbage, not a sequence

    def __init__(self) -> None:
        self.pending = ""

    def feed(self, chunk: str, more_coming: Callable[[], bool]) -> str:
        """The complete input so far. more_coming() tells whether more bytes arrive at once
        (it is only asked when the input ends in an unfinished sequence)."""
        buf = self.pending + chunk
        m = _UNFINISHED.search(buf)
        if m and len(buf) - m.start() < self.MAX_PENDING:
            data, self.pending = buf[:m.start()], buf[m.start():]
            if not more_coming():
                data, self.pending = data + self.pending, ""
            return data
        self.pending = ""
        return buf
