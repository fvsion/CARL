"""Terminal text: ANSI colours, column-exact cutting and padding, bars, human-readable
numbers, and the bordered cards every tab is drawn from. Pure functions."""
from __future__ import annotations

import re
import textwrap
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, NamedTuple, Sequence, Union

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
R, DIM, B = "\x1b[0m", "\x1b[2m", "\x1b[1m"
GRN, YEL, RED, CYN, MAG = "\x1b[32m", "\x1b[33m", "\x1b[31m", "\x1b[36m", "\x1b[35m"
NA = f"{DIM}N/A{R}"     # a value that is not known yet: cards keep their height

Span = tuple[int, int, str]     # clickable columns of a row: (first, end exclusive, action), 0-based
Row = tuple[str, list[Span]]    # one screen row and its clickable parts


def cw(c: str) -> int:
    """Terminal columns of one character: 2 for wide ones (emoji, CJK)."""
    return 2 if unicodedata.east_asian_width(c) in "WF" else 1


def vlen(s: str) -> int:
    """Visible columns of a string that may hold colour codes."""
    return sum(cw(c) for c in ANSI.sub("", s))


def fit(s: str, w: int) -> str:
    """Cut a coloured string to w visible columns and pad it."""
    out: list[str] = []
    n = i = 0
    while i < len(s):
        m = ANSI.match(s, i)
        if m:
            out.append(m.group())
            i = m.end()
            continue
        c = cw(s[i])
        if n + c > w:
            break
        if n + c >= w and vlen(s[i:]) > c:
            out.append("…")
            n += 1
            break
        out.append(s[i])
        n += c
        i += 1
    return "".join(out) + R + " " * max(0, w - n)


def wrap(s: str, w: int) -> list[str]:
    """Hard-wrap the plain text of s every w characters (log lines)."""
    plain = ANSI.sub("", s)
    return [plain[i:i + w] for i in range(0, max(len(plain), 1), w)]


def wwrap(text: str | None, w: int) -> list[str]:
    """Word-wrap the plain text of text to w columns (at least 10)."""
    return textwrap.wrap(ANSI.sub("", text or ""), max(w, 10)) or [""]


def cwrap(text: str | None, w: int, indent: str = "") -> list[str]:
    """Word-wrap text that may hold colour codes to w visible columns (at least 10), keeping
    the colours: a line ends with a reset and the next starts with the codes still active.
    Runs of spaces are kept inside a line (padded labels stay aligned) and dropped where a
    line breaks. Lines after the first begin with indent (plain spaces, counted in w); a
    word longer than a line is split; newlines start a new line."""
    w = max(w, 10)
    out: list[str] = []
    active = ""                                   # SGR codes in effect (since the last reset)
    for para in (text or "").split("\n"):
        line, used, fresh = "", 0, True           # fresh: nothing but the indent on this line yet
        gap, broke = "", False                    # broke: this line comes from a wrap (no leading gap)

        def flush() -> None:
            nonlocal line, used, fresh, broke
            out.append(line + (R if ANSI.search(line) else ""))
            line, used, fresh, broke = indent + active, vlen(indent), True, True

        for tok in re.split(r"( +)", para):
            if not tok:
                continue
            if tok.startswith(" ") and not ANSI.sub("", tok).strip():
                if fresh and broke:
                    continue
                gap = tok
                continue
            wv = vlen(tok)
            if not fresh and used + len(gap) + wv > w and wv <= w - vlen(indent):
                flush()                           # (a word too long for any line is split where it is)
                gap = ""
            line, used = line + gap, used + len(gap)
            gap = ""
            i = 0
            while i < len(tok):                   # copy the word, splitting it if it is wider than a line
                m = ANSI.match(tok, i)
                if m:
                    code = m.group()
                    active = "" if code == R else active + code
                    line += code
                    i = m.end()
                    continue
                c = cw(tok[i])
                if used + c > w and not fresh:
                    flush()
                line, used, fresh = line + tok[i], used + c, False
                i += 1
        out.append(line + (R if ANSI.search(line) else ""))
    return out or [""]


def heading(title: str, w: int) -> str:
    """A section heading inside a card: the title in bold, then a dim rule to w columns."""
    return f"{B}{title}{R} {DIM}{'─' * max(w - vlen(title) - 1, 0)}{R}"


def bar(frac: float | None, w: int = 18) -> str:
    """A w-column fill bar: green below 70 %, yellow below 90 %, red above."""
    f = max(0.0, min(1.0, frac or 0))
    col = GRN if f < 0.7 else YEL if f < 0.9 else RED
    n = round(f * w)
    return f"{col}{'█' * n}{DIM}{'░' * (w - n)}{R}"


