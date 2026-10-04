"""The bodies of the Overview, Connect, Requests and Log tabs, and the quit dialog. Pure:
they draw from a View, the UI state and the snapshot (they clamp scroll positions)."""
from __future__ import annotations

import dataclasses
import time
from typing import List, Optional, Sequence, Tuple

from .cards import REQ_HEAD, View, card_connect, card_requests, column, log_view, req_row
from .fmt import (B, CYN, DIM, GRN, R, RED, YEL, Card, CardLine, Row, buttons, cwrap, draw_card, fit, home_short, indent,
                  knum, lv, side_by_side)
from .model import ServerData, clean
from .clients import Drift
from .state import CONNECT_SUBPANELS, InstallRun, UIState

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
    top: List[Row] = [connect_bar(ui.connect_sp), ("", [])]
    height -= 2
    if ui.connect_sp == 1:
        return (top + indent(draw_card("clients", "CLIENTS", f"{DIM}{len(v.clients)} that sync · this Mac{R}",
                                       clients_lines(v, here, stale, tw), w, 2)))[:height + 2]
    full = dataclasses.replace(v, level_override={"connect": 2})
    rows = _card(full, "connect", card_connect(full, d)._replace(title="CONNECTION"), w, 2)
    rows += draw_card("guide", "SET UP OPENCODE AND PI", "", setup_lines(v, ui, here, tw, stale, installed), w,
                      v.level("guide"))
    room = max(height - len(rows) - 3, 3)
    if ui.install and ui.install_shown:
        rows += draw_card("install", "INSTALLER", install_summary(ui.install), install_lines(ui, room, tw), w, 2)
        return (top + indent(rows))[:height + 2]
    kind = ui.preview
    note = f"{GRN}copied to the clipboard ✓{R}" if ui.copied == kind else f"{DIM}click its button (or o/p/t) to copy{R}"
    plines = preview.splitlines()
    ui.prev_scroll = min(ui.prev_scroll, max(len(plines) - room, 0))
    shown: List[CardLine] = [f"{DIM}{PREVIEW_WHERE[kind]}{R}", *plines[ui.prev_scroll:ui.prev_scroll + room - 1]]
    rows += draw_card("preview", PREVIEW_TITLES[kind], note, shown, w, v.level("preview"))
    return (top + indent(rows))[:height + 2]


def connect_bar(sp: int) -> Row:
    """The Setup / Clients switch, the selected one in reverse video."""
    text, spans, col = "", [], 0
    for i, name in enumerate(CONNECT_SUBPANELS):
        lab = f" {name} "
        text += (f"\x1b[1;7m{lab}{R}" if i == sp else f"{DIM}{lab}{R}") + " "
        spans.append((1 + col, 1 + col + len(lab), f"csp:{i}"))
        col += len(lab) + 1
    return " " + text + f"{DIM}  press [ or ] to switch{R}", spans


def _ago(t: float, now: float) -> str:
    s = max(0, int(now - t))
    return f"{s} s" if s < 90 else f"{s // 60} min" if s < 5400 else f"{s // 3600} h" if s < 172800 else f"{s // 86400} days"


def clients_lines(v: View, here: List[Tuple[str, str]], stale: Sequence[Drift], tw: int) -> List[CardLine]:
    """Every client: this Mac's configs, then each one that syncs (connected or last seen, its config
    against the pushed one), the push and its button."""
    now = time.time()
    pushed = v.pushed.split(" at ")[0] if v.pushed else ""
    L: List[CardLine] = [*cwrap(f"{DIM}The clients this server knows: this Mac's OpenCode and Pi (their configs follow "
                                f"./carl.sh install and the Connect tab's Update), and every computer whose installer "
                                f"set up the sync (a copied client folder: it pulls the config you push).{R}", tw), "",
                         lv("pushed", f"{v.pushed}" if v.pushed else f"{DIM}nothing yet{R}"),
                         lv("API", f"{GRN}{v.api}{R}" if v.api else f"{YEL}not running{R}"), ""]
    mac = (f"{GRN}configs up to date{R}" if here and not stale else f"{YEL}configs out of date: Setup, u{R}" if here
           else f"{DIM}not set up for this server{R}")
    L.append(f"  {B}this Mac{R}  {DIM}OpenCode, Pi{R}  {mac}")
    for c in v.clients:
        link = (f"{GRN}● connected{R}" if c.connected > 0 else
                f"{DIM}○ last seen {_ago(c.last_seen, now)} ago{R}")
        how = "sync service" if c.mode == "service" else "checks at start"
        if not pushed:
            conf = f"{DIM}nothing pushed{R}"
        elif c.applied == pushed:
            conf = f"{GRN}up to date ({pushed}){R}"
        else:
            conf = f"{YEL}has {c.applied or 'none'}, pushed {pushed}{' (auto-apply off: it waits)' if not c.auto_apply else ''}{R}"
        L += cwrap(f"  {B}{c.host or c.id}{R}  {DIM}{c.user} · {c.os} · {c.address} · {how}{R}  {link}  {conf}", tw, "    ")
    if not v.clients:
        L += cwrap(f"  {DIM}no other computer yet: copy the client folder to it and run ./install-clients.sh && "
                   f"./install.sh there (the folder carries the address and the key).{R}", tw, "  ")
    L += ["", buttons("  ", [("Push config to clients (P)", "inspush"), ("Forget clients not seen for a week", "clforget")])]
    return L


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
               f"{DIM}there. The folder carries the server's address and key (remote.json, api-key: written at every "
               f"server start), so the installer needs no arguments.{R}", tw)
    L += cwrap(f"{B}Clients on other computers:{R} {DIM}the installer there adds a sync service: it keeps one connection "
               f"to this dashboard (it opens no port on the client) and applies the config you push, with backups. "
               f"{R}" + (f"{GRN}{v.listeners} connected{R}" if v.api else f"{YEL}the dashboard's API is not running{R}")
               + f"{DIM} · the Clients tab (]) lists them{R}", tw)
    L.append(buttons("  ", [("Push config to clients (P)", "inspush")]))
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
