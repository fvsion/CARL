"""The bodies of the Live, Connect, Requests and Log tabs, the ? card, the quit dialog and the
"a turn runs" dialog. Pure: they draw from a View, the UI state and the snapshot (they clamp scroll
positions and set the keys of the screen they draw: ui.keys for the footer and the ? card)."""
from __future__ import annotations

import time
from typing import Callable, List, Optional, Sequence, Tuple

from carl_core.domain.units import duration, speed, tokens

from .cards import (REQ_HEAD, STOPPED, View, card_requests, card_stopped, column, live_sentence, log_view,
                    req_row, status_of, wrapped)
from .fmt import (B, CYN, DIM, GRN, R, RED, YEL, CardLine, Key, Ln, Row, Section, button_rows, buttons, cwrap, draw_card,
                  fit, heading, home_short, indent, lv, side_by_side, with_side)
from .model import ServerData, clean
from .clients import Drift
from .state import CONNECT_SUBPANELS, Drain, InstallRun, UIState

EVERY_SCREEN: List[Key] = [("1-5 Tab", "the tabs"), ("D", "detail: simple or full (saved)"), ("?", "this list"),
                           ("q", "quit (CARL asks first)")]
TWO_COLUMNS = 120           # the Live tab puts its cards in two columns from this width
THREE_COLUMNS = 180         # ... and in three from this one


def keys_card(keys: Sequence[Key], more: Sequence[str], messages: Sequence[Tuple[float, str]], w: int) -> List[Row]:
    """The ? card: every key of the screen shown, what else it does, the keys of every screen, and the
    last messages."""
    def table(ks: Sequence[Key]) -> List[CardLine]:
        kw = max((len(k) for k, _ in ks), default=0) + 2
        return [f"  {B}{k:<{kw}}{R}{what}" for k, what in ks]
    L: List[CardLine] = [heading("This screen", w - 4), *table(keys)]
    L += [x for m in more for x in cwrap(f"{DIM}{m}{R}", w - 4)]
    L += ["", heading("Every screen", w - 4), *table(EVERY_SCREEN)]
    if messages:
        L += ["", heading("Recent messages", w - 4)]
        L += [x for t, m in messages[-5:] for x in cwrap(f"{DIM}{time.strftime('%H:%M', time.localtime(t))}{R} {m}",
                                                          w - 4, "      ")]
    return draw_card("keys", "KEYS", f"{DIM}? closes this list{R}", L, w, 1)


PREVIEW_TITLES = {"opencode": "OPENCODE CONFIG", "pi": "PI CONFIG", "curl": "CURL TEST"}
PREVIEW_WHERE = {
    "opencode": "→ Add it under \"provider\" in ~/.config/opencode/opencode.json. Do not remove your other providers. "
                "Restart OpenCode. Then use /models.",
    "pi": "→ Add it under \"providers\" in ~/.pi/agent/models.json. Do not remove your other providers. Then use /model "
          "in Pi.",
    "curl": "→ Run it on a computer that can connect to the server. The server answers \"hello\"."}


