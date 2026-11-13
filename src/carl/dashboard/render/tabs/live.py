"""The bodies of the Live, Requests and Log tabs (Connect: connect.py), the ? card, the quit dialog and the
"a turn runs" dialog. Pure: they draw from a View, the UI state and the snapshot; the keys of the screen they draw,
its sections and the scroll positions kept in range go to a Shown, not to the UI state."""
from __future__ import annotations

import time
from dataclasses import replace
from typing import List, Mapping, Optional, Sequence, Tuple

from ....domain.server.logbook import RequestRecord
from ....domain.server.snapshot import ServerData
from ....domain.shared.units import duration
from ....ports.plugin import SLOT_MAIN, InfoLine
from ....ui.fmt import wall as clock_text
from ....ui.fmt import (reveal, B, DIM, R, RED, CardLine, Key, Row, aligned, buttons, cwrap, draw_card, row, heading,
                        home_short, indent, side_by_side)
from ....ui.words import command, computer, plural
from ...act import DrainOp, Verb, act as make
from ...shown import Shown
from ...state import Drain, UIState
from ..cards import (REQ_FULL, REQ_HEAD, REQ_MORE, STOPPED, View, card_requests, card_requests_plugin, card_stopped,
                     column, live_sentence, log_view, req_table, status_of, wrapped)
from .connect import body_connect, clients_lines  # noqa: F401  (app.py and the tests import them from here)

EVERY_SCREEN: List[Key] = [("1-5", "the tabs"), ("Tab Shift-Tab", "select a section"), ("L", "its level: simple, full, "
                            "collapsed"), ("D", "every section: simple or full (saved)"), ("space", "read the server "
                            "again now"), ("?", "this list"),
                           ("q", "quit (CARL asks first)")]
TWO_COLUMNS = 100           # the Live tab puts its cards in two columns from this width
CACHE_COLUMN = 160          # the SLOTS card shows the Cache column from this width (user, 2026-10-11: "only ... on 160
                            # and 200 not on 130 and 100")
REQ_ROWS = 3               # the rows RECENT REQUESTS shows at least (a collapsed plugin card may take some, down to 1)
THREE_COLUMNS = 190        # ... and in three from this one (three of 52 at 160 wrap more than two of 78)


def keys_card(keys: Sequence[Key], more: Sequence[str], messages: Sequence[Tuple[float, str]], w: int) -> List[Row]:
    """The ? card: every key of the screen shown, what else it does, the keys of every screen, and the
    last messages."""
    def table(ks: Sequence[Key]) -> List[CardLine]:
        kw = max((len(k) for k, _ in ks), default=0) + 2
        return [f"  {B}{k:<{kw}}{R}{what}" for k, what in ks]
    every = {k for k, _ in EVERY_SCREEN} | {"Tab", "L", "D", "?", "q"}
    L: List[CardLine] = [heading("This screen", w - 4), *table([k for k in keys if k[0] not in every])]
    L += [x for m in more for x in cwrap(f"{DIM}{m}{R}", w - 4)]
    L += ["", heading("Every screen", w - 4), *table(EVERY_SCREEN)]
    if messages:
        L += ["", heading("Recent messages", w - 4)]
        L += [x for t, m in messages[-5:] for x in cwrap(f"{DIM}{time.strftime('%H:%M', time.localtime(t))}{R} {m}",
                                                          w - 4, "      ")]
    return draw_card("keys", "KEYS", f"{DIM}? closes this list{R}", L, w, 1)


def live_columns(cols: int) -> int:
    """How many columns of cards the Live tab uses: 3 from THREE_COLUMNS, 2 from TWO_COLUMNS (100: two narrow
    columns, the page scrolls; user, 2026-10-08), else 1."""
    if cols >= THREE_COLUMNS:
        return 3
    return 2 if cols >= TWO_COLUMNS else 1


