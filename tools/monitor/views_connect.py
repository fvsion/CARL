"""The Connect tab: Setup (the steps, the config preview, the installer, the client package, how it works) and
Clients (the other computers that sync). Pure, like views.py (split from it in Phase 23.2).

Phase 23.2 layout: each part is a section with its own level (Tab selects it, L changes it). Setup at 100 and 130
columns: one column (steps, preview, HOW IT WORKS), the page scrolls. From 150: the steps and HOW IT WORKS side by
side, the preview (or the installer, or the package) under both at full width. From 190: three columns. Clients: the
table, HOW IT WORKS under it, or to its right from 150."""
from __future__ import annotations

import re
import time
from typing import Dict, List, Optional, Sequence, Tuple

from carl_core.domain.units import duration, file_size

from .cards import View, sections, wrapped
from .clients import Drift, single_mode
from .fmt import (reveal, ANSI, B, CYN, DIM, GRN, LW, NA, R, RED, ROW_MARK, ROW_SEP, YEL, CardLine, Ln, Row, aligned,
                  button_rows, buttons, cwrap, draw_card, fit, heading, home_short, indent, row, side_by_side, vlen, wrap)
from .model import ServerData, clean
from .state import CONNECT_SUBPANELS, InstallRun, UIState

SETUP_SECTIONS = ["csetup", "cpreview", "cinstall", "cpackage", "chow"]
CLIENT_SECTIONS = ["clients", "clhow"]
sections(three=["csetup", "clients"], two=["cpreview", "cinstall", "cpackage", "chow", "clhow"])

PREVIEW_TITLES = {"opencode": "OPENCODE CONFIG", "pi": "PI CONFIG", "curl": "CURL TEST"}
PREVIEW_KEY = {"opencode": "o", "pi": "p", "curl": "c"}
PREVIEW_WHERE = {
    "opencode": "Add it under \"provider\" in ~/.config/opencode/opencode.json, next to your other providers. Restart "
                "OpenCode, then use /models.",
    "pi": "Add it under \"providers\" in ~/.pi/agent/models.json, next to your other providers. Then use /model in Pi.",
    "curl": "Run it on a computer that can connect to the server. The server answers \"hello\"."}
SIDE_AT = 150           # from this width: the steps and HOW IT WORKS side by side (Setup), the help beside the table
THREE_AT = 190          # from this width: steps, preview and HOW IT WORKS in three columns (Setup)
PREVIEW_LINES = 8       # the preview's lines in one column (100 and 130 columns)
MIN_LINES = 10          # ... and at least this many under the steps (160)
MAX_LINES = 50          # (a -tall dump draws 300 rows: the preview stays a window)
HOW_W = 66              # HOW IT WORKS beside the steps (160)
HELP_W = 50             # the Clients help beside the table


def section(ui: UIState, name: str, title: str, summary: str, lines: Sequence[CardLine], w: int, marks: int,
            wrap: bool = True) -> List[Row]:
    """One section at its own level (its title shows it; Tab selects it, L changes it). Label rows get the card's
    label column; wrap=False: the other lines are drawn as they are (they fit already)."""
    body = [Ln(x.text.rstrip(" "), x.act, x.spans) if isinstance(x, Ln) else x     # buttons end with two spaces
            for x in aligned(list(lines), w - 4)]
    return draw_card(name, title, summary, wrapped(body, w - 4) if wrap else body, w, ui.levels.get(name, 1), marks,
                     ui.section == name)


def full(ui: UIState, name: str) -> bool:
    return ui.levels.get(name, 1) == 2


def unpad(rows: List[Row]) -> List[Row]:
    """side_by_side's rows without the space it puts before the left column."""
    return [(t[1:], [(a - 1, b - 1, act) for a, b, act in sp]) for t, sp in rows]