def body_live(v: View, ui: UIState, d: ServerData, cols: int, height: int) -> List[Row]:
    """The state in one sentence, then the cards: SLOTS and MEMORY beside SPEED, CONNECT and HEALTH (one
    column when narrow), RECENT REQUESTS; in full detail also MODEL and the end of the log. No server:
    the SERVER card with the next action."""
    stopped = status_of(d, v.server_pid)[0] == STOPPED
    rows: List[Row] = [] if stopped else [(" " + x, list()) for x in cwrap(live_sentence(d, v.port), cols - 2)]
    rows.append(("", []))
    if stopped:
        card = card_stopped(v, d)
        rows += indent(draw_card("server", card.title, card.summary, wrapped(card.lines, cols - 5), cols - 1, 1))
        ui.keys = [("a", "start the server"), ("5", "settings"), ("4", "log")]
        ui.keys_more = ["a opens Settings > Server and asks before it starts the server."]
    else:
        right = ["speed", "connect", "health"] + (["model"] if v.full else [])
        if cols >= THREE_COLUMNS:
            cw = (cols - 4) // 3
            rows += side_by_side(column(v, ["slots", "memory"], d, cw),
                                 side_by_side(column(v, ["speed", "health"], d, cw),
                                              column(v, ["connect"] + (["model"] if v.full else []), d, cols - 4 - 2 * cw),
                                              cw, pad_left=False), cw, pad_left=False)
        elif cols >= TWO_COLUMNS:
            cw = (cols - 3) // 2
            rows += side_by_side(column(v, ["slots", "memory"], d, cw), column(v, right, d, cols - 3 - cw), cw,
                                 pad_left=False)
        else:
            rows += indent(column(v, ["slots", *right[:1], "memory", *right[1:]], d, cols - 1))
        req = card_requests(v, d, 8 if v.full else 3)
        rows += indent(draw_card("requests", req.title, req.summary, req.lines, cols - 1, v.level("requests")))
        ui.keys = [("k", "show key"), ("o p c", "copy OpenCode / Pi / curl config"), ("click a title:", "more / less"),
                   ("↑↓", "scroll")] + ([("+ -", "log lines")] if v.full else [])
        ui.keys_more = ["Click a card title to collapse or open the card. D shows more figures in every card.",
                        "space reads the server again now. The mouse wheel scrolls."]
    if v.full:
        name = (v.log_path or "").rsplit("/", 1)[-1]
        lt = f"{DIM}{name} · tab 4 for the full log{R}" if name else f"{DIM}no log file{R}"
        rows += indent(draw_card("log", "LOG", lt, log_view(v, ui.lines, cols - 5), cols - 1, v.level("log")))
    ui.scroll = min(ui.scroll, max(len(rows) - height, 0))
    return rows[ui.scroll:ui.scroll + height]


# ---------------------------------------------------------------- Connect
def body_connect(v: View, ui: UIState, d: ServerData, cols: int, height: int,
                 here: List[Tuple[str, str]], preview: str, stale: Sequence[Drift] = (), installed: int = 0) -> List[Row]:
    """Setup: the steps (this Mac, a VM or another computer, by hand) with their buttons, then the
    installer's output or the selected config, then the explanations (beside the steps when wide).
    Clients: the computers that sync."""
    w = cols - 1
    tw = w - 4
    top: List[Row] = [connect_bar(ui.connect_sp),
                      (f" {DIM}Connect OpenCode and Pi to this server: on this Mac, in a VM or on another computer.{R}",
                       []), ("", [])]
    height -= len(top)
    if ui.connect_sp == 1:
        ui.keys = [("P", "send the config"), ("[ ]", "Setup / Clients")]
        ui.keys_more = ["Forget (a button) removes the computers that CARL did not see for a week. A computer comes back "
                        "when it connects again."]
        n = len(v.clients)
        summ = (f"{n} {'computer syncs' if n == 1 else 'computers sync'} their config with this server" if n
                else "no other computer syncs yet")
        return (top + indent(draw_card("clients", "CLIENTS", f"{DIM}{summ}{R}",
                                       clients_lines(v, here, stale, tw), w, 1)))[:height + len(top)]
    ui.keys = [("i", "install on this Mac"), ("u", "update the model lists"), ("o p c", "copy a config"), ("k", "show key"),
               ("P", "send the config"), ("↑↓", "scroll the config"), ("[ ]", "Setup / Clients")]
    if ui.install_ask:
        ui.keys = [("y", "run the installer"), ("n Esc", "cancel")]
    ui.keys_more = ["x closes the installer's output. Click a button, or press its key."]
    steps, about = setup_parts(v, ui, here, tw, stale, installed)
    beside = tw >= 150
    if beside:
        rows = draw_card("guide", "SET UP OPENCODE AND PI", "", with_side(steps, tip_of(here, stale), about, tw,
                                                                            main_w=92), w, 1)
    else:
        rows = draw_card("guide", "SET UP OPENCODE AND PI", "",
                         [*steps(tw), "", *cwrap(f"{CYN}Quick tip:{R} {tip_of(here, stale)}", tw)], w, 1)
    room = max(height - len(rows) - 3, 3)
    if ui.install and ui.install_shown:
        rows += draw_card("install", "INSTALLER", install_summary(ui.install), install_lines(ui, room, tw), w, 1)
    else:
        kind = ui.preview
        note = (f"{GRN}copied to the clipboard ✓{R}" if ui.copied == kind else
                f"{DIM}its button (or o, p, c) copies it{R}")
        plines = preview.splitlines()
        where = cwrap(f"{DIM}{PREVIEW_WHERE[kind]}{R}", tw)
        shown = max(room - len(where) - 1, 1)
        ui.prev_scroll = min(ui.prev_scroll, max(len(plines) - shown, 0))
        body: List[CardLine] = [*where, *plines[ui.prev_scroll:ui.prev_scroll + shown]]
        if len(plines) > shown:
            body.append(f"{DIM}lines {ui.prev_scroll + 1}-{min(ui.prev_scroll + shown, len(plines))} of {len(plines)} · "
                        f"↑↓ PgUp PgDn scroll · the copy has all of it{R}")
        rows += draw_card("preview", PREVIEW_TITLES[kind], note, body, w, 1)
    if not beside:
        rows += draw_card("guide_about", "HOW IT WORKS", "", [x for t, b in about for x in
                                                              ([heading(t, tw)] + [y for s in b for y in cwrap(str(s), tw)])],
                          w, 1)
    return (top + indent(rows))[:height + len(top)]


