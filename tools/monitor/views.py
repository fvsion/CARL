"""The bodies of the Overview, Connect, Requests and Log tabs, the quit dialog and the running-turn dialog. Pure:
they draw from a View, the UI state and the snapshot (they clamp scroll positions)."""
from __future__ import annotations

import dataclasses
import time
from typing import Dict, List, Optional, Sequence, Tuple

from .cards import REQ_HEAD, View, card_connect, card_requests, column, log_view, req_row
from .fmt import (B, CYN, DIM, GRN, R, RED, YEL, Card, CardLine, Key, Row, button_rows, buttons, cwrap, draw_card, fit,
                  heading, home_short, indent, knum, lv, side_by_side, with_side)
from .model import ServerData, clean
from .clients import Drift
from .state import CONNECT_SUBPANELS, Drain, InstallRun, UIState

# the keys of each tab (the footer); a Settings panel and the Connect sub-tabs set their own as they draw
TAB_KEYS: Dict[int, List[Key]] = {
    0: [("click a title", "more / less"), ("e c", "expand / collapse all"), ("o p t", "copy configs"), ("k", "show key")],
    1: [("[ ]", "Setup / Clients")],
    2: [("↑↓ PgUp PgDn", "scroll")],
    3: [("w", "wrap"), ("f", "errors only"), ("↑↓ PgUp PgDn", "scroll")],
    4: [("[ ]", "panels")],
}
EVERY_TAB: List[Key] = [("1-5 Tab", "tabs"), ("click a card title", "more / less detail"), ("e c", "expand / collapse all"),
                        ("k", "show / hide the API key"), ("o p t", "copy the OpenCode / Pi / curl config"),
                        ("space", "refresh now"), ("↑↓ PgUp PgDn", "scroll (the mouse wheel too)"), ("q", "quit (asks)"),
                        ("?", "this list")]


def keys_card(keys: Sequence[Key], more: Sequence[str], w: int) -> List[Row]:
    """The ? card: the keys of the panel shown, what else it does, then the keys of every tab."""
    def table(ks: Sequence[Key]) -> List[CardLine]:
        kw = max((len(k) for k, _ in ks), default=0) + 2
        return [f"  {B}{k:<{kw}}{R}{what}" for k, what in ks]
    L: List[CardLine] = [heading("This panel", w - 4), *table(keys)]
    L += [x for m in more for x in cwrap(f"{DIM}{m}{R}", w - 4)]
    L += ["", heading("Every tab", w - 4), *table(EVERY_TAB)]
    return draw_card("keys", "KEYS", f"{DIM}? closes{R}", L, w, 2)


PREVIEW_TITLES = {"opencode": "OPENCODE CONFIG", "pi": "PI CONFIG", "curl": "CURL TEST"}
PREVIEW_WHERE = {
    "opencode": "→ Add it under \"provider\" in ~/.config/opencode/opencode.json (keep the others). Restart OpenCode. "
                "Use /models.",
    "pi": "→ Add it under \"providers\" in ~/.pi/agent/models.json (keep the others). Then use /model in Pi.",
    "curl": "→ Run it on a computer that can connect to the server."}


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
        ui.keys = [("P", "push config to clients"), ("[ ]", "Setup / Clients")]
        ui.keys_more = ["The Forget button removes the clients not seen for a week. It does not ask first. A client "
                        "comes back when it syncs."]
        return (top + indent(draw_card("clients", "CLIENTS", f"{DIM}{len(v.clients)} that sync · this Mac{R}",
                                       clients_lines(v, here, stale, tw), w, 2)))[:height + 2]
    ui.keys = [("i", "install here"), ("u", "update configs"), ("P", "push to clients"), ("o p t", "copy a config"),
               ("k", "show key"), ("[ ]", "Setup / Clients")]
    ui.keys_more = ["Click a card title for more or less detail. To scroll the config below, use ↑↓ PgUp PgDn."]
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
    where = cwrap(f"{DIM}{PREVIEW_WHERE[kind]}{R}", tw)
    shown: List[CardLine] = [*where, *plines[ui.prev_scroll:ui.prev_scroll + max(room - len(where), 1)]]
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
    return " " + text + f"{DIM}  press [ or ] to change{R}", spans


def _ago(t: float, now: float) -> str:
    s = max(0, int(now - t))
    return f"{s} s" if s < 90 else f"{s // 60} min" if s < 5400 else f"{s // 3600} h" if s < 172800 else f"{s // 86400} days"


