"""The text layout of the command line: the output's width, and paragraphs and term columns wrapped to it. The help
topics (cli/help_topics.py) and every command's text use it.

The width is COLUMNS when it is set (the composition reads it and gives it here: rule R6), else the terminal's
width, else 80 columns; at most MAX_WIDTH, at least MIN_WIDTH. Words are never cut, and a number stays on the line
of its unit ("2 min").
"""
from __future__ import annotations

import re
import shutil
import sys
import textwrap
from typing import List, Optional, Sequence, Tuple

MIN_WIDTH, MAX_WIDTH, DEFAULT_WIDTH = 40, 120, 80


def width(columns: str = "") -> int:
    """The width of the output: columns (the text of COLUMNS), else the terminal's width, else 80; in
    MIN_WIDTH..MAX_WIDTH."""
    if columns.isdigit() and int(columns) > 0:
        w = int(columns)
    elif sys.stdout.isatty():
        w = shutil.get_terminal_size((DEFAULT_WIDTH, 24)).columns
    else:
        w = DEFAULT_WIDTH
    return max(MIN_WIDTH, min(MAX_WIDTH, w))


NBSP = " "
_UNIT = re.compile(r"(\d) (s|min|h|ms|GB|GiB|MB|MiB|KiB|kB|K|tokens|tok/s|%|slots?|guess(?:es)?)(?=[\s.,;:)]|$)")


def wrap(text: str, w: int, indent: str = "", hang: Optional[str] = None) -> List[str]:
    """A paragraph as lines of at most w characters: the first line starts with indent, the
    others with hang (default: indent). Words are never cut, and a number stays on the line of
    its unit ("2 min"); a word longer than a line stays whole on its own line."""
    hang = indent if hang is None else hang
    lines = textwrap.wrap(_UNIT.sub(lambda m: m.group(1) + NBSP + m.group(2), text), w, initial_indent=indent,
                          subsequent_indent=hang, break_long_words=False, break_on_hyphens=False)
    return [line.replace(NBSP, " ") for line in lines] or [indent.rstrip()]


def columns(rows: Sequence[Tuple[str, str]], w: int, indent: str = "  ", max_term: int = 24, gap: int = 2,
            term_width: Optional[int] = None) -> List[str]:
    """Term and description pairs: the description in a column beside the term, wrapped; a term
    longer than the column puts its description on the next line. A narrow screen puts every
    description under its term. term_width: the column of the terms (default: the longest
    term, at most max_term)."""
    tw = term_width if term_width is not None else min(max((len(t) for t, _ in rows), default=0), max_term)
    col = len(indent) + tw + gap
    out: List[str] = []
    if w - col < 30:                                # too narrow for two columns
        for term, desc in rows:
            out.append(indent + term)
            if desc:
                out += wrap(desc, w, indent + "    ")
        return out
    for term, desc in rows:
        if not desc:
            out.append(indent + term)
        elif len(term) > tw:
            out.append(indent + term)
            out += wrap(desc, w, " " * col)
        else:
            out += wrap(desc, w, f"{indent}{term:<{tw}}{' ' * gap}", " " * col)
    return out