def connect_bar(sp: int) -> Row:
    """The Setup / Clients switch: the selected one in brackets and reverse video."""
    text, spans, col = "", [], 0
    for i, name in enumerate(CONNECT_SUBPANELS):
        lab = f"[{name}]" if i == sp else f" {name} "
        text += (f"\x1b[1;7m{lab}{R}" if i == sp else f"{DIM}{lab}{R}") + "  "
        spans.append((1 + col, 1 + col + len(lab), f"csp:{i}"))
        col += len(lab) + 2
    return " " + text + f"{DIM}   [ ] change{R}", spans


def ago(t: float, now: float) -> str:
    """How long ago, in the units' style: '42 s ago', '3 days ago'."""
    s = max(0.0, now - t)
    return f"{int(s // 86400)} days ago" if s >= 172800 else f"{duration(s)} ago"


def forget_count(v: View, now: float, after: float = 7 * 86400) -> int:
    return sum(1 for c in v.clients if c.connected <= 0 and now - c.last_seen > after)


def clients_lines(v: View, here: List[Tuple[str, str]], stale: Sequence[Drift], tw: int) -> List[CardLine]:
    """Every computer: this Mac's configs, then each one that syncs (connected or last seen, its config
    against the one sent), Send and Forget; beside them what the list is and how to add a computer."""
    now = time.time()
    version, _, when = v.pushed.partition(" at ")

    def main(mw: int) -> List[CardLine]:
        sent = (f"{when}" + (f" {DIM}(version {version}){R}" if v.detail == "full" else "")) if v.pushed else \
            f"{DIM}nothing sent yet{R}"
        L: List[CardLine] = [lv("Last config sent", sent, 18),
                             lv("Sync address", f"{GRN}{v.api}{R} {DIM}(the dashboard API){R}" if v.api
                                else f"{YEL}not running: other computers cannot sync now{R}", 18), ""]
        L.append(f"  {DIM}{'computer':<16} {'user':<8} {'syncs':<14} {'last seen':<16} config{R}")
        mac = (f"{GRN}up to date{R}" if here and not stale else f"{YEL}out of date: Setup, u{R}" if here
               else f"{DIM}not set up for this server{R}")
        L.append(f"  {'this Mac':<16} {'':<8} {'OpenCode, Pi':<14} {'now':<16} {mac}")
        for c in v.clients:
            seen = f"{GRN}connected{R}" if c.connected > 0 else f"{DIM}{ago(c.last_seen, now)}{R}"
            how = "all the time" if c.mode == "service" else "at their start"
            if not version:
                conf = f"{DIM}nothing sent{R}"
            elif c.applied == version:
                conf = f"{GRN}up to date{R}"
            elif not c.auto_apply:
                conf = f"{YEL}waits for you to apply it{R}"
            else:
                conf = f"{YEL}applies it at the next sync{R}"
            name = (c.host or c.id)[:16]
            L.append(f"  {B}{name:<16}{R} {c.user[:8]:<8} {how:<14} " + fit(seen, 16) + f" {conf}")
            if v.detail == "full":
                L.append(f"  {DIM}{'':<16} {c.os} · {c.address} · has version {c.applied or 'none'}{R}")
        if not v.clients:
            L.append(f"  {DIM}No other computer yet.{R}")
        old = forget_count(v, now)
        forget = (f"Forget {old} computer{'' if old == 1 else 's'} not seen for a week" if old
                  else "Forget the computers not seen for a week")
        return [*L, "", *button_rows("  ", [("Send the config to them (P)", "inspush"), (forget, "clforget")], mw)]

    tip = ("Press P to send the installed models to every computer that syncs." if v.clients
           else "To add a computer: copy the client folder to it, then run its installer there.")
    return with_side(main, tip, [
        ("Who is in the list", ["OpenCode and Pi on this Mac, and each computer whose installer set up the sync. Such a "
                                "computer has a copy of the client folder. It gets the config that you send."]),
        ("Add a computer", [f"Copy the client folder to the computer. Run {CYN}./install-clients.sh{R} there, then "
                            f"{CYN}./install.sh{R}. The folder has the address and the key of the server."])],
        tw, main_w=100)


