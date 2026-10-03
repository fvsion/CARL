#!/usr/bin/env python3
"""Screenshots and GIFs of terminal programs (the CARL dashboard, OpenCode) for
the README. Runs CMD in a virtual terminal (pyte), plays a script of keys and
waits, and draws chosen frames with Pillow in a dark theme.

  uv run --with pillow --with pyte tools/tuishot.py SCRIPT.json OUT_DIR -- CMD...

SCRIPT: a list of steps, each [wait_seconds, action, arg...]:
  ["key", "5"]          send text or a named key (enter esc tab up down left right)
  ["click", "TEXT"]     left-click the first place where TEXT shows on screen
  ["wait_for", "TEXT", timeout_s]
  ["png", "name"]       save the screen as OUT_DIR/name.png
  ["frame", "gifname"]  add the screen to OUT_DIR/gifname.gif (written at the end)
  ["frames", "gifname", n, every_s]   n frames, one every every_s seconds
Environment: COLS, ROWS (default 132 x 40), FONT_SIZE (default 15), TITLE (window title),
GIF_MS (frame time), TUISHOT_LOGO=PNG (draw that image in the top-left 4 x 2 cells, where
iTerm2 shows the CARL logo; pyte cannot show images).
"""
from __future__ import annotations

import fcntl
import json
import os
import pty
import select
import signal
import struct
import sys
import termios
import time
from typing import Any

import pyte
from PIL import Image, ImageDraw, ImageFont

RGB = tuple[int, int, int]
Font = ImageFont.FreeTypeFont | ImageFont.ImageFont

COLS, ROWS = int(os.environ.get("COLS", 132)), int(os.environ.get("ROWS", 40))
FONT_SIZE = int(os.environ.get("FONT_SIZE", 15))
FONT_DIRS = [os.path.expanduser("~/Library/Fonts"), "/Library/Fonts", "/System/Library/Fonts"]

# A dark theme (Catppuccin Mocha-like): background, foreground, dim text, 16 ANSI colours.
BG: RGB = (30, 30, 46)
FG: RGB = (205, 214, 244)
DIM_FG: RGB = (127, 132, 156)
ANSI: dict[str, RGB] = {"black": (69, 71, 90), "red": (243, 139, 168), "green": (166, 227, 161), "brown": (249, 226, 175),
        "yellow": (249, 226, 175), "blue": (137, 180, 250), "magenta": (245, 194, 231), "cyan": (148, 226, 213),
        "white": (186, 194, 222), "brightblack": (88, 91, 112), "brightred": (243, 139, 168),
        "brightgreen": (166, 227, 161), "brightyellow": (249, 226, 175), "brightblue": (137, 180, 250),
        "brightmagenta": (245, 194, 231), "brightcyan": (148, 226, 213), "brightwhite": (166, 173, 200)}
KEYS: dict[str, str] = {"enter": "\r", "esc": "\x1b", "tab": "\t", "up": "\x1b[A", "down": "\x1b[B", "right": "\x1b[C",
        "left": "\x1b[D", "ctrl-c": "\x03"}


def font(names: list[str]) -> Font:
    for d in FONT_DIRS:
        for n in names:
            p = os.path.join(d, n)
            if os.path.exists(p):
                return ImageFont.truetype(p, FONT_SIZE)
    return ImageFont.load_default()


REG = font(["FiraCode-Regular.ttf", "Menlo.ttc"])
BOLD = font(["FiraCode-Bold.ttf", "FiraCode-SemiBold.ttf", "Menlo.ttc"])
FALLBACKS = [font(["Menlo.ttc"]), font(["Apple Symbols.ttf"]), font(["STIXTwoMath.otf", "Monaco.ttf"])]
CW: int = int(round(REG.getlength("M")))
CH = int(FONT_SIZE * 1.45)


class DimScreen(pyte.Screen):
    """pyte drops SGR 2 (dim); keep it as 'italics', which these programs do not use."""
    def select_graphic_rendition(self, *attrs: int, private: bool = False) -> None:
        out: list[int] = []
        for a in attrs:
            out += [3] if a == 2 else [22, 23] if a == 22 else [a]
        super().select_graphic_rendition(*out)


def colour(name: str, default: RGB) -> RGB:
    """A pyte colour (a name, or 6 hex digits) as RGB."""
    if name == "default":
        return default
    if name in ANSI:
        return ANSI[name]
    if len(name) != 6:
        return default
    try:
        return (int(name[0:2], 16), int(name[2:4], 16), int(name[4:6], 16))
    except ValueError:
        return default


_NOTDEF: dict[Font, bytes] = {}


def has_glyph(f: Font, ch: str) -> bool:
    """False when f draws ch as its 'missing glyph' box (compared with a code point no font has)."""
    if ch == " ":
        return True
    try:
        if f not in _NOTDEF:
            _NOTDEF[f] = bytes(f.getmask("\U0010fffd"))
        m = f.getmask(ch)
        return m.getbbox() is not None and bytes(m) != _NOTDEF[f]
    except Exception:                       # PIL raises various errors for unrenderable glyphs
        return False


