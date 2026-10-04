"""The terminal: cbreak mode, the alternate screen, SGR mouse reporting, and the CARL logo
on terminals that show images. Mouse reporting is always turned off again on exit
(atexit, the main loop's finally, SIGTERM / SIGHUP), so the user's shell never gets it."""
from __future__ import annotations

import atexit
import base64
import os
import signal
import sys
import termios
import tty
from types import FrameType
from typing import Callable, List, Mapping, Optional

ENTER = "\x1b[?1049h\x1b[?25l\x1b[?1000h\x1b[?1006h"     # alt screen, hide cursor, mouse (SGR)
LEAVE = "\x1b[?1000l\x1b[?1006l\x1b[?25h\x1b[?1049l"
LOGO_COLS = 5           # columns kept free for the logo (4 + a gap)


def logo_mode(env: Mapping[str, str]) -> Optional[str]:
    """The image protocol for the logo (4 columns x 2 rows, top left): iTerm2 (its inline-image
    escape) or Ghostty / WezTerm / kitty (the kitty graphics protocol); None = an emoji.
    CARL_LOGO=0 turns it off; CARL_LOGO=iterm|kitty forces a protocol."""
    want = env.get("CARL_LOGO", "")
    if want in ("0", "off"):
        return None
    if want in ("iterm", "kitty"):
        return want
    if env.get("TERM_PROGRAM") == "iTerm.app" or env.get("LC_TERMINAL") == "iTerm2":
        return "iterm"
    if env.get("TERM_PROGRAM") in ("ghostty", "WezTerm") or env.get("TERM") == "xterm-kitty":
        return "kitty"
    return None


def logo_escape(mode: str, png: bytes) -> str:
    """The escape sequence that draws png at the top left."""
    data = base64.b64encode(png).decode()
    if mode == "iterm":
        return (f"\x1b[1;1H\x1b]1337;File=inline=1;width=4;height=2;preserveAspectRatio=1;"
                f"size={len(png)}:{data}\x07")
    chunks = [data[i:i + 4096] for i in range(0, len(data), 4096)]
    out = "\x1b_Ga=d,d=A,q=2\x1b\\\x1b[1;1H"          # kitty: delete the old copy, then place it again
    for n, c in enumerate(chunks):
        keys = "a=T,f=100,c=4,r=2,C=1,q=2," if n == 0 else ""
        out += f"\x1b_G{keys}m={1 if n < len(chunks) - 1 else 0};{c}\x1b\\"
    return out


def place_lines(lines: List[str], logo_cols: int) -> str:
    """Each line at its own position (the two header lines start right of the logo), each
    cleared to its end, and the rest of the screen cleared."""
    return "".join(f"\x1b[{i + 1};{(logo_cols if i < 2 else 0) + 1}H{ln}\x1b[K" for i, ln in enumerate(lines)) + "\x1b[J"


class Terminal:
    """The interactive terminal on stdin / stdout."""

    def __init__(self) -> None:
        self.fd = sys.stdin.fileno()
        self.saved = termios.tcgetattr(self.fd)

    def write(self, text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()

    def restore(self) -> None:
        """Mouse off, cursor on, main screen, the saved tty mode. Safe to call more than once."""
        self.write(LEAVE)
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)

    def enter(self, on_interrupt: Callable[[], None]) -> None:
        """cbreak mode, alternate screen, mouse on. Ctrl-C calls on_interrupt; SIGTERM / SIGHUP
        restore the terminal and exit at once."""
        def interrupted(_signum: int, _frame: Optional[FrameType]) -> None:
            on_interrupt()

        def terminated(_signum: int, _frame: Optional[FrameType]) -> None:
            self.restore()
            os._exit(0)

        signal.signal(signal.SIGINT, interrupted)
        for sig in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, terminated)
        atexit.register(self.restore)          # never leave mouse reporting on in the user's shell
        tty.setcbreak(self.fd)
        sys.stdout.write(ENTER)

    def read(self) -> str:
        return os.read(self.fd, 4096).decode(errors="replace")