def clients_lines(v: View, here: List[Tuple[str, str]], stale: Sequence[Drift], tw: int) -> List[CardLine]:
    """Every client: this Mac's configs, then each one that syncs (connected or last seen, its config
    against the pushed one), the push and its button; beside them what the list is and how to add one."""
    now = time.time()
    pushed = v.pushed.split(" at ")[0] if v.pushed else ""

    def main(mw: int) -> List[CardLine]:
        L: List[CardLine] = [lv("pushed", f"{v.pushed}" if v.pushed else f"{DIM}nothing yet{R}"),
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
                conf = (f"{YEL}has {c.applied or 'none'}, pushed {pushed}"
                        f"{' (auto-apply off: it waits)' if not c.auto_apply else ''}{R}")
            L += cwrap(f"  {B}{c.host or c.id}{R}  {DIM}{c.user} · {c.os} · {c.address} · {how}{R}  {link}  {conf}", mw, "    ")
        if not v.clients:
            L.append(f"  {DIM}no other computer yet{R}")
        return [*L, "", *button_rows("  ", [("Push config to clients (P)", "inspush"),
                                             ("Forget clients not seen for a week", "clforget")], mw)]

    tip = ("Press P to send the installed models to all computers that sync." if v.clients
           else "Copy the client folder to a different computer. Then run its installer there.")
    return with_side(main, tip, [
        ("Who is in the list", ["The list shows OpenCode and Pi on this Mac. Their configs follow ./carl.sh install "
                                "and the Update button in Setup. The list also shows each computer whose installer set "
                                "up the sync. "
                                "That computer has a copy of the client folder, and it gets the config that you push."]),
        ("Add a computer", [f"Copy the client folder to the computer. Then run {CYN}./install-clients.sh && "
                            f"./install.sh{R} there. The folder contains the address and the key of the server."])],
        tw, main_w=100)


def setup_lines(v: View, ui: UIState, here: List[Tuple[str, str]], tw: int, stale: Sequence[Drift] = (),
                installed: int = 0) -> List[CardLine]:
    """Setup in steps, each with its state and buttons: this Mac (Install, or Update configs when the
    installed models changed), a VM or another computer (the server for the VM, the push), and by hand
    (the copy buttons); beside them what each step does."""
    vm_ready = v.host not in ("127.0.0.1", "::1", "localhost")

    def main(mw: int) -> List[CardLine]:
        L: List[CardLine] = [heading("This Mac", mw)]
        for dr in stale:
            L += cwrap(f"{YEL}⚠ Out of date: {dr.line(installed)}.{R}", mw)
        if here and not stale:
            L += cwrap(f"{GRN}✓ set up{R} for this server (" + ", ".join(f"{c}: provider {p}" for c, p in here) + ")", mw)
        elif not here:
            L.append(f"{DIM}not set up for this server yet{R}")
        if ui.install_ask:
            what = ("installs OpenCode and Pi if they are missing (a download from npm). Then it writes their configs"
                    if ui.install_ask == "all" else "writes the OpenCode and Pi configs for this server")
            L += cwrap(f"{YEL}Run ./carl.sh install{' --config-only' if ui.install_ask == 'config' else ''} now? It "
                       f"{what}.{R}", mw)
            L.append(buttons("  ", [("Yes, run it (y)", "insyes"), ("Cancel (n)", "insno")]))
        elif ui.install and not ui.install.done:
            L.append(buttons("  ", [("Show the installer (i)", "insshow"), ("Stop it", "inscancel")]))
        else:
            L.append(buttons("  ", [("Install on this Mac (i)", "insall"), ("Update configs only (u)", "insconfig")]))
        L += ["", heading("A VM or another computer", mw)]
        L += cwrap(f"server  {CYN}./carl.sh --vm{R} for a VMware VM "
                   + (f"{GRN}(it is: {v.host}){R}" if vm_ready else f"{YEL}(now: this Mac only){R}"), mw)
        L += cwrap(f"there   {CYN}./install-clients.sh && ./install.sh{R} in the copied client folder", mw)
        L += cwrap("sync    " + (f"{GRN}{v.listeners} connected{R}" if v.api else f"{YEL}the dashboard's API is not running{R}")
                   + f"{DIM} · the Clients tab lists them{R}", mw)
        L.append(buttons("  ", [("Push config to clients (P)", "inspush")]))
        L += ["", heading("By hand", mw)]
        L.append(buttons("  ", [("OpenCode config (o)", "opencode"), ("Pi config (p)", "pi"), ("curl test (t)", "curl")]))
        return L

    tip = ("Press u to list the installed models in the OpenCode and Pi configs." if stale
           else "Press i to set up OpenCode and Pi on this Mac." if not here
           else "After a new model or a change to the context or slots, press u to run the installer again.")
    return with_side(main, tip, [
        ("On this Mac", [f"One command does it: {CYN}./carl.sh install{R}. It installs OpenCode and Pi if they are "
                         f"missing (into ~/.local, no sudo). Then it connects them to this server. It keeps your own "
                         f"providers, default model and agents, and it writes a backup first."]),
        ("In a VM", ["Start the server for the VM with --vm. Copy the client folder into the VM. Then run the "
                     "installer there. The folder contains the address and the key of the server (remote.json and "
                     "api-key). CARL writes them at each server start, so the installer needs no arguments."]),
        ("Other computers", ["On a different computer, the installer adds a sync service. The service keeps one "
                             "connection to this dashboard and opens no port on the client. It applies the config "
                             "that you push, and it makes backups."]),
        ("By hand", ["Each button copies one provider block (id carl) to the clipboard. The block adds a provider and "
                     "does not replace your providers."])],
        tw, main_w=90)


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
    w = min(76, cols - 4)                   # room for the three buttons on one line
    lines: List[CardLine] = [""]
    if alive:
        lines += [f"The server (pid {pid}) still runs.", "",
                  *button_rows("  ", [("Stop server (s)", "stop"), ("Leave it running (d)", "detach"),
                                      ("Cancel (Esc)", "cancel")], w - 4),
                  "", *cwrap(f"{DIM}Leave it running: the server continues. To attach again, run ./carl.sh monitor.{R}",
                             w - 4)]
    else:
        lines += ["No server runs.", "", buttons("  ", [("Quit (q)", "detach"), ("Cancel (Esc)", "cancel")])]
    card = draw_card("quitbox", "QUIT", "", lines, w, 1)
    pad = (cols - w) // 2
    top = max((height - len(card)) // 2, 0)
    rows: List[Row] = [("", [])] * top
    return (rows + indent(card, pad))[:height]


def drain_dialog(dr: Drain, cols: int, height: int) -> List[Row]:
    """A client's turn runs while a step would stop the model: wait for the end of the turn, now, or cancel."""
    w = min(80, cols - 4)
    def slots(ids: List[int]) -> str:
        return ("slot " if len(ids) == 1 else "slots ") + ", ".join(str(i) for i in ids)
    state = (f"{slots(dr.busy)}: a reply is in progress" if dr.busy
             else f"{slots(dr.turns)}: between two requests (a tool runs)" if dr.turns
             else "CARL saves the session" if dr.saving else "the turn ends")
    tw = w - 4
    lines: List[CardLine] = [""]
    if dr.waiting:
        lines += [*cwrap(f"CARL waits for the end of the turn: {state} ({_ago(dr.since, time.time())}).", tw),
                  *cwrap(f"Then CARL saves the session. After that, it will {dr.what}.", tw), "",
                  *button_rows("  ", [("Now (y)", "drain:now"), ("Cancel (Esc)", "drain:cancel")], tw), "",
                  *cwrap(f"{DIM}A client without the CARL plugin does not mark its turns. For such a client, CARL waits "
                         f"for an idle slot.{R}", tw)]
    else:
        lines += [*cwrap(f"The turn of an agent runs now: {state}.", tw), *cwrap(f"To {dr.what}, CARL stops the model.", tw), "",
                  *button_rows("  ", [("Wait for the turn to end (w)", "drain:wait"), ("Now (y)", "drain:now"),
                                      ("Cancel (Esc)", "drain:cancel")], tw), "",
                  *cwrap(f"{DIM}Wait: CARL continues after the turn ends and after it saves the session.{R}", tw),
                  *cwrap(f"{DIM}Now: the reply stops. The client shows an error (Pi tries again). The session goes "
                         f"back to its last save.{R}", tw)]
    card = draw_card("drainbox", "A TURN IS RUNNING", "", lines, w, 1)
    pad = (cols - w) // 2
    top = max((height - len(card)) // 2, 0)
    rows: List[Row] = [("", [])] * top
    return (rows + indent(card, pad))[:height]