def plugin_slots(v: View, out: Shown, cols: int, stopped: bool) -> Tuple[List[str], List[str]]:
    """The names of the plugins' cards for the slots of the Live tab (WP): main (the end of the first column) and side
    (the end of the last one), each in its order. A card shows from its min_cols; when no server runs, only a card that
    says stopped. Each card is a section of its own (Tab, L, a click on its title). None without a plugin card."""
    main: List[str] = []
    side: List[str] = []
    for c in v.plugin_cards:
        if cols < c.min_cols or (stopped and not c.stopped):
            continue
        out.levels.append(c.name)
        (main if c.slot == SLOT_MAIN else side).append(c.name)
    return main, side


def body_live(v: View, ui: UIState, d: ServerData, cols: int, height: int, out: Optional[Shown] = None) -> List[Row]:
    """The state in one sentence, then the cards in 1, 2 or 3 columns (live_columns), then RECENT REQUESTS and
    the LOG, which take the rows left (the page scrolls when it is taller than the screen). No server: SERVER
    and THIS MAC, then the log."""
    out = Shown() if out is None else out
    stopped = status_of(d, v.server_pid)[0] == STOPPED
    rows: List[Row] = [] if stopped else [(" " + x, list()) for x in cwrap(live_sentence(d, v.port), cols - 2)]
    rows.append(("", []))
    ncol = live_columns(cols)
    v = replace(v, selected=ui.section, cache_column=cols >= CACHE_COLUMN)
    main, side = plugin_slots(v, out, cols, stopped)
    req_floor = REQ_ROWS                                  # the rows RECENT REQUESTS keeps at least
    if stopped:
        card = card_stopped(v, d)
        cw = (cols - 3) // 2 if ncol > 1 else cols - 1
        server = draw_card("server", card.title, card.summary, wrapped(aligned(card.lines, cw - 4), cw - 4), cw,
                           v.level("server"), 2, ui.section == "server")
        mac = column(v, ["thismac", *main, *side], d, cols - 3 - cw if ncol > 1 else cols - 1)
        rows += side_by_side(server, mac, cw, pad_left=False) if ncol > 1 else indent(server + mac)
        out.sections = ["server", "thismac", *main, *side, "log"]
        out.keys = [("a", "start the server"), ("5", "settings"), ("4", "log"), ("Tab", "section"), ("L", "level")]
        out.keys_more = ["a opens Settings > Server and asks before it starts the server."]
    else:
        def cards(v: View, main: List[str], side: List[str]) -> Tuple[List[Row], List[List[str]]]:
            if ncol == 3:
                cw = (cols - 4) // 3
                groups = [["slots", "memory", *main], ["speed", "health", "model"], ["thismac", "connect", *side]]
                c1, c2 = column(v, groups[0], d, cw), column(v, groups[1], d, cw)
                c3 = column(v, groups[2], d, cols - 4 - 2 * cw)
                return side_by_side(c1, side_by_side(c2, c3, cw, pad_left=False), cw, pad_left=False), groups
            if ncol == 2:
                cw = (cols - 3) // 2
                groups = [["slots", "memory", "thismac", *main], ["speed", "connect", "health", "model", *side]]
                return side_by_side(column(v, groups[0], d, cw), column(v, groups[1], d, cols - 3 - cw), cw,
                                    pad_left=False), groups
            groups = [["slots", "speed", "memory", "thismac", "connect", "health", "model", *main, *side]]
            return indent(column(v, groups[0], d, cols - 1)), groups
        shown, groups = cards(v, main, side)
        # A plugin's card never pushes the LOG off the screen (user, 2026-11-03): where the rows left are fewer than
        # the RECENT REQUESTS and the LOG need, the plugins' cards the user has not set a level for (ui.opened) are
        # collapsed, and what the collapsed cards still add is taken from the RECENT REQUESTS (never from the LOG),
        # down to one row.
        loose = [c.name for c in v.plugin_cards if c.shrinks and c.name in (*main, *side) and c.name not in ui.opened]
        log_rows = ui.lines or (3 if height < 44 else 5 if height < 54 else 10)
        if loose and height - len(rows) - len(shown) < log_rows + 8:
            v = replace(v, level_override={**v.level_override, **{n: 0 for n in loose}})
            shown, groups = cards(v, main, side)
            req_floor = max(1, REQ_ROWS - (len(shown) - len(cards(v, [n for n in main if n not in loose],
                                                                   [n for n in side if n not in loose])[0])))
        rows += shown
        out.sections = [n for g in groups for n in g] + ["requests", "log"]
        out.keys = [("Tab", "section"), ("L", "level"), ("k", "show key"), ("o p c", "copy a config"), ("↑↓", "scroll")]
        out.keys_more = ["o p c copy a config and open the Connect tab. A click on a section's title changes its level.",
                        "+ and - show more or fewer log lines.",
                        "The mouse wheel scrolls. The SPEED averages count every request since the server started."]
    left = height - len(rows)
    log_n = (max(3, left - 3) if stopped else ui.lines or (3 if height < 44 else 5 if height < 54 else 10))
    # (stopped: the log fills the rows that are left; + - or --lines set it, else the screen's height does)
    if not stopped:
        n_req = max(req_floor, left - (log_n + 2) - 3) if v.level("requests") else 0
        req = card_requests(replace(v, detail="full" if v.level("requests") == 2 else "simple"), d, n_req, cols - 5)
        rows += indent(draw_card("requests", req.title, req.summary, req.lines, cols - 1, v.level("requests"), 3,
                                 ui.section == "requests"))
    lf = v.level("log") == 2
    errs = v.log.counts["E"]
    lsum = (f"{RED}{plural(errs, 'error')}{R}" if errs else "0 errors") + (
        f"   {DIM}{v.log_path.rsplit('/', 1)[-1]}{R}" if v.log_path else "")
    where = aligned([row("file", home_short(v.log_path, v.home))], cols - 5) if lf and v.log_path else []   # a row
    rows += indent(draw_card("log", "LOG", lsum, where + log_view(replace(v, detail="full" if lf else "simple"), log_n, cols - 5),
                             cols - 1, v.level("log"), 3, ui.section == "log"))
    out.revealed, top = reveal(ui.section, ui.revealed, rows, ui.scroll, height)
    top = out.fix("scroll", min(top, max(len(rows) - height, 0)))
    out.more = len(rows) > top + height
    return rows[top:top + height]


