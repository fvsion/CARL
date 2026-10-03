"""The bodies of the Overview, Connect, Requests and Log tabs, and the quit dialog. Pure:
they draw from a View, the UI state and the snapshot (they clamp scroll positions)."""
from __future__ import annotations

import dataclasses
from typing import List, Optional, Tuple

from .cards import REQ_HEAD, View, card_connect, card_requests, column, log_view, req_row
from .fmt import B, DIM, GRN, R, Card, CardLine, Row, buttons, draw_card, home_short, indent, knum, side_by_side
from .model import ServerData
from .state import UIState

PREVIEW_TITLES = {"opencode": "OPENCODE CONFIG", "pi": "PI CONFIG", "curl": "CURL TEST"}
PREVIEW_WHERE = {
    "opencode": "→ add under \"provider\" in ~/.config/opencode/opencode.json (keep your other providers), restart OpenCode, "
                "pick it with /models",
    "pi": "→ add under \"providers\" in ~/.pi/agent/models.json (keep yours), then /model in Pi",
    "curl": "→ run anywhere that can reach the server"}


def _card(v: View, name: str, card: Card, w: int, lvl: Optional[int] = None) -> List[Row]:
    return draw_card(name, card.title, card.summary, card.lines, w, v.level(name) if lvl is None else lvl)


def body_overview(v: View, ui: UIState, d: ServerData, cols: int, height: int, log_name: Optional[str]) -> List[Row]:
    """Two columns of cards (one when narrow), recent requests and the end of the log.
    log_name: the log file's name (the link target's)."""
    if cols >= 100:
        cw = (cols - 3) // 2
        left = column(v, ["connect", "context", "memory"], d, cw)
        right = column(v, ["activity", "model", "health", "system"], d, cols - 3 - cw)
        rows = side_by_side(left, right, cw, pad_left=False)
    else:
        rows = indent(column(v, ["connect", "activity", "context", "memory", "model", "health", "system"], d, cols - 1))
    rows += indent(_card(v, "requests", card_requests(v, d, 3 if v.level("requests") == 1 else 8), cols - 1))
    lt = f"{DIM}{log_name} · tab 4 for the full log{R}" if log_name else f"{DIM}no log file{R}"
    rows += indent(draw_card("log", "LOG", lt, log_view(v, ui.lines, cols - 5), cols - 1, v.level("log")))
    ui.scroll = min(ui.scroll, max(len(rows) - height, 0))
    return rows[ui.scroll:ui.scroll + height]


def body_connect(v: View, ui: UIState, d: ServerData, cols: int, height: int,
                 here: List[Tuple[str, str]], preview: str) -> List[Row]:
    """The connection in full, how to set up clients, and the selected config (preview text)."""
    w = cols - 1
    full = dataclasses.replace(v, level_override={"connect": 2})
    rows = _card(full, "connect", card_connect(full, d)._replace(title="CONNECTION"), w, 2)
    guide: List[CardLine] = []
    if here:
        guide.append(f"{GRN}✓ This Mac:{R} already configured by install.sh for this server ("
                     + ", ".join(f"{c}: provider {p}" for c, p in here) + "). Nothing to paste here.")
    guide += [f"{B}Clients in the VM:{R}   copy the bundle in, then ./install-clients.sh && ./install.sh",
              f"{B}Clients on this Mac:{R} ./client/install-clients.sh && ./client/install.sh --local",
              f"{DIM}The installer merges without overwriting your providers, default model or agents, and adds the "
              f"coder, sidebar and session switcher.{R}",
              f"{B}By hand:{R}             a provider block only (id carl), copied to the clipboard; it adds, never replaces",
              buttons("", [("OpenCode config", "opencode"), ("Pi config", "pi"), ("curl test", "curl")])]
    rows += draw_card("guide", "CLIENT SETUP", "", guide, w, v.level("guide"))
    kind = ui.preview
    note = f"{GRN}copied to the clipboard ✓{R}" if ui.copied == kind else f"{DIM}click its button (or o/p/t) to copy{R}"
    plines = preview.splitlines()
    room = max(height - len(rows) - 3, 3)
    ui.prev_scroll = min(ui.prev_scroll, max(len(plines) - room, 0))
    shown: List[CardLine] = [f"{DIM}{PREVIEW_WHERE[kind]}{R}", *plines[ui.prev_scroll:ui.prev_scroll + room - 1]]
    rows += draw_card("preview", PREVIEW_TITLES[kind], note, shown, w, v.level("preview"))
    return indent(rows)[:height]


def body_requests(v: View, ui: UIState, cols: int, height: int) -> List[Row]:
    """Every finished request in the log, newest first, with averages."""
    reqs = list(reversed(v.log.requests))
    done = [r for r in reqs if r.tg]
    avg_pp = sum(r.pp or 0 for r in done) / len(done) if done else 0
    avg_tg = sum(r.tg or 0 for r in done) / len(done) if done else 0
    head = (f"{len(reqs)} in this log · avg read {avg_pp:.0f} tok/s · avg generate {avg_tg:.1f} tok/s"
            + (f" · {len(v.log.current)} running" if v.log.current else ""))
    room = max(height - 4, 3)
    ui.req_scroll = min(ui.req_scroll, max(len(reqs) - room, 0))
    lines: List[CardLine] = [f"{DIM}{REQ_HEAD}  {'prompt from cache':>17}{R}"]
    for r in reqs[ui.req_scroll:ui.req_scroll + room]:
        cached = r.ctx - r.new - r.gen
        lines.append(req_row(r, v.log.wall) + f"  {knum(max(cached, 0)):>17}")
    if not reqs:
        lines.append(f"{DIM}no finished requests yet{R}")
    return indent(draw_card("requests_tab", "REQUESTS", head, lines, cols - 1, v.level("requests_tab")))


def body_log(v: View, ui: UIState, cols: int, n: int) -> List[Row]:
    """The log, n lines, with its wrap / errors-only / follow switches."""
    switches = buttons("", [(f"wrap {'on' if ui.wrap else 'off'} (w)", "wrap"),
                            (f"errors only {'on' if ui.errors_only else 'off'} (f)", "errors"),
                            (("follow (end)" if ui.log_scroll else "following"), "follow")])
    summ = (f"{DIM}{home_short(v.log_path or '', v.home)}" + (f" · {ui.log_scroll} lines back" if ui.log_scroll else "") + R)
    lines: List[CardLine] = [switches, *log_view(v, n, cols - 5)]
    return indent(draw_card("logtab", "LOG", summ, lines, cols - 1, v.level("logtab")))


def quit_dialog(d: ServerData, ui: UIState, cols: int, height: int) -> List[Row]:
    """Quit: stop the server, leave it running, or cancel."""
    pid = d.target_pid
    alive = pid and not d.exited and not ui.stopping
    w = min(70, cols - 4)
    lines: List[CardLine] = [""]
    if alive:
        lines += [f"The server (pid {pid}) is still running.", "",
                  buttons("  ", [("Stop server (s)", "stop"), ("Leave it running (d)", "detach"), ("Cancel (Esc)", "cancel")]),
                  "", f"{DIM}Leave it running: it keeps serving; re-attach with ./carl.sh monitor{R}"]
    else:
        lines += ["The server isn't running.", "", buttons("  ", [("Quit (q)", "detach"), ("Cancel (Esc)", "cancel")])]
    card = draw_card("quitbox", "QUIT", "", lines, w, 1)
    pad = (cols - w) // 2
    top = max((height - len(card)) // 2, 0)
    rows: List[Row] = [("", [])] * top
    return (rows + indent(card, pad))[:height]