# ---------------------------------------------------------------- Connect
def body_connect(v: View, ui: UIState, d: ServerData, cols: int, height: int,
                 here: List[Tuple[str, str]], preview: str, stale: Sequence[Drift] = (), installed: int = 0) -> List[Row]:
    """The panel bar, then Setup or Clients as one page that scrolls (↑↓, PgUp PgDn, the wheel); the config preview
    scrolls on its own while it is the selected section."""
    w = cols - 1
    top: List[Row] = [connect_bar(ui.connect_sp), ("", [])]
    room = max(height - len(top), 4)
    focus: Optional[Tuple[int, int]] = None
    if ui.connect_sp == 1:
        page = clients_page(v, ui, here, stale, w)
    else:
        page, focus = setup_page(v, ui, d, here, preview, stale, w, room)
    if focus is not None:                       # the preview has the focus: ↑↓ scroll it; the page shows it whole
        off = max(0, min(focus[0], focus[1] - room))
    else:
        reveal(ui, page, "conn_scroll", room)
        ui.conn_scroll = off = max(0, min(ui.conn_scroll, len(page) - room))
    ui.more = len(page) > off + room
    return top + indent(page[off:off + room])


def connect_bar(sp: int) -> Row:
    """The Setup / Clients switch: the selected one in brackets and reverse video (as the Settings panel bar)."""
    text, spans, col = "", [], 0
    for i, name in enumerate(CONNECT_SUBPANELS):
        lab = f"[{name}]" if i == sp else f" {name} "
        text += (f"\x1b[1;7m{lab}{R}" if i == sp else f"{DIM}{lab}{R}") + "  "
        spans.append((1 + col, 1 + col + len(lab), f"csp:{i}"))
        col += len(lab) + 2
    return " " + text + f"{DIM}   [ ] change panel{R}", spans


# ---------------------------------------------------------------- Setup
def setup_page(v: View, ui: UIState, d: ServerData, here: List[Tuple[str, str]], preview: str,
               stale: Sequence[Drift], w: int, room: int) -> Tuple[List[Row], Optional[Tuple[int, int]]]:
    """The Setup page and, when the preview has the focus, its first and end rows."""
    ui.sections = ["csetup", middle_name(ui), "chow"]
    setup_keys(ui)
    tip = tip_of(here, stale)
    if w >= THREE_AT:                                                    # three columns
        cw = (w - 2) // 3
        hw = w - 2 * cw - 2
        steps = setup_card(v, ui, here, stale, cw)
        how = section(ui, "chow", "HOW IT WORKS", "", how_lines(tip, hw - 4), hw, 2)
        mrows = middle(v, ui, d, preview, cw, max(room, len(steps), len(how)) - 6)
        page = unpad(side_by_side(steps, unpad(side_by_side(mrows, how, cw, pad_left=False)), cw, pad_left=False))
        return page, ((0, len(mrows)) if focused(ui) else None)
    if w >= SIDE_AT:                                                     # steps | HOW IT WORKS, the preview under both
        lw = w - HOW_W - 1
        steps = setup_card(v, ui, here, stale, lw)
        how = section(ui, "chow", "HOW IT WORKS", "", how_lines(tip, HOW_W - 4), HOW_W, 2)
        upper = unpad(side_by_side(steps, how, lw, pad_left=False))
        mrows = middle(v, ui, d, preview, w, max(room - len(upper) - 4, MIN_LINES))
        return upper + mrows, ((len(upper), len(upper) + len(mrows)) if focused(ui) else None)
    steps = setup_card(v, ui, here, stale, w)                            # one column
    mrows = middle(v, ui, d, preview, w, PREVIEW_LINES)
    how = section(ui, "chow", "HOW IT WORKS", "", how_lines(tip, w - 4), w, 2)
    return steps + mrows + how, ((len(steps), len(steps) + len(mrows)) if focused(ui) else None)


def setup_card(v: View, ui: UIState, here: List[Tuple[str, str]], stale: Sequence[Drift], w: int) -> List[Row]:
    summary, lines = setup_lines(v, ui, here, stale, w - 4)
    return section(ui, "csetup", "SET UP OPENCODE AND PI", summary, balanced(lines, w - 4), w, 3)


def greedy(items: Sequence[str], w: int) -> List[str]:
    """Items joined with ", " on lines of at most w columns (an item is never split)."""
    out: List[str] = []
    for it in items:
        if out and len(out[-1]) + 2 + len(it) + 1 <= w:
            out[-1] += f", {it}"
        else:
            if out:
                out[-1] += ","
            out.append(it)
    return out