# ---------------------------------------------------------------- Requests and Log
def fitting(reqs: Sequence[RequestRecord], start: int, rows: int, each: int,
            more: Mapping[int, Sequence[InfoLine]]) -> int:
    """How many requests from start fit in rows (at least 1): each takes `each` rows, and one more for each line the
    plugins add under it (more: by its task; the full level)."""
    n = used = 0
    for r in reqs[start:]:
        used += each + (len(more.get(r.task, ())) if r.task is not None else 0)
        if used > rows:
            break
        n += 1
    return max(n, 1)


def body_requests(v: View, ui: UIState, cols: int, height: int, out: Optional[Shown] = None) -> List[Row]:
    """Every finished request in the log, newest first, with a mean row under the speeds; full adds the slot, where
    the reused tokens came from and the lines the plugins add under a request. Under it, the plugins' cards of the
    Requests tab (SLOT_REQUESTS), each a section of its own. The cards are no wider than the table."""
    out = Shown() if out is None else out
    full = ui.levels.get("reqtab", 1) == 2
    w = min(cols - 1, len(REQ_HEAD + REQ_MORE + (REQ_FULL if full else "")) + 6)
    said: List[Row] = []
    for c in v.request_cards:
        card = card_requests_plugin(c, w - 4)
        said += draw_card(c.name, card.title, card.summary, card.lines, w, ui.levels.get(c.name, 1), 2,
                          ui.section == c.name)
    reqs = list(reversed(v.log.requests))
    since = clock_text(v.log.epoch(reqs[-1].t0))[:5] if reqs else ""
    head = f"{len(reqs)} since {since}" if reqs else "none yet"
    head += f", {len(v.log.current)} running" if v.log.current else ""
    room = max(height - 5 - len(said), 3)
    if full:                # a narrow full level puts the slot on a second row, and a plugin's lines on others
        each = 1 if len(REQ_HEAD + REQ_MORE + REQ_FULL) <= w - 4 else 2
        room = fitting(reqs, ui.req_scroll, room, each, v.request_lines)
    scroll = out.fix("req_scroll", min(ui.req_scroll, max(len(reqs) - room, 0)))
    lines = req_table(reqs[scroll:scroll + room], lambda t: clock_text(v.log.epoch(t)), full, w - 4, mean=True,
                      more=v.request_lines) if reqs else [f"{DIM}No finished request yet.{R}"]
    out.sections, out.more = ["reqtab", *(c.name for c in v.request_cards)], len(reqs) > scroll + room
    out.levels += [c.name for c in v.request_cards]
    out.keys = [("↑↓ PgUp PgDn", "scroll"), ("L", "level")]
    out.keys_more = ["Each row is one call from a client (a turn makes many).",
                    "reused = the context minus the new tokens and the output: the tokens the server did not read again.",
                    "The mean row is of the requests in this list. The SPEED card on the Live tab counts every request "
                    "since the server started.", *(c.about for c in v.request_cards if c.about)]
    return indent(draw_card("reqtab", "REQUESTS", head, lines, w, ui.levels.get("reqtab", 1), 3, False) + said)