def tip_of(here: List[Tuple[str, str]], stale: Sequence[Drift]) -> str:
    return ("Press u to put the installed models in the OpenCode and Pi configs." if stale
            else "Press i to set up OpenCode and Pi on this Mac." if not here
            else "After you add a model or change the context or the slots, press u.")


def setup_parts(v: View, ui: UIState, here: List[Tuple[str, str]], tw: int, stale: Sequence[Drift] = (),
                installed: int = 0) -> Tuple[Callable[[int], List[CardLine]], List[Section]]:
    """Setup in steps, each with its state and buttons (this Mac; a VM or another computer; by hand), and
    the explanations."""
    vm_ready = v.host not in ("127.0.0.1", "::1", "localhost")

    def main(mw: int) -> List[CardLine]:
        L: List[CardLine] = [heading("This Mac", mw)]
        for dr in stale:
            L += cwrap(f"{YEL}⚠ {dr.line(installed)}{R}", mw)
        if here and not stale:
            L += cwrap(f"{GRN}✓{R} OpenCode and Pi on this Mac use this server."
                       + (f" {DIM}(" + ", ".join(f"{c}: provider {p}" for c, p in here) + f"){R}"
                          if v.detail == "full" else ""), mw)
        elif not here:
            L.append(f"{DIM}OpenCode and Pi on this Mac are not set up for this server.{R}")
        if ui.install_ask:
            what = ("installs OpenCode and Pi if they are not installed (a download from npm). Then it writes their "
                    "configs" if ui.install_ask == "all" else "writes the OpenCode and Pi configs for this server")
            L += cwrap(f"{YEL}Run ./carl.sh install{' --config-only' if ui.install_ask == 'config' else ''} now? It "
                       f"{what}.{R}", mw)
            L.append(buttons("  ", [("Run it (y)", "insyes"), ("Cancel (n)", "insno")]))
        elif ui.install and not ui.install.done:
            L.append(buttons("  ", [("Show the installer (i)", "insshow"), ("Stop it", "inscancel")]))
        else:
            first = "Install again (i)" if here else "Install on this Mac (i)"
            L.append(buttons("  ", [(first, "insall"), ("Update the model lists (u)", "insconfig")]))
        L += ["", heading("A VM or another computer", mw)]
        L += cwrap(f"1. Start the server for the VM: {CYN}./carl.sh --vm{R} "
                   + (f"{GRN}(it runs so: {v.host}){R}" if vm_ready else f"{YEL}(now: this Mac only){R}"), mw, "   ")
        L += cwrap(f"2. Copy the client folder to it. Run {CYN}./install-clients.sh{R}, then {CYN}./install.sh{R} "
                   f"there.", mw, "   ")
        L += cwrap("3. Other computers sync their config: " + (f"{GRN}{v.listeners} connected now{R}" if v.api else
                                                                f"{YEL}the dashboard API is not running{R}")
                   + f"{DIM} (the Clients tab lists them){R}", mw, "   ")
        L.append(buttons("  ", [("Send the config (P)", "inspush")]))
        L += ["", heading("By hand", mw)]
        L.append(Ln(f"{DIM}Address{R} {v.base}/v1   {DIM}Key{R} "
                    + (v.key if v.key_shown else ("•" * 8 + v.key[-4:] if v.key else f"{RED}no key file{R}"))
                    + f"   {DIM}{'hide' if v.key_shown else 'show'} (k){R}", "key"))
        L.append(buttons("  ", [("OpenCode config (o)", "opencode"), ("Pi config (p)", "pi"), ("curl test (c)", "curl")]))
        return L

    about: List[Section] = [
        ("On this Mac", [f"One command does it: {CYN}./carl.sh install{R}. It installs OpenCode and Pi if they are not "
                         f"installed (into ~/.local, no sudo). Then it connects them to this server. It keeps your "
                         f"providers, your default model and your agents. It writes a backup first."]),
        ("In a VM", ["Start the server for the VM with --vm. Copy the client folder into the VM. Then run the "
                     "installer there. The folder has the address and the key of the server (remote.json and "
                     "api-key). CARL writes them at each server start, so the installer needs no arguments."]),
        ("Other computers", ["On a different computer, the installer adds a sync service. The service keeps one "
                             "connection to this dashboard and opens no port on the computer. It applies the config "
                             "that you send, and it makes backups."]),
        ("By hand", ["Each button copies one provider block (named carl) to the clipboard. The block adds a provider. "
                     "It does not replace your providers."])]
    return main, about