def balanced(lines: List[CardLine], tw: int) -> List[CardLine]:
    """Label rows whose value is a list too long for one line: the list on lines of even length, so that no
    line holds one lone item (aligned() would wrap it greedily)."""
    labels = [x[1:].split(ROW_SEP, 1)[0] for x in lines if isinstance(x, str) and x.startswith(ROW_MARK)]
    lw = min(max((len(x) for x in labels), default=0) + 2, LW + 2)
    out: List[CardLine] = []
    for x in lines:
        if not (isinstance(x, str) and x.startswith(ROW_MARK)):
            out.append(x)
            continue
        lab, val = x[1:].split(ROW_SEP, 1)
        items = val.split(", ")
        if vlen(val) <= tw - lw or len(items) < 2 or ANSI.search(val):
            out.append(x)
            continue
        n = len(greedy(items, tw - lw))
        width = tw - lw
        while width > 10 and len(greedy(items, width - 1)) == n:
            width -= 1
        parts = greedy(items, width)
        out += [row(lab, parts[0]), *[row("", p) for p in parts[1:]]]
    return out


def install_width(cols: int) -> int:
    """The text width of the INSTALLER card at a terminal width (for the installer's COLUMNS, so that its own
    wrapping matches the card)."""
    w = cols - 1
    return ((w - 2) // 3 if w >= THREE_AT else w) - 4


def middle_name(ui: UIState) -> str:
    """The section under (or beside) the steps: the installer's output, the client package, or the config preview."""
    if ui.install and ui.install_shown:
        return "cinstall"
    if ui.package and ui.package_shown:
        return "cpackage"
    return "cpreview"


def focused(ui: UIState) -> bool:
    """Does the config preview have the focus (selected, open)? Then ↑↓ scroll it, not the page."""
    return ui.section == "cpreview" and middle_name(ui) == "cpreview" and ui.levels.get("cpreview", 1) > 0


def middle(v: View, ui: UIState, d: ServerData, preview: str, w: int, n: int) -> List[Row]:
    """The installer, the client package or the config preview, w wide; n: the lines the preview (or the installer's
    output) gets."""
    mid = middle_name(ui)
    n = max(min(n, MAX_LINES), 3)
    if mid == "cinstall" and ui.install:
        return section(ui, "cinstall", "INSTALLER", install_summary(ui.install), install_lines(ui, n, w - 4), w, 2,
                       wrap=False)
    if mid == "cpackage":
        return section(ui, "cpackage", "CLIENT PACKAGE", package_summary(ui), package_lines(ui, v.home, w - 4), w, 2)
    kind = ui.preview
    return section(ui, "cpreview", PREVIEW_TITLES[kind], f"{GRN}✓ copied{R}" if ui.copied == kind else "",
                   preview_lines(ui, d, kind, preview, w - 4, n), w, 2, wrap=False)


def preview_lines(ui: UIState, d: ServerData, kind: str, preview: str, tw: int, n: int) -> List[CardLine]:
    """Where to add it, a window of n lines (long ones wrap under their indent), and a foot line: which lines, how to
    scroll, how to copy."""
    out: List[CardLine] = [f"{DIM}{x}{R}" for x in cwrap(PREVIEW_WHERE[kind], tw)] if d.up else []
    lines: List[str] = []
    for x in preview.splitlines():
        lead = " " * (len(x) - len(x.lstrip(" ")))
        lines += (cwrap(x, tw) if not d.up else code_wrap(x, tw, lead + "  ")) if vlen(x) > tw else [x]   # prose: cwrap
    first = 0
    if focused(ui):
        ui.prev_scroll = first = min(ui.prev_scroll, max(len(lines) - n, 0))
    out += lines[first:first + n]
    if not d.up:
        return out
    if len(lines) > n:
        parts = [f"Lines {first + 1}-{min(first + n, len(lines))} of {len(lines)}",
                 f"Press {PREVIEW_KEY[kind]} to copy all of it.",
                 "Press ↑↓ PgUp PgDn to scroll." if focused(ui) else "Press Tab, then ↑↓ to scroll."]
    else:
        parts = [f"{len(lines)} lines", f"Press {PREVIEW_KEY[kind]} to copy all of it."]
    foot: List[str] = []                        # the parts on as few lines as fit, "│" between them
    for p in parts:
        if foot and len(foot[-1]) + 5 + len(p) <= tw:
            foot[-1] += f"  │  {p}"
        else:
            foot.append(p)
    return out + [f"{DIM}{x}{R}" for x in foot]


def code_wrap(line: str, w: int, lead: str) -> List[str]:
    """A long line of a config or a command (plain text) on several lines: broken after a space or a comma, the
    next lines under lead; a piece wider than a line is cut where it must."""
    out: List[str] = []
    cur = ""
    for piece in re.split(r"(?<=[ ,])", line):
        while piece:
            room = w - len(cur)
            if len(piece) <= room:
                cur, piece = cur + piece, ""
            elif cur.strip():
                out.append(cur.rstrip())
                cur = lead
            else:
                cur, piece = cur + piece[:room], piece[room:]
                out.append(cur)
                cur = lead
    if cur.strip():
        out.append(cur.rstrip())
    return out


def setup_keys(ui: UIState) -> None:
    """The footer's keys and the ? card's notes for Setup (the installer's question: its keys only)."""
    if ui.install_ask:
        ui.keys = [("y", "run the installer"), ("n Esc", "cancel")]
    else:
        closes = (ui.install and ui.install_shown) or (ui.package and ui.package_shown)
        ui.keys = [("Tab", "section"), ("L", "level"), ("i", "install"), ("u", "update"), ("z", "package"),
                   ("P", "send"), ("o p c", "copy"),
                   *([("f", "Finder")] if package_path(ui) else []), *([("x", "close")] if closes else []),
                   ("↑↓", "scroll the config" if focused(ui) else "scroll"), ("k", "key"), ("[ ]", "Clients")]
    ui.keys_more = ["Press i to install OpenCode and Pi on this Mac. Press u to update their model lists. Press z to "
                    "make the client package. Press P to send the config to the computers that sync. Press o, p or c "
                    "to copy a config. Press k to show the key.",
                    "Press g to set the new-file gate (advanced, not recommended): OpenCode and Pi then stop the main "
                    "agent at its Nth new file in a turn and tell it to use the coder. The full level of SET UP shows "
                    "it.",
                    "To scroll the config, select it with Tab, then press ↑↓, PgUp or PgDn. Press x to close the "
                    "installer's output or the client package.",
                    "The client package holds the API key of the server. Keep it secret, and delete it after you copy "
                    "it."]


def tip_of(here: List[Tuple[str, str]], stale: Sequence[Drift]) -> str:
    single = any(dr.server for dr in stale)
    return (("Press u to put the model the server runs in the configs." if single
             else "Press u to put the installed models in the configs.") if stale
            else "Press i to set up OpenCode and Pi on this Mac." if not here
            else "Press u after you add a model or change the context or slots.")


UNSENT = "The last config sent is out of date: the models for the clients changed. Press P to send the new config."


def drift_rows(stale: Sequence[Drift]) -> List[CardLine]:
    """What an update changes, one reading per row; grouped when the clients differ alike (the usual case)."""
    groups: Dict[Tuple[Tuple[str, ...], Tuple[str, ...], str], List[str]] = {}
    for dr in stale:
        groups.setdefault((tuple(dr.added), tuple(dr.removed), dr.window or ""), []).append(dr.client)
    out: List[CardLine] = []
    for (added, removed, window), names in groups.items():
        if len(groups) > 1:
            out.append(f"{B}{' and '.join(names)}{R}")
        if added:
            out.append(f"An update adds {and_list(added)}.")
        if removed:
            out.append(f"An update removes {and_list(removed)}.")
        if window:
            out.append(row("context", window))
    return out


def and_list(items: Sequence[str]) -> str:
    """'a', 'a and b', 'a, b and c'."""
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def setup_lines(v: View, ui: UIState, here: List[Tuple[str, str]], stale: Sequence[Drift],
                tw: int) -> Tuple[str, List[CardLine]]:
    """The collapsed summary and the steps: this Mac; a VM or another computer (1-3); by hand. Full adds the
    provider names."""
    vm_ready = v.host not in ("127.0.0.1", "::1", "localhost")
    L: List[CardLine] = [heading("This Mac", tw)]
    if stale:
        summary = f"{YEL}⚠ out of date{R}"
        L.append(f"{YEL}⚠{R} {' and '.join(dr.client for dr in stale)} on this Mac "
                 f"{'is' if len(stale) == 1 else 'are'} out of date.")
        server = next((dr.server for dr in stale if dr.server and (dr.added or dr.removed)), "")
        if server:                      # single-model mode (23.4.4): the server's model changed
            L += cwrap(single_mode(server), tw)
        L += drift_rows(stale)
    elif here:
        summary = f"{GRN}✓ set up{R}"
        L.append(f"{GRN}✓{R} OpenCode and Pi on this Mac use this server.")
    else:
        summary = "not set up"
        L.append("OpenCode and Pi on this Mac are not set up for this server.")
    if full(ui, "csetup") and here:
        L += [row(c, f"provider {p}") for c, p in here]
    if full(ui, "csetup"):              # advanced, not recommended: the dashboard is the only place to set it (23.4)
        L.append(row("New-file gate", ("off" if not v.gate else f"{v.gate}: the main agent's new file number "
                                       f"{v.gate} of a turn goes to the coder") + f"   {DIM}g changes it{R}"))
    if ui.install_ask:
        what = ("installs OpenCode and Pi when they are missing or older (a download from npm). Then it writes "
                "their configs" if ui.install_ask == "all" else "writes the OpenCode and Pi configs for this server")
        L += para(f"{YEL}Run ./carl.sh install{' --config-only' if ui.install_ask == 'config' else ''} now? It "
                  f"{what}.{R}", tw=tw)
        L.append(buttons("", [("Run it (y)", "insyes"), ("Cancel (n)", "insno")]))
    elif ui.install and not ui.install.done:
        L.append(buttons("", [("Show the installer (i)", "insshow"), ("Stop it", "inscancel")]))
    else:
        first = "Install again (i)" if here else "Install on this Mac (i)"
        L += button_rows("", [(first, "insall"), ("Update the model lists (u)", "insconfig")], tw)
    L += ["", heading("A VM or another computer", tw)]
    L += cwrap(f"1. Start the server for it: {CYN}./carl.sh --vm{R}", tw, "   ")
    L.append(row("   Network", f"{GRN}{v.host}{R}" if vm_ready else f"{YEL}this Mac only{R}"))
    L += cwrap("2. Make the client package. Copy the zip to the computer, unzip it and run ./setup there.", tw, "   ")
    L += button_rows("   ", [("Make the client package (z)", "pkgmake")]
                     + ([("Show it in the Finder (f)", "pkgshow")] if package_path(ui) else []), tw)
    L += cwrap("3. Send the config to the computers that sync.", tw, "   ")
    L.append(row("   Sync", f"{GRN}{v.listeners} connected now{R}" if v.api
                 else f"{YEL}Off: the dashboard API is not running.{R}"))
    if v.unsent:
        L += cwrap(f"{YEL}{UNSENT}{R}", tw, "   ")
    L.append(buttons("   ", [("Send the config (P)", "inspush")]))
    L += ["", heading("By hand", tw)]
    L.append(row("address", f"{B}{v.base}/v1{R}"))
    L.append(Ln(row("key", (v.key if v.key_shown else ("•" * 8 + v.key[-4:] if v.key else f"{RED}no key file{R}"))
                    + f"   {DIM}{'hide' if v.key_shown else 'show'} (k){R}"), "key"))
    L += button_rows("", [("OpenCode config (o)", "opencode"), ("Pi config (p)", "pi"), ("curl test (c)", "curl")], tw)
    return summary, L


def para(*texts: str, tw: int) -> List[CardLine]:
    """Paragraphs wrapped to tw (no indent under the first line, as prose)."""
    return [x for t in texts for x in cwrap(t, tw)]


def how_lines(tip: str, tw: int) -> List[CardLine]:
    """HOW IT WORKS (Setup): the quick tip, then each way, short."""
    return para(heading("Quick tip", tw), f"{CYN}{tip}{R}",
            heading("On this Mac", tw),
            f"{CYN}./carl.sh install{R} (i) installs OpenCode and Pi when they are missing or older, into ~/.local "
            f"without sudo. Then it connects them to this server. It keeps your providers, your default model and "
            f"your agents, and it writes a backup first.",
            heading("A VM or another computer", tw),
            f"Start the server for it with {CYN}./carl.sh --vm{R}. The client package is a zip in dist/ with the "
            f"client folder, the address and the key. On the computer, unzip it and run {CYN}./setup{R} (on a Mac, "
            f"double-click setup.command). To update, unzip a new package over the folder and run ./setup again.",
            f"{YEL}The zip holds the API key: keep it secret, and delete it after the copy.{R}",
            heading("Other computers", tw),
            "Their setup adds a sync service. It keeps one connection to this dashboard and opens no port. It applies "
            "the config that you send (P) and makes backups. The Clients panel lists these computers.",
            heading("By hand", tw),
            "Each button copies one provider block, named carl. It adds a provider and keeps your others.", tw=tw)


def package_path(ui: UIState) -> str:
    """The zip of the last client package made in this session ("" when none)."""
    run = ui.package
    return run.outcome.path if run and run.outcome else ""


def package_summary(ui: UIState) -> str:
    """The client package card's title value."""
    run = ui.package
    if run is None or not run.done or run.outcome is None:
        return f"{YEL}in progress{R}"
    return f"{RED}not made{R}" if run.outcome.error else f"{GRN}made{R}"


def package_lines(ui: UIState, home: str, tw: int) -> List[CardLine]:
    """The client package: the zip, its size and files (or why CARL made none), its buttons, then the key warning
    and how to use it."""
    run = ui.package
    if run is None:
        return []
    if not run.done or run.outcome is None:
        return ["CARL is making the client package."]
    o = run.outcome
    out: List[CardLine] = []
    if o.error:
        out.append(f"{RED}{o.error}{R}")
    else:
        zp = wrap(home_short(o.path, home), tw - 7)          # (the label column: "files" + 2)
        out += [row("zip", f"{B}{zp[0]}{R}"), *[row("", f"{B}{p}{R}") for p in zp[1:]],
                row("size", file_size(o.size)), row("files", str(o.files))]
    out += ["", *button_rows("", ([("Show it in the Finder (f)", "pkgshow")] if o.path else [])
                             + [("Make it again (z)", "pkgmake"), ("Close (x)", "pkgclose")], tw)]
    for note in o.notes:
        text, *commands = note.split("\n")
        if text.startswith("CAUTION: "):
            rest = text[len("CAUTION: "):]
            text = f"{YEL}⚠ {rest[:1].upper()}{rest[1:]}{R}"
        out += ["", *para(text, tw=tw)]
        out += [f"  {CYN}{p}{R}" for c in commands for p in wrap(c, tw - 2)]
    return out


def install_summary(ins: InstallRun) -> str:
    """The installer card's title value: how it runs or ended."""
    if not ins.done:
        return f"{YEL}running{R}"
    return f"{GRN}done{R}" if ins.proc.returncode == 0 else f"{RED}stopped with an error{R}"


def install_lines(ui: UIState, n: int, tw: int) -> List[CardLine]:
    """What runs, the exit code when it failed, the installer's last n output lines (control characters removed;
    long ones wrap), and its buttons."""
    ins = ui.install
    if ins is None:
        return []
    failed = ins.done and ins.proc.returncode != 0
    out: List[CardLine] = [row("command", "./carl.sh install --config-only" if ins.what == "configs"
                               else "./carl.sh install")]
    if failed:
        out.append(row("exit code", f"{RED}{ins.proc.returncode}{R}"))
    body = [y for x in ins.lines if x.strip() for y in cwrap(f"{DIM}{clean(x)}{R}", tw, "  ")]
    out += body[-n:] if body else [f"{DIM}The installer is starting.{R}"]
    out += ["", buttons("", [("Stop it", "inscancel")] if not ins.done else
                        [("Close (x)", "insclose"), ("Run it again (i)", "insall")])]
    return out


# ---------------------------------------------------------------- Clients
def ago(t: float, now: float) -> str:
    """How long ago, in the units' style: '42 s ago', '5 h 12 min ago', '1 day ago', '3 days ago'."""
    s = max(0.0, now - t)
    days = int(s // 86400)
    return f"{days} {'day' if days == 1 else 'days'} ago" if days else f"{duration(s)} ago"


def forget_count(v: View, now: float, after: float = 7 * 86400) -> int:
    return sum(1 for c in v.clients if c.connected <= 0 and now - c.last_seen > after)


def clients_page(v: View, ui: UIState, here: List[Tuple[str, str]], stale: Sequence[Drift], w: int) -> List[Row]:
    """CLIENTS (the table), and HOW IT WORKS under it or, from SIDE_AT, to its right."""
    ui.keys = [("Tab", "section"), ("L", "level"), ("P", "send the config"), ("↑↓", "scroll"), ("[ ]", "Setup")]
    ui.keys_more = ["Forget (a button) removes the computers that CARL did not see for a week. It does not ask first. "
                    "A computer comes back when it connects again."]
    ui.sections = list(CLIENT_SECTIONS)
    n = len(v.clients)
    summary = f"{n} {'computer' if n == 1 else 'computers'}"
    cw = w - HELP_W - 1
    need = table_width(v, here, stale, full(ui, "clients"))
    if w >= SIDE_AT and (need <= cw - 4 or need > w - 4):
        table_ = section(ui, "clients", "CLIENTS", summary, clients_lines(v, here, stale, cw - 4, full(ui, "clients")),
                         cw, 3)
        side = section(ui, "clhow", "HOW IT WORKS", "", how_clients(v, HELP_W - 4), HELP_W, 2)
        return unpad(side_by_side(table_, side, cw, pad_left=False))
    # (from SIDE_AT, a table that needs one row per client more than the help beside it: the help goes under it)
    return (section(ui, "clients", "CLIENTS", summary, clients_lines(v, here, stale, w - 4, full(ui, "clients")), w, 3)
            + section(ui, "clhow", "HOW IT WORKS", "", how_clients(v, w - 4), w, 2))


def how_clients(v: View, tw: int) -> List[CardLine]:
    tip = ("Press P to send the installed models to every computer that syncs." if v.clients
           else "To add a computer, make the client package in Setup (z). Then run ./setup on the computer.")
    return para(heading("Quick tip", tw), f"{CYN}{tip}{R}",
            heading("Who is in the list", tw),
            "The list shows OpenCode and Pi on this Mac, and each computer whose setup added the sync. Such a "
            "computer gets the config that you send.",
            heading("Add a computer", tw),
            f"In Setup, make the client package (z, or {CYN}./carl.sh package{R}). Copy the zip to the computer, "
            f"unzip it and run {CYN}./setup{R} there. The package has the address and the key of the server.", tw=tw)


def table(head: Sequence[str], rows: Sequence[Sequence[str]], gap: int = 2) -> Tuple[List[int], List[str]]:
    """Column widths (the widest cell or header) and the table's lines: a dim header, then the rows."""
    widths = [max([len(h)] + [vlen(r[i]) for r in rows]) for i, h in enumerate(head)]
    pad = " " * gap

    def line(cells: Sequence[str]) -> str:
        return pad.join(fit(c, wd) for c, wd in zip(cells, widths)).rstrip()
    return widths, [f"{DIM}{line(head)}{R}", *[line(r) for r in rows]]


HEAD = ["Computer", "Last seen", "User", "Syncs", "Config", "OS", "Address"]
BLOCK_LABELS = HEAD[2:]                 # the label rows of a computer's block (its name and state are its first line)
Cells = List[List[str]]
CONFIG_NOTES = {                        # what a config state means, under the table when a computer has it
    "out of date": "Out of date: press u in Setup to update OpenCode and Pi on this Mac.",
    "at next sync": "At next sync: the computer applies the new config the next time it syncs.",
    "on hold": "On hold: the computer does not apply a new config at once. On that computer, open /carl in "
               "OpenCode or Pi and choose Apply the new config now."}


def client_cells(v: View, here: List[Tuple[str, str]], stale: Sequence[Drift], version: str,
                 is_full: bool) -> Tuple[List[str], Cells, List[str]]:
    """The table's header and cells, one list per computer (this Mac first): name, ● connected / ○ last seen, user,
    how it syncs, its config, OS, address (full: the version it has); and the config states that need a note."""
    now = time.time()
    states: List[str] = []
    mac = (f"{GRN}up to date{R}" if here and not stale else f"{YEL}out of date{R}" if here
           else f"{DIM}not set up{R}")
    if here and stale:
        states.append("out of date")
    cells: Cells = [[f"{B}This Mac{R}", NA, NA, NA, mac, "macOS", NA] + ([NA] if is_full else [])]
    for c in v.clients:
        seen = f"{GRN}● connected{R}" if c.connected > 0 else f"{DIM}○ {ago(c.last_seen, now)}{R}"
        if not version:
            conf = f"{DIM}nothing sent{R}"
        elif c.applied == version:
            conf = f"{GRN}up to date{R}"
        else:
            states.append("at next sync" if c.auto_apply else "on hold")
            conf = f"{YEL}{states[-1]}{R}"
        cells.append([f"{B}{c.host or c.id}{R}", seen, c.user or NA, "always" if c.mode == "service" else "at start",
                      conf, c.os or NA, c.address or NA] + ([c.applied or "none"] if is_full else []))
    return HEAD + (["Version"] if is_full else []), cells, [k for k in CONFIG_NOTES if k in states]


def table_width(v: View, here: List[Tuple[str, str]], stale: Sequence[Drift], is_full: bool) -> int:
    """The columns the clients table needs with one row per computer."""
    head, cells, _ = client_cells(v, here, stale, v.pushed.partition(" at ")[0], is_full)
    return max(vlen(x) for x in table(head, cells)[1])


def blocks(head: Sequence[str], cells: Cells) -> List[CardLine]:
    """Each computer as a small block (when the table does not fit): its name in bold and its state, then a label
    row per column (this Mac: its config only), a blank line between computers."""
    out: List[CardLine] = []
    for i, cs in enumerate(cells):
        out += [""] if i else []
        out.append(cs[0] + (f"  {cs[1]}" if i else ""))
        rows = [(h, x) for h, x in zip(head[2:], cs[2:]) if i or h == "Config"]
        out += [row(f"  {h}", x) for h, x in rows]
    return out


def clients_lines(v: View, here: List[Tuple[str, str]], stale: Sequence[Drift], tw: int,
                  is_full: bool = False) -> List[CardLine]:
    """The last config sent and the sync address; a table of this Mac and each computer that syncs (whole host
    name, ● connected / ○ last seen, user, how it syncs, its config, OS, address; full: the version it has), or,
    when one row per computer does not fit tw, a block per computer; a note per config state that needs one; then
    Send and Forget."""
    version, _, when = v.pushed.partition(" at ")
    L: List[CardLine] = [row("last config sent", when if v.pushed else f"{DIM}nothing sent yet{R}")]
    if is_full and v.pushed:
        L.append(row("version", version))
    L.append(row("sync address", f"{GRN}{v.api}{R}" if v.api
                 else f"{YEL}Off: other computers cannot sync now.{R}"))
    if v.unsent:
        L += ["", *para(f"{YEL}{UNSENT}{R}", tw=tw)]
        if v.single:
            L += para(single_mode(v.single), tw=tw)
    L.append("")
    head, cells, notes = client_cells(v, here, stale, version, is_full)
    _, one = table(head, cells)
    L += one if max(vlen(x) for x in one) <= tw else blocks(head, cells)
    if not v.clients:
        L += ["", f"{DIM}No other computer yet.{R}"]
    if notes:
        L += ["", *para(*[f"{DIM}{CONFIG_NOTES[k]}{R}" for k in notes], tw=tw)]
    old = forget_count(v, time.time())
    n = len(v.clients)
    send = f"Send the config to {n} {'computer' if n == 1 else 'computers'} (P)" if n else "Send the config (P)"
    forget = (f"Forget {old} {'computer' if old == 1 else 'computers'} not seen for a week" if old
              else "Forget the computers not seen for a week")
    return [*L, "", *button_rows("", [(send, "inspush"), (forget, "clforget")], tw)]