def body_log(v: View, ui: UIState, cols: int, n: int, out: Optional[Shown] = None) -> List[Row]:
    """The log, n lines, with its wrap / errors-and-warnings / back-to-the-end switches; full shows the times with
    milliseconds and the whole path."""
    out = Shown() if out is None else out
    full = ui.levels.get("logtab", 1) == 2
    switches = buttons("", [(f"Wrap lines: {'on' if ui.wrap else 'off'} (w)", make(Verb.WRAP)),
                            (f"Errors and warnings: {'on' if ui.errors_only else 'off'} (f)", make(Verb.ERRORS)),
                            (("Back to the end (End)" if ui.log_scroll else "At the end"), make(Verb.FOLLOW))])
    path = v.log_path or ""
    summ = f"{DIM}{path.rsplit('/', 1)[-1] or 'no log file'}{R}" + (f"   {ui.log_scroll} lines back" if ui.log_scroll else "")
    where = aligned([row("file", home_short(path, v.home))], cols - 5) if full and path else []
    lines: List[CardLine] = [switches, *where, "", *log_view(replace(v, detail="full" if full else "simple"), n - 2, cols - 5)]
    out.sections = ["logtab"]
    out.keys = [("w", "wrap lines"), ("f", "errors and warnings"), ("↑↓ PgUp PgDn", "scroll"), ("End", "back to the end"),
                ("L", "level")]
    out.keys_more = ["The server's own log (llama.cpp). Red: errors. Yellow: warnings. Dim: routine lines (normal at "
                    "any time).", f"The times are {computer()}'s clock; the full level adds milliseconds."]
    return indent(draw_card("logtab", "LOG", summ, lines, cols - 1, ui.levels.get("logtab", 1), 3, False))