def pick_font(f: Font, ch: str) -> Font | None:
    """The first font that has ch, or None (then the cell stays empty instead of a box)."""
    if has_glyph(f, ch):
        return f
    return next((g for g in FALLBACKS if has_glyph(g, ch)), None)


def dim(c: RGB) -> RGB:
    """Dim text: c 62% of the way from the background."""
    return (int(c[0] * 0.62 + BG[0] * 0.38), int(c[1] * 0.62 + BG[1] * 0.38), int(c[2] * 0.62 + BG[2] * 0.38))


def render(screen: pyte.Screen, title: str) -> Image.Image:
    pad, bar = 18, 34
    w, h = COLS * CW + 2 * pad, ROWS * CH + 2 * pad + bar
    img = Image.new("RGB", (w, h), (17, 17, 27))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=12, fill=BG)
    for i, c in enumerate([(243, 139, 168), (249, 226, 175), (166, 227, 161)]):
        d.ellipse([16 + i * 22, 12, 28 + i * 22, 24], fill=c)
    d.text((w // 2, 18), title, fill=DIM_FG, font=REG, anchor="mm")
    for y in range(ROWS):
        line = screen.buffer[y]
        for x in range(COLS):
            ch = line[x]
            fg, bg = colour(ch.fg, FG), colour(ch.bg, BG)
            if ch.reverse:
                fg, bg = bg, fg
            if ch.italics and not ch.reverse:
                fg = dim(fg)
            px, py = pad + x * CW, pad + bar + y * CH
            if bg != BG:
                d.rectangle([px, py, px + CW, py + CH], fill=bg)
            if ch.data.strip():
                f = pick_font(BOLD if ch.bold else REG, ch.data)
                if f is None:
                    continue
                d.text((px, py + (CH - FONT_SIZE) // 2), ch.data, fill=fg, font=f)
    logo = os.environ.get("TUISHOT_LOGO")        # where the program shows an inline image (iTerm2): draw it
    if logo and os.path.exists(logo):
        ic = Image.open(logo).convert("RGBA")
        side = min(4 * CW, 2 * CH)
        ic = ic.resize((side, side), Image.Resampling.LANCZOS)
        img.paste(ic, (pad + (4 * CW - side) // 2, pad + bar), ic)
    return img


def main() -> None:
    if "--" not in sys.argv or sys.argv.index("--") != 3 or len(sys.argv) < 5 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0 if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help") else 2)
    i = sys.argv.index("--")
    with open(sys.argv[1], encoding="utf-8") as f:
        steps: list[list[Any]] = json.load(f)
    out = sys.argv[2]
    cmd = sys.argv[i + 1:]
    os.makedirs(out, exist_ok=True)
    pid, fd = pty.fork()
    if pid == 0:
        os.environ.update(TERM="xterm-256color", COLUMNS=str(COLS), LINES=str(ROWS), COLORTERM="truecolor")
        os.execvp(cmd[0], cmd)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
    screen = DimScreen(COLS, ROWS)
    stream = pyte.ByteStream(screen)
    title = os.environ.get("TITLE", os.path.basename(cmd[0]))
    gifs: dict[str, list[Image.Image]] = {}

    def pump(t: float) -> None:
        end = time.time() + t
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.05)
            if r:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    return
                if not data:
                    return
                stream.feed(data)

    def find(text: str) -> tuple[int, int] | None:
        for y, line in enumerate(screen.display):
            x = line.find(text)
            if x >= 0:
                return x + 1, y + 1
        return None

    for step in steps:
        wait, action, rest = step[0], step[1], step[2:]
        pump(wait)
        if action == "key":
            os.write(fd, KEYS.get(rest[0], rest[0]).encode())
        elif action == "click":
            hit = find(rest[0])
            print(f"click {rest[0]!r}: {hit or 'NOT FOUND'}", flush=True)
            if hit:
                os.write(fd, f"\x1b[<0;{hit[0]};{hit[1]}M\x1b[<0;{hit[0]};{hit[1]}m".encode())
        elif action == "wait_for":
            t0 = time.time()
            while not find(rest[0]) and time.time() - t0 < (rest[1] if len(rest) > 1 else 120):
                pump(0.5)
            print(f"wait_for {rest[0]!r}: {'found' if find(rest[0]) else 'TIMEOUT'} after {time.time() - t0:.0f}s", flush=True)
        elif action == "png":
            render(screen, title).save(os.path.join(out, rest[0] + ".png"), optimize=True)
            print(f"png {rest[0]}", flush=True)
        elif action == "frame":
            gifs.setdefault(rest[0], []).append(render(screen, title))
        elif action == "frames":
            for n in range(rest[1]):
                gifs.setdefault(rest[0], []).append(render(screen, title))
                pump(rest[2])
    for name, frames in gifs.items():
        small = [f.convert("P", palette=Image.Palette.ADAPTIVE, colors=128) for f in frames]
        small[0].save(os.path.join(out, name + ".gif"), save_all=True, append_images=small[1:],
                      duration=int(os.environ.get("GIF_MS", 900)), loop=0, optimize=True)
        print(f"gif {name}: {len(frames)} frames", flush=True)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


if __name__ == "__main__":
    main()
