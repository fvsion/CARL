"""The page of the Settings panels that are built from sections (Auto fit, Auto-tune, Router, Caching): a Part
(one section with its level), the main column and a side column from SIDE_AT columns, one scroll for the page, and
the progress of a download as rows. Pure (Phase 23.2): what a page tells the dashboard goes to a Shown."""
from __future__ import annotations

import shutil
from typing import Callable, List, NamedTuple, Optional, Sequence

from ....domain.shared.units import duration, file_size, percent
from ....ports.plugin import PanelView, section_name
from ....ui.fmt import (reveal, ANSI, B, CYN, DIM, GRN, R, RED, YEL, CardLine, Key, Ln, Row, aligned, bar, cwrap, draw_card,
                        fit, indent, row, side_by_side, vlen)
from ...act import Verb, act as make
from ...shown import Shown
from ...state import Download, UIState
from .common import choice_line


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


def layout(out: Shown, parts: Sequence[Part], cols: int, side_w: int, balance: bool = False) -> List[Row]:
    """The page: one column in the parts' order; with side_w, the side parts in a column beside the
    others. balance: the last side parts move under the main column while the two columns get more even. The
    sections, in order, go to out."""
    w = cols - 1
    if not side_w:
        out.sections = [p.name for p in parts]
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
    out.sections = [p.name for p in (*main, *under, *side)]
    return side_by_side([r for p in (*main, *under) for r in p.draw(mw)], [r for p in side for r in p.draw(side_w)],
                        mw, pad_left=False)


def scrolled(ui: UIState, out: Shown, rows: List[Row], height: int, attr: str = "set_scroll") -> List[Row]:
    """The rows on the screen: height rows from the scroll position (ui.<attr>, kept in range: the correction goes
    to out); out.more when the page goes on below."""
    out.revealed, top = reveal(ui.section, ui.revealed, rows, int(getattr(ui, attr)), height)
    top = out.fix(attr, max(0, min(top, len(rows) - height)))
    out.more = len(rows) > top + height
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


# ---------------------------------------------------------------- a plugin's panel (WP)
PLUGIN_LABEL_W = 22
PLUGIN_KEYS: List[Key] = [("↑↓", "a setting"), ("← →", "change its value"), ("Enter", "change it, or type a value"),
                          ("x", "its default"), ("[ ]", "panels")]
PLUGIN_KEYS_MORE = ["A plugin's panel shows the settings the plugin declares (config.json, plugins.ID.KEY) and what it "
                    "reads. CARL saves each change at once. Turn a plugin off with its first row."]


def plugin_value(v: object) -> str:
    """A setting value as the panel shows and compares it (a bool as true or false)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    return " ".join(map(str, v)) if isinstance(v, list) else str(v)


def plugin_rows(ui: UIState, pv: PanelView, w: int, selected: int) -> List[CardLine]:
    """The panel's settings, one row each: a row with choices (a choice, a bool, a number the plugin offers values for)
    shows them to click, the current one in brackets; any other row shows its value, and Enter types a new one. The
    selected row (ui.plugin_row) has › and, under it, what it does."""
    out: List[CardLine] = []
    for i, r in enumerate(pv.rows):
        sel = i == selected
        cur = plugin_value(r.value)
        if r.choices:
            opts = [(c, c, make(Verb.PLUGSET, f"{pv.plugin}:{r.key}:{c}")) for c in r.choices]
            out += choice_line(r.label, opts, cur, w, PLUGIN_LABEL_W, sel)
        else:
            head = (f"{CYN}{B}›{R} " if sel else "  ") + f"{B if sel else ''}{r.label:<{PLUGIN_LABEL_W - 2}}{R}"
            default = plugin_value(r.default)
            note = f"   {DIM}default {default or 'empty'}{R}" if cur != default else ""
            if sel and ui.text and ui.text.on_enter == f"plugin:{pv.plugin}:{r.key}":      # a value being typed
                note, cur = f"   {DIM}(Enter: save   Esc: cancel){R}", f"{CYN}{ui.text.value}▏{R}"
            out.append(Ln(fit(f"{head}{cur or DIM + 'empty' + R}{note}", w), act=make(Verb.PLUGROW, i)))
        if sel and r.help:
            out += [f"{' ' * PLUGIN_LABEL_W}{DIM}{x}{R}" for x in cwrap(r.help, max(w - PLUGIN_LABEL_W, 20))]
    return out


def plugin_readings(pv: PanelView) -> List[CardLine]:
    """The plugin's readings, coloured by tone (labels aligned by the section)."""
    out: List[CardLine] = []
    for ln in pv.lines:
        colour = {"ok": GRN, "warn": YEL, "bad": RED, "dim": DIM}.get(ln.tone, "")
        text = f"{colour}{ln.text}{R}" if colour else ln.text
        out.append(row(ln.label, text) if ln.label else text)
    return out


def plugin_info(pv: PanelView, w: int) -> List[CardLine]:
    """What the plugin says about itself, then its readings when they have no section of their own (lines_title)."""
    out: List[CardLine] = list(cwrap(pv.about, w)) if pv.about else []
    lines = [] if pv.lines_title else plugin_readings(pv)
    if pv.about and lines:
        out.append("")
    return out + lines


def plugin_panel(ui: UIState, pv: PanelView, cols: int, height: int, out: Optional[Shown] = None) -> List[Row]:
    """A plugin's panel (Settings tab, after Caching, or in its group: A73): its settings (the first row turns the
    plugin on or off), its readings in a section of their own under them when the plugin titles them (lines_title),
    and beside them, or under them on a narrow screen, what it says and reads. Each is a section with its levels (Tab,
    L). Pure."""
    out = Shown() if out is None else out
    row_n = out.fix("plugin_row", max(0, min(ui.plugin_row, len(pv.rows) - 1)))
    info_name = section_name(pv.plugin, pv.id + "-info")
    lines_name = section_name(pv.plugin, pv.id + "-lines")
    out.levels += [pv.name, info_name] + ([lines_name] if pv.lines_title and pv.lines else [])
    parts = [Part(pv.name, lambda w: section(ui, pv.name, pv.title.upper(), f"{DIM}{pv.heading}{R}",
                                             plugin_rows(ui, pv, w - 4, row_n), w, 2))]
    if pv.lines_title and pv.lines:
        parts.append(Part(lines_name, lambda w: section(ui, lines_name, pv.lines_title.upper(), "",
                                                        plugin_readings(pv), w, 2)))
    if pv.about or (pv.lines and not pv.lines_title):
        parts.append(Part(info_name, lambda w: section(ui, info_name, "ABOUT", "", plugin_info(pv, w - 4), w, 2), True))
    rows = layout(out, parts, cols, side_width(cols, 50, 80))
    typing = ui.text is not None and ui.text.on_enter.startswith("plugin:")
    out.keys, out.keys_more = ([("Enter", "save the value"), ("Esc", "cancel")] if typing else list(PLUGIN_KEYS)), \
        list(PLUGIN_KEYS_MORE)
    return scrolled(ui, out, rows, body_height(height), "page_scroll")


# ---------------------------------------------------------------- Auto fit
