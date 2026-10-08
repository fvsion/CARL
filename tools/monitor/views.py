"""The bodies of the Live, Requests and Log tabs (Connect: views_connect.py), the ? card, the quit dialog and the
"a turn runs" dialog. Pure: they draw from a View, the UI state and the snapshot (they clamp scroll
positions and set the keys of the screen they draw: ui.keys for the footer and the ? card)."""
from __future__ import annotations

import time
from dataclasses import replace
from typing import List, Optional, Sequence, Tuple

from carl_core.domain.units import duration

from .cards import (REQ_FULL, REQ_HEAD, REQ_MORE, STOPPED, View, card_requests, card_stopped, column, live_sentence, log_view,
                    req_table, status_of, wrapped)
from .fmt import (reveal, B, DIM, R, RED, CardLine, Key, Row, aligned, buttons, cwrap,
                  draw_card, row,
                  heading, home_short, indent, side_by_side)
from .model import ServerData
from .state import Drain, UIState
from .words import plural
from .views_connect import body_connect, clients_lines  # noqa: F401  (app.py and the tests import them from here)

EVERY_SCREEN: List[Key] = [("1-5", "the tabs"), ("Tab Shift-Tab", "select a section"), ("L", "its level: simple, full, "
                            "collapsed"), ("D", "every section: simple or full (saved)"), ("space", "read the server "
                            "again now"), ("?", "this list"),
                           ("q", "quit (CARL asks first)")]
TWO_COLUMNS = 100           # the Live tab puts its cards in two columns from this width
THREE_COLUMNS = 190         # ... and in three from this one (three of 52 at 160 wrap more than two of 78)


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


def body_live(v: View, ui: UIState, d: ServerData, cols: int, height: int) -> List[Row]:
    """The state in one sentence, then the cards in 1, 2 or 3 columns (live_columns), then RECENT REQUESTS and
    the LOG, which take the rows left (the page scrolls when it is taller than the screen). No server: SERVER
    and THIS MAC, then the log."""
    stopped = status_of(d, v.server_pid)[0] == STOPPED
    rows: List[Row] = [] if stopped else [(" " + x, list()) for x in cwrap(live_sentence(d, v.port), cols - 2)]
    rows.append(("", []))
    ncol = live_columns(cols)
    v.selected = ui.section
    if stopped:
        card = card_stopped(v, d)
        cw = (cols - 3) // 2 if ncol > 1 else cols - 1
        server = draw_card("server", card.title, card.summary, wrapped(aligned(card.lines, cw - 4), cw - 4), cw,
                           v.level("server"), 2, ui.section == "server")
        mac = column(v, ["thismac"], d, cols - 3 - cw if ncol > 1 else cols - 1)
        rows += side_by_side(server, mac, cw, pad_left=False) if ncol > 1 else indent(server + mac)
        ui.sections = ["server", "thismac", "log"]
        ui.keys = [("a", "start the server"), ("5", "settings"), ("4", "log"), ("Tab", "section"), ("L", "level")]
        ui.keys_more = ["a opens Settings > Server and asks before it starts the server."]
    else:
        if ncol == 3:
            cw = (cols - 4) // 3
            groups = [["slots", "memory"], ["speed", "health", "model"], ["thismac", "connect"]]
            c1, c2 = column(v, groups[0], d, cw), column(v, groups[1], d, cw)
            c3 = column(v, groups[2], d, cols - 4 - 2 * cw)
            rows += side_by_side(c1, side_by_side(c2, c3, cw, pad_left=False), cw, pad_left=False)
        elif ncol == 2:
            cw = (cols - 3) // 2
            groups = [["slots", "memory", "thismac"], ["speed", "connect", "health", "model"]]
            rows += side_by_side(column(v, groups[0], d, cw), column(v, groups[1], d, cols - 3 - cw), cw, pad_left=False)
        else:
            groups = [["slots", "speed", "memory", "thismac", "connect", "health", "model"]]
            rows += indent(column(v, groups[0], d, cols - 1))
        ui.sections = [n for g in groups for n in g] + ["requests", "log"]
        ui.keys = [("Tab", "section"), ("L", "level"), ("k", "show key"), ("o p c", "copy a config"), ("↑↓", "scroll")]
        ui.keys_more = ["o p c copy a config and open the Connect tab. A click on a section's title changes its level.",
                        "+ and - show more or fewer log lines.",
                        "The mouse wheel scrolls. The SPEED averages count every request since the server started."]
    left = height - len(rows)
    log_n = (max(3, left - 3) if stopped else ui.lines or (3 if height < 44 else 5 if height < 54 else 10))
    # (stopped: the log fills the rows that are left; + - or --lines set it, else the screen's height does)
    if not stopped:
        n_req = max(3, left - (log_n + 2) - 3) if v.level("requests") else 0
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
    reveal(ui, rows, "scroll", height)
    ui.scroll = min(ui.scroll, max(len(rows) - height, 0))
    ui.more = len(rows) > ui.scroll + height
    return rows[ui.scroll:ui.scroll + height]


