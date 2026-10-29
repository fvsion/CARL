"""The page of the Settings panels that are built from sections (Auto fit, Auto-tune, Router, Caching): a Part
(one section with its level), the main column and a side column from SIDE_AT columns, one scroll for the page, and
the progress of a download as rows. Pure (Phase 23.2)."""
from __future__ import annotations

import shutil
from typing import Callable, List, NamedTuple, Sequence

from carl_core.domain.units import duration, file_size, percent

from ..fmt import (reveal, ANSI, DIM, GRN, R, RED, YEL, CardLine, Ln, Row, aligned, bar, cwrap, draw_card,
                   indent, row, side_by_side, vlen)
from ..state import Download, UIState


SIDE_AT = 150                   # a side column from this width (as the Server panel)


class Part(NamedTuple):
    """One section of a page: its name (its level, Tab), how it draws at a width, and whether it goes to
    the side column when there is one."""
    name: str
    draw: Callable[[int], List[Row]]
    side: bool = False


def section(ui: UIState, name: str, title: str, summary: str, lines: Sequence[CardLine], w: int,
            marks: int) -> List[Row]:
    """A section at its own level (its title shows it; Tab selects it, L changes it): the label rows
    aligned, long lines wrapped."""
    return draw_card(name, title, summary, flow(aligned(list(lines), w - 4), w - 4), w, ui.levels.get(name, 1),
                     marks, ui.section == name)


def flow(lines: Sequence[CardLine], w: int) -> List[CardLine]:
    """Card text wrapped to w columns (never cut): a wrapped line goes on under its own first column (prose
    at the edge, an indented table row under its indent); clickable lines stay as they are."""
    out: List[CardLine] = []
    for ln in lines:
        if isinstance(ln, Ln) or vlen(ln) <= w:
            out.append(ln)
        else:
            plain = ANSI.sub("", ln)
            lead = len(plain) - len(plain.lstrip(" "))
            out += cwrap(ln, w, " " * (lead + (2 if plain[lead:].startswith("• ") else 0)))
    return out


def help_part(ui: UIState, name: str, title: str, lines: Sequence[CardLine], summary: str = "",
              marks: int = 2) -> Part:
    """A side section of help text (two levels unless marks = 3)."""
    return Part(name, lambda w: section(ui, name, title, summary, lines, w, marks), True)


def side_width(cols: int, side_min: int = 60, main_max: int = 100) -> int:
    """The side column's width at cols columns (0: one column). The main column is at most main_max
    wide; the side column takes the rest, at least side_min."""
    w = cols - 1
    if w < SIDE_AT:
        return 0
    return max(side_min, w - 1 - main_max)


def body_height(height: int) -> int:
    """The rows the panel may use. 0: the app did not pass it (Auto-tune, Router and Caching today): the
    terminal's rows less the header, the panel bar, the message line and the footer."""
    return height if height > 0 else shutil.get_terminal_size((120, 36)).lines - 8


def layout(ui: UIState, parts: Sequence[Part], cols: int, side_w: int, balance: bool = False) -> List[Row]:
    """The page: one column in the parts' order; with side_w, the side parts in a column beside the
    others. balance: the last side parts move under the main column while the two columns get more even."""
    w = cols - 1
    if not side_w:
        ui.sections = [p.name for p in parts]
        return indent([r for p in parts for r in p.draw(w)])
    mw = w - side_w - 1
    main = [p for p in parts if not p.side]
    side = [p for p in parts if p.side]
    under: List[Part] = []
    if balance:
        left = sum(len(p.draw(mw)) for p in main)
        right = [len(p.draw(side_w)) for p in side]
        while len(side) > 1:
            moved = len(side[-1].draw(mw))
            if max(left + moved, sum(right) - right[-1]) >= max(left, sum(right)):
                break
            left += moved
            right.pop()
            under.insert(0, side.pop())
    ui.sections = [p.name for p in (*main, *under, *side)]
    return side_by_side([r for p in (*main, *under) for r in p.draw(mw)], [r for p in side for r in p.draw(side_w)],
                        mw, pad_left=False)


def scrolled(ui: UIState, rows: List[Row], height: int, attr: str = "set_scroll") -> List[Row]:
    """The rows on the screen: height rows from the scroll position (ui.<attr>, kept in range); ui.more
    when the page goes on below."""
    reveal(ui, rows, attr, height)
    top = max(0, min(int(getattr(ui, attr)), len(rows) - height))
    setattr(ui, attr, top)
    ui.more = len(rows) > top + height
    return rows[top:top + height]


def progress(dl: Download, w: int) -> List[CardLine]:
    """A download as label rows: what, then one progress line (a dim │ between the readings); the outcome
    after."""
    what = f"{dl.name} (model + MTP drafter)" if dl.more else dl.name
    if not dl.done:
        if any("verifying" in x for x in dl.tail):
            return [row("downloading", what), row("", f"{YEL}CARL is checking the file (SHA-256).{R}")]
        frac = dl.have / dl.total if dl.total else 0
        sep = f"  {DIM}│{R}  "
        parts = [f"{bar(frac, 16)}  {percent(frac)}", f"{file_size(dl.have).replace(' GB', '')} of {file_size(dl.total)}",
                 f"{file_size(dl.rate)}/s"]
        if dl.rate > 0 and dl.total:
            parts.append(f"about {duration((dl.total - dl.have) / dl.rate)} left")
        while len(parts) > 2 and vlen(sep.join(parts)) > w - 18:
            parts.pop()
        return [row("downloading", what), row("", sep.join(parts))]
    if dl.proc.returncode == 0:
        return [row("downloaded", f"{GRN}✓ {dl.name} is downloaded and checked.{R}")]
    return [row("download", f"{RED}✗ The download of {dl.name} failed.{R}"), row("", f"{DIM}{' '.join(dl.tail)[-100:]}{R}"),
            row("", "Press d to try again. The download continues from the part on the disk.")]


# ---------------------------------------------------------------- Auto fit
