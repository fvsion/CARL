"""Terminal text: ANSI colours, column-exact cutting and padding, bars, and the bordered cards
every tab is drawn from. Numbers on screen use carl_core.domain.units (one formatter per kind of
number). Pure functions."""
from __future__ import annotations

import re
import textwrap
import unicodedata
from dataclasses import dataclass, field
from typing import NamedTuple, Sequence, Union

from carl_core.domain.units import tokens

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
R, DIM, B = "\x1b[0m", "\x1b[2m", "\x1b[1m"
GRN, YEL, RED, CYN, MAG = "\x1b[32m", "\x1b[33m", "\x1b[31m", "\x1b[36m", "\x1b[35m"
NA = f"{DIM}–{R}"       # a value that is not known yet

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
    """Wrap the plain text of s to w columns (log lines): before a space or after a "/", inside a
    word only when the word is wider than a line."""
    plain = ANSI.sub("", s)
    w = max(w, 1)
    out: list[str] = []
    line = ""
    for piece in re.split(r"(?<=/)|(?= )", plain):
        if len(line) + len(piece) <= w:
            line += piece
            continue
        if line.strip():
            out.append(line.rstrip())
        line = piece.lstrip() if line.strip() else line + piece
        while len(line) > w:
            out.append(line[:w])
            line = line[w:]
    out.append(line)
    return out


def wwrap(text: str | None, w: int) -> list[str]:
    """Word-wrap the plain text of text to w columns (at least 10)."""
    return textwrap.wrap(ANSI.sub("", text or ""), max(w, 10)) or [""]


def cwrap(text: str | None, w: int, indent: str = "") -> list[str]:
    """Word-wrap like _cwrap, but no line holds one lone word at the end of a paragraph ("GiB.", "minutes."): the
    lines are wrapped narrower (up to a quarter) until the last one holds two words (Phase 23.2)."""
    out = _cwrap(text, w, indent)
    if len(out) < 2 or "\n" in (text or ""):
        return out

    def lone(lines: list[str]) -> bool:
        return len(ANSI.sub("", lines[-1]).split()) == 1 and len(ANSI.sub("", lines[-2]).split()) > 2
    if not lone(out):
        return out
    for narrower in range(max(w, 10) - 1, int(max(w, 10) * 0.75), -1):
        alt = _cwrap(text, narrower, indent)
        if not lone(alt):
            return alt
    return out


def _cwrap(text: str | None, w: int, indent: str = "") -> list[str]:
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


def ctx_label(v: object) -> str:
    """A context size in tokens as 96K (K = 1024); anything that is not a whole number as itself."""
    return tokens(int(str(v))) if str(v).isdigit() else str(v)


def home_short(path: str, home: str) -> str:
    """A path with the home directory shown as ~."""
    return path.replace(home, "~")


def pill(text: str, bg: str) -> str:
    """Bold black text on a coloured background (bg: an SGR background code such as 42). The black is colour 16 of
    the 256: many terminals draw bold colour 30 as grey, which is hard to read on red."""
    return f"\x1b[1;38;5;16;{bg}m {text} {R}"


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


LW = 16                  # the widest label column of a section (a card's own is as wide as its labels need)
ROW_MARK, ROW_SEP = "\x1f", "\x1e"


def row(label: str, value: str) -> str:
    """One reading: a label, then its value (one reading per row: no " · " runs). aligned() gives every card
    its own label column, as wide as its longest label."""
    return f"{ROW_MARK}{label}{ROW_SEP}{value}"


def aligned(lines: list, w: int = 0) -> list:
    """The section's rows with their labels in one dim column (the longest label + 2, at most LW + 2). With w (the
    text width), a long value wraps under its own column, not under the label."""
    labels = [(ln.text if isinstance(ln, Ln) else ln)[1:].split(ROW_SEP, 1)[0] for ln in lines
              if (ln.text if isinstance(ln, Ln) else ln if isinstance(ln, str) else "").startswith(ROW_MARK)]
    lw = min(max((len(x) for x in labels), default=0) + 2, LW + 2)
    out: list = []
    for ln in lines:
        text = ln.text if isinstance(ln, Ln) else ln
        if not isinstance(text, str) or not text.startswith(ROW_MARK):
            out.append(ln)
            continue
        lab, val = text[1:].split(ROW_SEP, 1)
        lab = lab[:1].upper() + lab[1:]                     # labels start with a capital (user, 2026-10-08)
        if isinstance(ln, Ln) or not w or vlen(val) <= w - lw:
            out.append(Ln(lv(lab, val, lw), ln.act, ln.spans) if isinstance(ln, Ln) else lv(lab, val, lw))
            continue
        parts = cwrap(val, w - lw)
        out.append(lv(lab, parts[0], lw))
        out += [" " * lw + x for x in parts[1:]]
    return out


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