# ---------------------------------------------------------------- Requests and Log
def body_requests(v: View, ui: UIState, cols: int, height: int) -> List[Row]:
    """Every finished request in the log, newest first, with a mean row under the speeds; full adds the slot and
    where the reused tokens came from. The card is no wider than its table."""
    full = ui.levels.get("reqtab", 1) == 2
    reqs = list(reversed(v.log.requests))
    since = v.log.wall(reqs[-1].t0)[:5] if reqs else ""
    head = f"{len(reqs)} since {since}" if reqs else "none yet"
    head += f", {len(v.log.current)} running" if v.log.current else ""
    room = max(height - 5, 3)
    ui.req_scroll = min(ui.req_scroll, max(len(reqs) - room, 0))
    w = min(cols - 1, len(REQ_HEAD + REQ_MORE + (REQ_FULL if full else "")) + 6)
    lines = req_table(reqs[ui.req_scroll:ui.req_scroll + room], v.log.wall, full, w - 4, mean=True) if reqs else \
        [f"{DIM}No finished request yet.{R}"]
    ui.sections, ui.more = ["reqtab"], len(reqs) > ui.req_scroll + room
    ui.keys = [("↑↓ PgUp PgDn", "scroll"), ("L", "level")]
    ui.keys_more = ["Each row is one call from a client (a turn makes many).",
                    "reused = the context minus the new tokens and the output: the tokens the server did not read again.",
                    "The mean row is of the requests in this list. The SPEED card on the Live tab counts every request "
                    "since the server started."]
    return indent(draw_card("reqtab", "REQUESTS", head, lines, w, ui.levels.get("reqtab", 1), 3, False))


def body_log(v: View, ui: UIState, cols: int, n: int) -> List[Row]:
    """The log, n lines, with its wrap / errors-and-warnings / back-to-the-end switches; full shows the times with
    milliseconds and the whole path."""
    full = ui.levels.get("logtab", 1) == 2
    switches = buttons("", [(f"Wrap lines: {'on' if ui.wrap else 'off'} (w)", "wrap"),
                            (f"Errors and warnings: {'on' if ui.errors_only else 'off'} (f)", "errors"),
                            (("Back to the end (End)" if ui.log_scroll else "At the end"), "follow")])
    path = v.log_path or ""
    summ = f"{DIM}{path.rsplit('/', 1)[-1] or 'no log file'}{R}" + (f"   {ui.log_scroll} lines back" if ui.log_scroll else "")
    where = aligned([row("file", home_short(path, v.home))], cols - 5) if full and path else []
    lines: List[CardLine] = [switches, *where, "", *log_view(replace(v, detail="full" if full else "simple"), n - 2, cols - 5)]
    ui.sections = ["logtab"]
    ui.keys = [("w", "wrap lines"), ("f", "errors and warnings"), ("↑↓ PgUp PgDn", "scroll"), ("End", "back to the end"),
               ("L", "level")]
    ui.keys_more = ["The server's own log (llama.cpp). Red: errors. Yellow: warnings. Dim: routine lines (normal at "
                    "any time).", "The times are this Mac's clock; the full level adds milliseconds."]
    return indent(draw_card("logtab", "LOG", summ, lines, cols - 1, ui.levels.get("logtab", 1), 3, False))


# ---------------------------------------------------------------- dialogs
def centred(card: List[Row], cols: int, w: int, height: int) -> List[Row]:
    pad = (cols - w) // 2
    top = max((height - len(card)) // 2, 0)
    rows: List[Row] = [("", [])] * top
    return (rows + indent(card, pad))[:height]


def quit_dialog(d: ServerData, ui: UIState, cols: int, height: int) -> List[Row]:
    """Quit: stop the server, leave it running, or cancel; one button per row."""
    pid = d.target_pid
    alive = pid and not d.exited and not ui.stopping
    w = min(72, cols - 4)
    lines: List[CardLine] = [""]
    if alive:
        what = d.alias or "The server"
        lines += [*cwrap(f"{B}{what}{R} is running. Stop it, or leave it running for OpenCode and Pi?", w - 4), ""]
        lines += [buttons("  ", [(t, a)]) for t, a in (("Stop the server and quit (s)", "stop"),
                                                       ("Leave it running and quit (l)", "detach"),
                                                       ("Cancel (Esc)", "cancel"))]
        lines += ["", *aligned([row("open again", "Run ./carl.sh."), *([row("server", f"pid {pid}")] if ui.full else [])],
                               w - 4)]
        ui.keys = [("s", "stop the server and quit"), ("l", "leave it running and quit"), ("Esc", "cancel")]
    else:
        lines += ["No server is running.", "", buttons("  ", [("Quit (q)", "detach")]), buttons("  ", [("Cancel (Esc)", "cancel")])]
        ui.keys = [("q", "quit"), ("Esc", "cancel")]
    return centred(draw_card("quitbox", "QUIT", "", lines, w, 1), cols, w, height)


def sentence(text: str) -> str:
    """text as a sentence: a capital first, a full stop at the end."""
    t = text.strip()
    return (t[:1].upper() + t[1:] + ("" if t.endswith((".", "?", "!", ":")) else ".")) if t else t


def drain_dialog(dr: Drain, cols: int, height: int, ui: Optional[UIState] = None) -> List[Row]:
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
        lines += [*choice("Stop now (s)", "drain:now", now_text), *choice("Cancel (Esc)", "drain:cancel",
                                                                          "Nothing changes."), "",
                  *cwrap(f"{DIM}A client without the CARL plugin does not mark its turns. For such a client, CARL waits "
                         f"until the slots are free.{R}", tw)]
        keys: List[Key] = [("s", "stop now"), ("Esc", "cancel")]
    else:
        lines += [*choice("Wait for the turn (w)", "drain:wait", "CARL waits for the turn to end and saves the session. "
                          "Then it does the next step."),
                  *choice("Stop now (s)", "drain:now", now_text),
                  *choice("Cancel (Esc)", "drain:cancel", "Nothing changes.")]
        keys = [("w", "wait for the turn"), ("s", "stop now"), ("Esc", "cancel")]
    if ui is not None:
        ui.keys = keys
    return centred(draw_card("drainbox", "AN AGENT IS WORKING", "", lines, w, 1), cols, w, height)
