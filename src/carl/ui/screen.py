"""What the dashboard writes to a terminal that has nothing to do with the terminal's mode: the logo (an image on the
terminals that show images, an emoji otherwise) and the placing of the frame's lines. Pure; the terminal itself (cbreak
mode, the alternate screen, the mouse) is adapters/posix/terminal.py."""
from __future__ import annotations

import base64
from typing import List, Mapping, Optional

ERASE_LINE, ERASE_SCREEN = "\x1b[K", "\x1b[J"       # from the cursor to the end of the line / of the screen
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
    """Each line at its own position (the two header lines start right of the logo), the line's old text cleared before
    the new is written, and the rest of the screen cleared with the last line.

    The clear comes first, never after the text: a line that fills the last column leaves the cursor on that column
    (the terminal waits to wrap), and an erase-to-end sent there removes the last character on the terminals that keep
    the cursor on it (Windows Terminal, xterm, most Linux terminals; the Mac's Terminal and iTerm2 do not). The right
    edge of every full-width box went (decision A48). The last line takes the clear of the rest of the screen in the
    same place."""
    if not lines:
        return ERASE_SCREEN
    last = len(lines) - 1
    return "".join(f"\x1b[{i + 1};{(logo_cols if i < 2 else 0) + 1}H{ERASE_SCREEN if i == last else ERASE_LINE}{ln}"
                   for i, ln in enumerate(lines))