# ---------------------------------------------------------------- dialogs
def centred(card: List[Row], cols: int, w: int, height: int) -> List[Row]:
    pad = (cols - w) // 2
    top = max((height - len(card)) // 2, 0)
    rows: List[Row] = [("", [])] * top
    return (rows + indent(card, pad))[:height]


def quit_dialog(d: ServerData, ui: UIState, cols: int, height: int, out: Optional[Shown] = None) -> List[Row]:
    """Quit: stop the server, leave it running, or cancel; one button per row."""
    pid = d.target_pid
    alive = pid and not d.exited and not ui.stopping
    w = min(72, cols - 4)
    lines: List[CardLine] = [""]
    if alive:
        what = d.alias or "The server"
        lines += [*cwrap(f"{B}{what}{R} is running. Stop it, or leave it running for OpenCode and Pi?", w - 4), ""]
        lines += [buttons("  ", [(t, a)]) for t, a in (("Stop the server and quit (s)", make(Verb.STOP)),
                                                       ("Leave it running and quit (l)", make(Verb.DETACH)),
                                                       ("Cancel (Esc)", make(Verb.CANCEL)))]
        lines += ["", *aligned([row("open again", f"Run {command()}."), *([row("server", f"pid {pid}")] if ui.full else [])],
                               w - 4)]
        keys: List[Key] = [("s", "stop the server and quit"), ("l", "leave it running and quit"), ("Esc", "cancel")]
    else:
        lines += ["No server is running.", "", buttons("  ", [("Quit (q)", make(Verb.DETACH))]), buttons("  ", [("Cancel (Esc)", make(Verb.CANCEL))])]
        keys = [("q", "quit"), ("Esc", "cancel")]
    if out is not None:
        out.keys = keys
    return centred(draw_card("quitbox", "QUIT", "", lines, w, 1), cols, w, height)


def sentence(text: str) -> str:
    """text as a sentence: a capital first, a full stop at the end."""
    t = text.strip()
    return (t[:1].upper() + t[1:] + ("" if t.endswith((".", "?", "!", ":")) else ".")) if t else t


def drain_dialog(dr: Drain, cols: int, height: int, ui: Optional[UIState] = None,
                 out: Optional[Shown] = None) -> List[Row]:
    """A client's turn runs while a step would stop the model: wait for the turn, stop now, or cancel. Each choice
    with what it does, in sentences."""
    w = min(84, cols - 4)
    tw = w - 4

    def slots(ids: List[int]) -> str:
        return ("slot " if len(ids) == 1 else "slots ") + ", ".join(str(i) for i in ids)
    if dr.busy:
        state = f"An agent is working: {slots(dr.busy)} is writing an answer."
        now_text = ("The answer stops. The client shows an error (Pi tries again). The session goes back to its last "
                    "save.")
    elif dr.turns:
        state = f"An agent is working: its turn is running in {slots(dr.turns)}, and a tool is running now."
        now_text = "The turn stops. The session goes back to its last save."
    else:
        state = "CARL is saving the session." if dr.saving else "The turn is ending."
        now_text = "CARL does not wait for the save."
    lines: List[CardLine] = ["", *cwrap(state, tw), *cwrap(f"Next step: {dr.what}.", tw), ""]

    def choice(label: str, act: str, what: str) -> List[CardLine]:
        return [buttons("  ", [(label, act)]), *[f"      {DIM}{x}{R}" for x in cwrap(what, tw - 6)]]
    if dr.waiting:
        lines = ["", *cwrap(f"CARL is waiting for the turn to end ({duration(time.time() - dr.since)} so far). Then it saves "
                            f"the session and does the next step: {dr.what}.", tw), *cwrap(state, tw), ""]
        lines += [*choice("Stop now (s)", make(Verb.DRAIN, DrainOp.NOW), now_text),
                  *choice("Cancel (Esc)", make(Verb.DRAIN, DrainOp.CANCEL), "Nothing changes."), "",
                  *cwrap(f"{DIM}A client without the CARL plugin does not mark its turns. For such a client, CARL waits "
                         f"until the slots are free.{R}", tw)]
        keys: List[Key] = [("s", "stop now"), ("Esc", "cancel")]
    else:
        lines += [*choice("Wait for the turn (w)", make(Verb.DRAIN, DrainOp.WAIT), "CARL waits for the turn to end and saves the session. "
                          "Then it does the next step."),
                  *choice("Stop now (s)", make(Verb.DRAIN, DrainOp.NOW), now_text),
                  *choice("Cancel (Esc)", make(Verb.DRAIN, DrainOp.CANCEL), "Nothing changes.")]
        keys = [("w", "wait for the turn"), ("s", "stop now"), ("Esc", "cancel")]
    if out is not None:
        out.keys = keys
    return centred(draw_card("drainbox", "AN AGENT IS WORKING", "", lines, w, 1), cols, w, height)
