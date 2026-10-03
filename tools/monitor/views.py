"""The bodies of the Overview, Connect, Requests and Log tabs, and the quit dialog. Pure:
they draw from a View, the UI state and the snapshot (they clamp scroll positions)."""
from __future__ import annotations

import dataclasses
from typing import List, Optional, Sequence, Tuple

from .cards import REQ_HEAD, View, card_connect, card_requests, column, log_view, req_row
from .fmt import (B, CYN, DIM, GRN, R, RED, YEL, Card, CardLine, Row, buttons, cwrap, draw_card, fit, home_short, indent,
                  knum, side_by_side)
from .model import ServerData, clean
from .clients import Drift
from .state import InstallRun, UIState

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
                 here: List[Tuple[str, str]], preview: str, stale: Sequence[Drift] = (), installed: int = 0) -> List[Row]:
    """The connection in full, how to set up clients (Install runs ./carl.sh install), a warning
    when a client config lists other models than are installed (stale), and the installer's
    output or the selected config (preview text)."""
    w = cols - 1
    tw = w - 4
    full = dataclasses.replace(v, level_override={"connect": 2})
    rows = _card(full, "connect", card_connect(full, d)._replace(title="CONNECTION"), w, 2)
    rows += draw_card("guide", "SET UP OPENCODE AND PI", "", setup_lines(v, ui, here, tw, stale, installed), w,
                      v.level("guide"))
    room = max(height - len(rows) - 3, 3)
    if ui.install and ui.install_shown:
        rows += draw_card("install", "INSTALLER", install_summary(ui.install), install_lines(ui, room, tw), w, 2)
        return indent(rows)[:height]
    kind = ui.preview
    note = f"{GRN}copied to the clipboard ✓{R}" if ui.copied == kind else f"{DIM}click its button (or o/p/t) to copy{R}"
    plines = preview.splitlines()
    ui.prev_scroll = min(ui.prev_scroll, max(len(plines) - room, 0))
    shown: List[CardLine] = [f"{DIM}{PREVIEW_WHERE[kind]}{R}", *plines[ui.prev_scroll:ui.prev_scroll + room - 1]]
    rows += draw_card("preview", PREVIEW_TITLES[kind], note, shown, w, v.level("preview"))
    return indent(rows)[:height]


def setup_lines(v: View, ui: UIState, here: List[Tuple[str, str]], tw: int, stale: Sequence[Drift] = (),
                installed: int = 0) -> List[CardLine]:
    """This Mac: one command (or the Install button), and a warning with Update configs when the
    installed models changed; a VM: start the server with --vm, then the installer there; by
    hand: the copy buttons."""
    L: List[CardLine] = []
    for dr in stale:
        L += cwrap(f"{YEL}⚠ Out of date: {dr.line(installed)}.{R} {DIM}Press u (Update configs only) to list the "
                   f"installed models; a VM client: copy the client folder again and run ./install.sh there.{R}", tw)
    if stale:
        L.append("")
    elif here:
        L += cwrap(f"{GRN}✓ This Mac is set up{R} for this server (" + ", ".join(f"{c}: provider {p}" for c, p in here)
                   + f"). {DIM}Run the installer again after a new model or a context / slots change.{R}", tw)
    L += cwrap(f"{B}On this Mac:{R} one command, {CYN}./carl.sh install{R} {DIM}— it installs OpenCode and Pi when "
               f"they are missing (into ~/.local, no sudo) and points them at this server. Your own providers, "
               f"default model and agents are kept, and a backup is written first.{R}", tw)
    if ui.install_ask:
        what = ("install OpenCode and Pi when missing (downloads from npm), then write their configs"
                if ui.install_ask == "all" else "write the OpenCode and Pi configs for this server")
        L += cwrap(f"{YEL}Run ./carl.sh install{' --config-only' if ui.install_ask == 'config' else ''} now? It will "
                   f"{what}.{R}", tw)
        L.append(buttons("  ", [("Yes, run it (y)", "insyes"), ("Cancel (n)", "insno")]))
    elif ui.install and not ui.install.done:
        L.append(buttons("  ", [("Show the installer (i)", "insshow"), ("Stop it", "inscancel")]))
    else:
        L.append(buttons("  ", [("Install on this Mac (i)", "insall"), ("Update configs only (u)", "insconfig")]))
    vm_ready = v.host not in ("127.0.0.1", "::1", "localhost")
    L += cwrap(f"{B}In a VM:{R} {DIM}start the server for the VM with{R} {CYN}./carl.sh --vm{R} "
               + (f"{GRN}(it is: {v.host}){R}" if vm_ready else f"{YEL}(now it listens on this Mac only){R}")
               + f"{DIM}, copy the client folder into the VM, then run{R} {CYN}./install-clients.sh && ./install.sh{R} "
               f"{DIM}there.{R}", tw)
    L += cwrap(f"{B}By hand:{R} {DIM}a provider block only (id carl), copied to the clipboard; it adds, never "
               f"replaces.{R}", tw)
    L.append(buttons("  ", [("OpenCode config (o)", "opencode"), ("Pi config (p)", "pi"), ("curl test (t)", "curl")]))
    return L


def install_summary(ins: InstallRun) -> str:
    """The installer card's title info: what runs and how it ended."""
    if not ins.done:
        return f"{YEL}running: {ins.what}…{R}"
    ok = ins.proc.returncode == 0
    return f"{GRN}done: {ins.what}{R}" if ok else f"{RED}failed (exit {ins.proc.returncode}){R}"


def install_lines(ui: UIState, room: int, tw: int) -> List[CardLine]:
    """The installer's last output lines (control characters removed), and its buttons."""
    ins = ui.install
    if ins is None:
        return []
    out: List[CardLine] = [f"{DIM}{fit(clean(x), tw)}{R}" for x in ins.lines if x.strip()]
    out = out[-max(room - 2, 1):]
    if not ins.lines:
        out.append(f"{DIM}starting…{R}")
    out.append(buttons("", [("Stop it", "inscancel")] if not ins.done else
                       [("Close (x)", "insclose"), ("Run again (i)", "insall")]))
    return out


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