def level_mark(lvl: int, marks: int) -> str:
    """The level of a section in its title: ▸ collapsed, ▾ open; with three levels also ○○ / ●○ / ●● (collapsed,
    simple, full). marks = 0: no mark (a box that has no levels)."""
    if not marks:
        return ""
    arrow = "▸" if not lvl else "▾"
    dots = ("○○", "●○", "●●")[max(0, min(lvl, 2))] if marks == 3 else ""
    return f"{arrow} " + (f"{{T}} {dots}" if dots else "{T}")


def draw_card(name: str, title: str, summary: str, lines: Sequence[CardLine], w: int, lvl: int,
              marks: int = 0, sel: bool = False) -> list[Row]:
    """Rows of a bordered card w columns wide. Its title row is clickable ("level:<name>"): lvl 0 =
    collapsed (one row: ▸, the title and its summary), else open (the lines in a box). marks: 3 = the
    section has three levels (its title shows ○○ ●○ ●●), 2 = two (collapsed / open), 0 = no levels.
    sel: the selected section (Tab): its title is drawn reversed."""
    t = f"\x1b[7m {title} \x1b[27m" if sel else title
    mark = level_mark(lvl, marks)
    shown = mark.replace("{T}", f"{B}{CYN}{t}{R}{CYN}") + R if mark else f"{B}{CYN}{t}{R}"
    if not lvl:
        if not mark:
            shown = f"{B}{CYN}▸ {t}{R}"
        head = f"{DIM}──{R} {shown}  {summary} " if summary else f"{DIM}──{R} {shown} "
        fill = max(w - vlen(head), 0)
        return [(fit(head + DIM + "─" * fill, w), [(0, w, f"level:{name}")])]
    head = f"{DIM}╭─{R} {shown}  {summary} " if summary else f"{DIM}╭─{R} {shown} "
    fill = max(w - vlen(head) - 1, 0)
    rows: list[Row] = [(fit(head + DIM + "─" * fill, w - 1) + f"{DIM}╮{R}", [(0, w, f"level:{name}")])]
    for item in lines:
        ln = item if isinstance(item, Ln) else Ln(item)
        spans = [(2 + a, 2 + b, act) for a, b, act in ln.spans]
        if ln.act:
            spans.append((2, w - 2, ln.act))
        rows.append((f"{DIM}│{R} " + fit(ln.text, w - 4) + f" {DIM}│{R}", spans))
    rows.append((f"{DIM}╰{'─' * (w - 2)}╯{R}", []))
    return rows


def reveal(ui: object, rows: Sequence[Row], attr: str, height: int) -> None:
    """Tab selected another section: scroll the page (ui.<attr>) so that its title row is on the screen. Only once
    per selection, so the wheel and PgUp PgDn still move the page after it."""
    sel = getattr(ui, "section", "")
    if not sel or getattr(ui, "revealed", "") == sel:
        return
    setattr(ui, "revealed", sel)
    at = next((i for i, (_, sp) in enumerate(rows) if any(act == f"level:{sel}" for _, _, act in sp)), None)
    if at is None:
        return
    top = int(getattr(ui, attr))
    if at < top or at >= top + max(height - 2, 1):
        setattr(ui, attr, max(0, at - 1))


def indent(rows: Sequence[Row], n: int = 1) -> list[Row]:
    """Rows moved n columns to the right, clickable parts included."""
    return [(" " * n + t, [(n + a, n + b, act) for a, b, act in sp]) for t, sp in rows]


SIDE_MIN = 140      # a card at least this wide (inside) puts its explanations in a side column
Section = tuple[str, Sequence[CardLine]]       # a side section: its header and its lines (text wraps)
Key = tuple[str, str]                          # a key and what it does (the footer, the ? card)


def side_width(inner: int) -> int:
    """The side column's width in a card `inner` columns wide (no main_w given)."""
    return max(44, min(76, inner * 2 // 5))


SIDE_MAX = 90       # wider explanations are hard to read


def key_hint(keys: Sequence[Key]) -> str:
    """The footer's keys: "↑↓ select   a apply   ..." (key bold, what it does dim)."""
    return "   ".join(f"{B}{k}{R}{DIM} {what}{R}" for k, what in keys)       # three spaces: no " · " (Phase 23.2)


def footer_keys(keys: Sequence[Key], tail: Sequence[Key], w: int) -> str:
    """The footer within w columns: the screen's keys, then tail (D detail, ? all keys, q quit),
    which always stays; the screen's last keys go first when the line is too long."""
    sticky = [k for k in keys if k[0] in ("Tab", "L")]          # the section keys stay, as the tail does
    ks = [k for k in keys if k not in sticky]
    while ks and vlen(key_hint([*ks, *sticky, *tail])) > w:
        ks.pop()
    return key_hint([*ks, *sticky, *tail])


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