def size(b: float | None) -> str:
    """Bytes as 1.5G / 300.0M / 12K / 7B."""
    if b is None:
        return "?"
    for unit, div in (("G", 2**30), ("M", 2**20), ("K", 2**10)):
        if abs(b) >= div:
            return f"{b / div:.0f}{unit}" if unit == "K" else f"{b / div:.1f}{unit}"
    return f"{b:.0f}B"


def knum(n: float | None) -> str:
    """A token count as 12.3K or 950 (None counts as 0)."""
    v = n or 0
    return f"{v / 1000:.1f}K" if v >= 1000 else f"{v:.0f}"


def dur(s: float | None) -> str:
    """Seconds as 1h02m / 3m05s / 42s; None as a dash."""
    if s is None:
        return "–"
    n = int(s)
    return f"{n // 3600}h{n % 3600 // 60:02d}m" if n >= 3600 else f"{n // 60}m{n % 60:02d}s" if n >= 60 else f"{n}s"


def ctx_label(v: object) -> str:
    """A context size in tokens as 96K; anything that is not a whole number as itself."""
    return f"{int(str(v)) // 1024}K" if str(v).isdigit() else str(v)


def home_short(path: str, home: str) -> str:
    """A path with the home directory shown as ~."""
    return path.replace(home, "~")


def pill(text: str, bg: str) -> str:
    """Bold black text on a coloured background (bg: an SGR background code such as 42)."""
    return f"\x1b[1;30;{bg}m {text} {R}"


def lv(label: str, value: str, w: int = 10) -> str:
    """A dim label padded to w columns, then the value."""
    return f"{DIM}{label:<{w}}{R}{value}"


@dataclass
class Ln:
    """A line of card text, optionally clickable as a whole (act) or in parts (spans of visible columns)."""
    text: str = ""
    act: str | None = None
    spans: list[Span] = field(default_factory=list)


CardLine = Union[str, Ln]


class Card(NamedTuple):
    """The content of a card: title, a summary after it in the header, and its lines."""
    title: str
    summary: str
    lines: list[CardLine]


def buttons(prefix: str, items: Sequence[tuple[str, str]]) -> Ln:
    """A line of inline buttons after prefix: items = [(label, action)]."""
    text, spans, col = prefix, [], vlen(prefix)
    for label, act in items:
        b = f"[ {label} ]"
        spans.append((col, col + len(b), act))
        text += f"{B}{CYN}{b}{R}  "
        col += len(b) + 2
    return Ln(text, spans=spans)


def button_rows(prefix: str, items: Sequence[tuple[str, str]], w: int) -> list[Ln]:
    """Inline buttons like buttons(), on as many lines as w columns need (a button is never cut)."""
    rows: list[Ln] = []
    line: list[tuple[str, str]] = []
    used = vlen(prefix)
    for label, act in items:
        bw = len(f"[ {label} ]") + 2
        if line and used + bw - 2 > w:
            rows.append(buttons(prefix if not rows else " " * vlen(prefix), line))
            line, used = [], vlen(prefix)
        line.append((label, act))
        used += bw
    rows.append(buttons(prefix if not rows else " " * vlen(prefix), line))
    return rows


def draw_card(name: str, title: str, summary: str, lines: Sequence[CardLine], w: int, lvl: int) -> list[Row]:
    """Rows of a bordered card w columns wide. Its header (clickable: "level:<name>") shows
    the detail level as dots: ○○ collapsed (the lines are hidden), ●○ normal, ●● full."""
    arrow = "▾" if lvl else "▸"
    dots = f"{CYN}{'●' * lvl}{DIM}{'○' * (2 - lvl)}{R}"
    head = f"{DIM}╭─{R} {B}{CYN}{arrow} {title}{R} {dots} "
    if summary:
        head += f"{summary} "
    fill = max(w - vlen(head) - 1, 0)
    rows: list[Row] = [(fit(head + DIM + "─" * fill, w - 1) + f"{DIM}╮{R}", [(0, w, f"level:{name}")])]
    if lvl:
        for item in lines:
            ln = item if isinstance(item, Ln) else Ln(item)
            spans = [(2 + a, 2 + b, act) for a, b, act in ln.spans]
            if ln.act:
                spans.append((2, w - 2, ln.act))
            rows.append((f"{DIM}│{R} " + fit(ln.text, w - 4) + f" {DIM}│{R}", spans))
    rows.append((f"{DIM}╰{'─' * (w - 2)}╯{R}", []))
    return rows