def install_summary(ins: InstallRun) -> str:
    """The installer card's title info: what runs and how it ended."""
    if not ins.done:
        return f"{YEL}runs: {ins.what}…{R}"
    code = ins.proc.returncode
    return f"{GRN}done: {ins.what}{R}" if code == 0 else f"{RED}stopped with an error (exit code {code}){R}"


def install_lines(ui: UIState, room: int, tw: int) -> List[CardLine]:
    """The installer's last output lines (control characters removed), and its buttons."""
    ins = ui.install
    if ins is None:
        return []
    out: List[CardLine] = []
    if ins.done and ins.proc.returncode != 0:
        out.append(f"{RED}The installer stopped with an error (exit code {ins.proc.returncode}). Its last lines:{R}")
    body = [f"{DIM}{fit(clean(x), tw)}{R}" for x in ins.lines if x.strip()]
    out += body[-max(room - 2 - len(out), 1):]
    if not ins.lines:
        out.append(f"{DIM}The installer starts…{R}")
    out.append(buttons("", [("Stop it", "inscancel")] if not ins.done else
                       [("Close (x)", "insclose"), ("Run it again (i)", "insall")]))
    return out


# ---------------------------------------------------------------- Requests and Log
def body_requests(v: View, ui: UIState, cols: int, height: int) -> List[Row]:
    """Every finished request in the log, newest first, with the means of their speeds."""
    reqs = list(reversed(v.log.requests))
    done = [r for r in reqs if r.tg]
    since = v.log.wall(reqs[-1].t0)[:5] if reqs else ""
    head = f"{len(reqs)} since {since}" if reqs else "none yet"
    if done:
        head += (f" · mean read {speed(sum(r.pp or 0 for r in done) / len(done))} · "
                 f"mean write {speed(sum(r.tg or 0 for r in done) / len(done))}")
    head += f" · {len(v.log.current)} running" if v.log.current else ""
    room = max(height - 6, 3)
    ui.req_scroll = min(ui.req_scroll, max(len(reqs) - room, 0))
    lines: List[CardLine] = [*cwrap(f"{DIM}Each row is one call from a client (a turn makes many). The first request of "
                                    f"a session reads the whole prompt. The next ones reuse most of it, so they read "
                                    f"few new tokens.{R}", cols - 5),
                             f"{DIM}{REQ_HEAD}  {'reused':>8}{R}"]
    for r in reqs[ui.req_scroll:ui.req_scroll + room]:
        lines.append(req_row(r, v.log.wall) + f"  {tokens(max(r.ctx - r.new - r.gen, 0)):>8}")
    if not reqs:
        lines.append(f"{DIM}No finished request yet.{R}")
    ui.keys = [("↑↓ PgUp PgDn", "scroll")]
    ui.keys_more = ["The means are of the requests in this list. The SPEED card on the Live tab counts every request "
                    "since the server started."]
    return indent(draw_card("requests_tab", "REQUESTS", head, lines, cols - 1, 1))


def body_log(v: View, ui: UIState, cols: int, n: int) -> List[Row]:
    """The log, n lines, with its wrap / only-errors / back-to-the-end switches."""
    switches = buttons("", [(f"Wrap lines: {'on' if ui.wrap else 'off'} (w)", "wrap"),
                            (f"Only errors: {'on' if ui.errors_only else 'off'} (f)", "errors"),
                            (("Back to the end (End)" if ui.log_scroll else "At the end"), "follow")])
    path = v.log_path or ""
    name = home_short(path, v.home) if v.detail == "full" else path.rsplit("/", 1)[-1]
    summ = f"{DIM}{name or 'no log file'}" + (f" · {ui.log_scroll} lines back" if ui.log_scroll else "") + R
    note = f"{DIM}The server's own log (llama.cpp). Dim lines are normal at a start.{R}"
    lines: List[CardLine] = [switches, note, *log_view(v, n - 1, cols - 5)]
    ui.keys = [("w", "wrap lines"), ("f", "only errors"), ("↑↓ PgUp PgDn", "scroll"), ("End", "back to the end")]
    ui.keys_more = ["The times are this Mac's clock."]
    return indent(draw_card("logtab", "LOG", summ, lines, cols - 1, 1))


