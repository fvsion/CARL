"""RECENT REQUESTS and the LOG.
Pure: they render a ServerData snapshot and a View."""
from __future__ import annotations

from typing import Callable, List, Mapping, Optional, Sequence

from ....domain.server.known_warnings import known
from ....domain.server.logbook import ROUTINE, TS, LogBook, RequestRecord, level_of, offset
from ....domain.server.snapshot import ServerData
from ....domain.shared.units import duration, percent, tokens
from ....ports.plugin import InfoLine
from ....ui.fmt import (DIM, R, RED, YEL, Card, CardLine, fit, wrap)
from ....ui.fmt import wall as clock_text
from ....ui.words import command
from .view import View

# ---------------------------------------------------------------- requests and log
REQ_MORE = f"  {'Reused':>8}"                      # the Requests tab and the full Live card add these columns
REQ_FULL = f"  {'Slot':>4}  {'Reused from':<12}"


def req_row(r: RequestRecord, wall: Callable[[Optional[float]], str]) -> str:
    """One request as a table row (REQ_HEAD); wall turns its start into a clock time."""
    took = r.t1 - r.t0 if r.t1 is not None and r.t0 is not None else None
    acc = f"{RED}{'error':>12}{R}" if r.error else f"{percent(r.acc) if r.acc is not None else '–':>12}"
    return (f"{wall(r.t0):8}  {tokens(r.ctx):>8}  {tokens(r.new):>10}  {(r.pp or 0):>10.0f}  "
            f"{tokens(r.gen):>6}  {(r.tg or 0):>11.1f}  {duration(took):>6}  {acc}")


def req_table(reqs: List[RequestRecord], wall: Callable[[Optional[float]], str], full: bool, w: int,
              mean: bool = False, more: Optional[Mapping[int, Sequence[InfoLine]]] = None) -> List[CardLine]:
    """The requests as a table: a header row, an optional "mean" row under the speed columns, one row each (newest
    first as given); full adds the slot and where the reused tokens came from, as columns, or on a second aligned row
    when w is too narrow for them, and the lines the plugins add under a request (more: by the request's task), dim,
    one row each (a long one is cut)."""
    wide = len(REQ_HEAD + REQ_MORE + REQ_FULL) <= w
    L: List[CardLine] = [f"{DIM}{REQ_HEAD}{REQ_MORE}{REQ_FULL if full and wide else ''}{R}"]
    done = [r for r in reqs if r.tg]
    if mean and done:
        pp = sum(r.pp or 0 for r in done) / len(done)
        tg = sum(r.tg or 0 for r in done) / len(done)
        L.append(f"{DIM}{'Mean':8}  {'':>8}  {'':>10}  {R}{pp:>10.0f}  {'':>6}  {tg:>11.1f}")
    for r in reqs:
        line = req_row(r, wall) + f"  {tokens(max(r.ctx - r.new - r.gen, 0)):>8}"
        extra = f"{r.slot:>4}  {r.source or '–':<12}"
        if full and wide:
            line += "  " + extra
        L.append(line)                    # a failed request says "error" in its guesses column
        if full and not wide:
            L.append(f"{DIM}{'':8}  Slot {r.slot}.   Reused from: {r.source or '–'}.{R}")
        for ln in (more or {}).get(r.task, ()) if full and r.task is not None else ():
            said = f"{ln.label}: {ln.text}" if ln.label else ln.text
            L.append(fit(f"{DIM}{'':8}  {said}{ln.note}{R}", w))
    return L


def card_requests(v: View, d: ServerData, n: int, w: int = 200) -> Card:
    """The last n finished requests, newest first (the full level adds the slot and the source)."""
    reqs = list(v.log.requests)
    running = f", {len(v.log.current)} running" if v.log.current else ""
    rows = req_table(list(reversed(reqs[-n:])), lambda t: clock_text(v.log.epoch(t)), v.full, w) if reqs else \
        [f"{DIM}No finished request in this log yet.{R}"]
    return Card("RECENT REQUESTS", f"{len(reqs)} finished{running}", rows)


def routine(line: str) -> bool:
    """A log line llama.cpp writes in normal work (logbook.ROUTINE)."""
    return any(r in line for r in ROUTINE)


KNOWN_INDENT = len("12:00:00 W ")      # the reason under a known line starts under its text


def log_line(book: LogBook, line: str, ms: bool = False) -> str:
    """A log line with its clock time (the log's own clock is minutes since the server started; ms: with
    milliseconds)."""
    m = TS.match(line)
    if not m or not book.start:
        return line
    return f"{clock_text(book.epoch(offset(line)), ms)} {m.group(5)} {line[m.end():]}"


def log_view(v: View, n: int, width: int) -> List[CardLine]:
    """The last n lines of the log (fewer, wrapped, when wrapping), with clock times; errors red,
    warnings yellow, the lines that are normal at a start dim."""
    if not v.log_path:
        return [f"{DIM}{NOLOG_TEXT.format(command=command())}{R}"]
    lines = [ln for ln in v.log.lines if not v.errors_only or level_of(ln) in ("E", "W")]   # errors and warnings, all
    end = len(lines) - v.log_scroll
    out: List[CardLine] = []
    for line in lines[max(0, end - n * (4 if v.wrap else 1)):end]:
        lvl = level_of(line)
        col = DIM if routine(line) else RED if lvl == "E" else YEL if lvl == "W" else ""
        text = log_line(v.log, line, v.full)
        if not v.wrap:
            out.append(col + text)
        else:
            out += [col + part for part in wrap(text, width)]
        why = known(line) if lvl in ("W", "E") else None
        if why:                         # a known warning: grey, with its reason under it (Phase 23.5 item 8)
            out.append(f"{DIM}{' ' * KNOWN_INDENT}↳ known: {why}{R}")
    return out[-n:] or [f"{DIM}(no log lines){R}"]

NOLOG_TEXT = "No log file: the server writes to its terminal. Start it with {command} to see the log here."
REQ_HEAD = (f"{'Started':8}  {'Context':>8}  {'New tokens':>10}  {'Read tok/s':>10}  {'Output':>6}  "
            f"{'Write tok/s':>11}  {'Took':>6}  {'Guesses ACPT':>12}")