def indent(rows: Sequence[Row], n: int = 1) -> list[Row]:
    """Rows moved n columns to the right, clickable parts included."""
    return [(" " * n + t, [(n + a, n + b, act) for a, b, act in sp]) for t, sp in rows]


def merge_columns(left: Sequence[CardLine], right: Sequence[CardLine], lw: int) -> list[CardLine]:
    """Two columns inside one card: left lines cut / padded to lw, a dim divider, then the right
    lines; the clickable parts of both are kept (the right ones shifted)."""
    out: list[CardLine] = []
    shift = lw + 3                                       # " │ "
    for i in range(max(len(left), len(right))):
        lt = left[i] if i < len(left) else ""
        rt = right[i] if i < len(right) else ""
        ll = lt if isinstance(lt, Ln) else Ln(lt)
        rl = rt if isinstance(rt, Ln) else Ln(rt)
        spans = list(ll.spans) + ([(0, lw, ll.act)] if ll.act else [])
        spans += [(a + shift, b + shift, act) for a, b, act in rl.spans]
        if rl.act:
            spans.append((shift, shift + max(vlen(rl.text), 1), rl.act))
        out.append(Ln(fit(ll.text, lw) + f" {DIM}│{R} " + rl.text, spans=spans))
    return out


SIDE_MIN = 140      # a card at least this wide (inside) puts its explanations in a side column
Section = tuple[str, Sequence[CardLine]]       # a side section: its header and its lines (text wraps)
Key = tuple[str, str]                          # a key and what it does (the footer, the ? card)


def side_width(inner: int) -> int:
    """The side column's width in a card `inner` columns wide (no main_w given)."""
    return max(44, min(76, inner * 2 // 5))


def side_lines(tip: str, sections: Sequence[Section], w: int) -> list[CardLine]:
    """The side column: a one-line Quick tip about the selection, then each section under its own
    header (sections without lines are left out)."""
    out: list[CardLine] = []
    if tip:
        out += [heading("Quick tip", w), *cwrap(f"{CYN}{tip}{R}", w)]
    for title, body in sections:
        if not body:
            continue
        if out:
            out.append("")
        out.append(heading(title, w))
        for x in body:
            if isinstance(x, str):
                out += cwrap(x, w)
            elif x.spans:                    # buttons: as they are
                out.append(x)
            else:                            # a clickable line: every wrapped piece clicks
                out += [Ln(t, act=x.act) for t in cwrap(x.text, w)]
    return out


SIDE_MAX = 90       # wider explanations are hard to read


def with_side(main: Callable[[int], list[CardLine]], tip: str, sections: Sequence[Section], inner: int,
              main_w: int = 0) -> list[CardLine]:
    """The controls and the data on the left; the quick tip and the explanations beside them when
    the card is wide (SIDE_MIN), else under them, under the same headers. main(w) draws w columns;
    main_w: the width the controls need (the side column takes the rest, up to SIDE_MAX)."""
    if inner >= SIDE_MIN:
        mw = max(main_w, inner - SIDE_MAX - 3) if main_w else inner - side_width(inner) - 3
        if inner - mw - 3 >= 44:
            return merge_columns(main(mw), side_lines(tip, sections, inner - mw - 3), mw)
    return [*main(inner), "", *side_lines(tip, sections, inner)]


def key_hint(keys: Sequence[Key]) -> str:
    """The footer's keys: "↑↓ select · a apply · ..." (key bold, what it does dim)."""
    return f"{DIM} · {R}".join(f"{B}{k}{R}{DIM} {what}{R}" for k, what in keys)


def side_by_side(left: Sequence[Row], right: Sequence[Row], lw: int, pad_left: bool = True) -> list[Row]:
    """Two columns of rows, the left one lw columns wide, with a space before and between them.
    pad_left=False trusts the left rows to be exactly lw wide already (cards are)."""
    out: list[Row] = []
    for i in range(max(len(left), len(right))):
        lt, ls = left[i] if i < len(left) else (" " * lw, [])
        rt, rs = right[i] if i < len(right) else ("", [])
        out.append((" " + (fit(lt, lw) if pad_left else lt) + " " + rt,
                    [(1 + a, 1 + b, act) for a, b, act in ls] + [(2 + lw + a, 2 + lw + b, act) for a, b, act in rs]))
    return out