# ---------------------------------------------------------------- dialogs
def centred(card: List[Row], cols: int, w: int, height: int) -> List[Row]:
    pad = (cols - w) // 2
    top = max((height - len(card)) // 2, 0)
    rows: List[Row] = [("", [])] * top
    return (rows + indent(card, pad))[:height]


def quit_dialog(d: ServerData, ui: UIState, cols: int, height: int) -> List[Row]:
    """Quit: stop the server, leave it running, or cancel."""
    pid = d.target_pid
    alive = pid and not d.exited and not ui.stopping
    w = min(84, cols - 4)
    lines: List[CardLine] = [""]
    if alive:
        what = d.alias or "The server"
        lines += [*cwrap(f"{what} runs{f' (pid {pid})' if ui.full else ''}. Stop it, or leave it running for OpenCode "
                         f"and Pi?", w - 4), "",
                  *button_rows("  ", [("Stop the server and quit (s)", "stop"),
                                      ("Leave it running and quit (l)", "detach"), ("Cancel (Esc)", "cancel")], w - 4),
                  "", *cwrap(f"{DIM}If you leave it running, run ./carl.sh to open the dashboard again.{R}", w - 4)]
        ui.keys = [("s", "stop the server and quit"), ("l", "leave it running and quit"), ("Esc", "cancel")]
    else:
        lines += ["No server runs.", "", buttons("  ", [("Quit (q)", "detach"), ("Cancel (Esc)", "cancel")])]
        ui.keys = [("q", "quit"), ("Esc", "cancel")]
    return centred(draw_card("quitbox", "QUIT", "", lines, w, 1), cols, w, height)


def drain_dialog(dr: Drain, cols: int, height: int, ui: Optional[UIState] = None) -> List[Row]:
    """A client's turn runs while a step would stop the model: wait for the turn, stop now, or cancel."""
    w = min(84, cols - 4)
    tw = w - 4

    def slots(ids: List[int]) -> str:
        return ("slot " if len(ids) == 1 else "slots ") + ", ".join(str(i) for i in ids)
    lines: List[CardLine] = [""]
    if dr.busy:
        state = f"An agent is working: {slots(dr.busy)} writes an answer."
        now_text = "Stop now: the answer stops. The client shows an error (Pi tries again). The session goes back to its last save."
    elif dr.turns:
        state = f"An agent is working: its turn runs in {slots(dr.turns)} (a tool runs now)."
        now_text = "Stop now: the turn stops. The session goes back to its last save."
    else:
        state = "CARL saves the session." if dr.saving else "The turn ends."
        now_text = "Stop now: CARL does not wait for the save."
    if dr.waiting:
        lines += [*cwrap(f"CARL waits for the turn to end ({duration(time.time() - dr.since)} so far). Then it saves the "
                         f"session and continues: {dr.what}.", tw),
                  *cwrap(state, tw), "",
                  *button_rows("  ", [("Stop now (s)", "drain:now"), ("Cancel (Esc)", "drain:cancel")], tw), "",
                  *cwrap(f"{DIM}A client without the CARL plugin does not mark its turns. For such a client, CARL waits "
                         f"until the slots are free.{R}", tw)]
        keys: List[Key] = [("s", "stop now"), ("Esc", "cancel")]
    else:
        lines += [*cwrap(state, tw), *cwrap(f"Next step: {dr.what}. This stops the model.", tw), "",
                  *button_rows("  ", [("Wait for the turn (w)", "drain:wait"), ("Stop now (s)", "drain:now"),
                                      ("Cancel (Esc)", "drain:cancel")], tw), "",
                  *cwrap(f"{DIM}Wait: CARL continues after the turn ends and the session is saved.{R}", tw),
                  *cwrap(f"{DIM}{now_text}{R}", tw)]
        keys = [("w", "wait for the turn"), ("s", "stop now"), ("Esc", "cancel")]
    if ui is not None:
        ui.keys = keys
    return centred(draw_card("drainbox", "AN AGENT IS WORKING", "", lines, w, 1), cols, w, height)
