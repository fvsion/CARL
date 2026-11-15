"""The Caching panel (the first of Settings > Context management, A73): the slots, the RAM cache (where the Server
panel sets them) and the disk cache (slot_folder.py: saved prompts and saved sessions), its limit, when
CARL saves, the shared storage, the slots kept over a restart (slot_handoff.py), the cache of the Gemma models (sliding window), what the cache did (Phase 23.5.1.1:
cache_events.py, a table over the last hour, today or since the start; t changes it), what the disk cache
holds, and Clear; beside them (or under them) the Quick tip and the help. Every change is saved at
once. Each part is a section with its own level; the page scrolls. Pure, except the clock (the
files' ages)."""
from __future__ import annotations

import time
from typing import List, Mapping, Optional, Sequence, Tuple

from ....domain.server.slot_files import (PROMPT, SLOT, CacheConfig, CacheFile, describe, gb as gb_text,
                                          session_id, shared_saving, used)
from ....domain.shared.units import duration, file_size
from ....ports.cache import StatRow
from ....ui.fmt import B, CYN, DIM, GRN, R, YEL, CardLine, Ln, Row, bar, buttons, fit, home_short, row, vlen
from ....ui.words import computer, plural
from ...act import CacheOp, cache
from ...settings_texts import range_ends, range_words
from ...shown import Shown
from ...state import UIState
from .common import choice_line
from .page import Part, body_height, help_part, layout, scrolled, section, side_width

NUMBER_ROWS = {"disk": "disk_gb", "auto": "auto_s"}     # the number rows (W1.6, B6) -> their key of config.json "cache"
PREV = "prev"                                           # cache:KEY:prev, the value before the current one
CACHE_ROWS = ("disk", "prefix", "sessions", "save", "auto", "share", "keep", "swa", "move")   # the rows, in order (↑↓)
ROW_LABELS = {"disk": "Disk limit", "prefix": "Saved prompts", "sessions": "Saved sessions", "save": "When to save",
              "auto": "Save after", "share": "Shared storage", "keep": "Keep slots", "swa": "Gemma models",
              "move": "Other templates"}
SAVE_HELP = {
    "auto": "Auto: CARL saves a session when its unsaved part would take AUTO to read again (Save after). The measured "
            "read speed of the model gives the time. CARL also saves before a session leaves the server: when another "
            "session needs its slot, at a router switch, or at a stop. This writes little. After a crash, a session loses "
            "at most AUTO of reading.",
    "turn": "Every turn: CARL saves after each answer. A crash loses nothing. This writes the most to the disk: up to "
            "about 1 GB per turn for a long session on the 35B.",
    "switch": "When it leaves: CARL saves before a session leaves the server: when another session needs its slot, at a "
              "router switch, or at a stop or restart from the dashboard. A crash or a stop outside the dashboard (Ctrl-C) loses "
              "the unsaved part.",
    "stop": "Before a stop: CARL saves only before the dashboard stops or restarts the server, and before a router "
            "switch. The sessions that moved to the RAM cache before it are lost at the stop."}
SWA_HELP = {
    "auto": "Auto: a Gemma model keeps its full cache when that fits {this}. Then CARL can restore saved sessions and "
            "prompts. If not, it uses the window cache: less memory, but CARL cannot restore them. It applies at the "
            "next start.",
    "full": "Full cache: CARL can restore saved sessions and prompts, but the model uses more memory (Gemma 4 E4B with "
            "2 slots of 96K tokens: 2.3 GiB more). It applies at the next start.",
    "window": "Window cache: the model uses the least memory. CARL cannot restore the saved sessions and prompts of a Gemma model, so "
              "the server reads them again. It applies at the next start."}
ROW_HELP = {
    "disk": "The most that the disk cache can use. At the limit, CARL removes the oldest saved sessions first, then the "
            "oldest saved prompts.",
    "prefix": "Saved prompts: CARL saves each agent's system prompt and tools after the first read. A new session starts "
              "from them and reads only its own messages.",
    "sessions": "Saved sessions: CARL saves a session at the end of a turn. When the session continues and the server does not "
                "hold it, CARL restores it before its next request.",
    "share": "Shared storage: CARL stores a saved session as its changes to its saved prompt. This uses less disk.",
    "keep": "Keep slots: CARL saves the slots before a restart that it makes (Apply, Stop, Auto-tune, a router load "
            "from the dashboard) and puts them back after it. This is for clients without CARL's plugins: OpenCode "
            "without them, other clients, curl. The client's next request then finds its prompt in a slot. CARL puts "
            "nothing back when the model file, the llama.cpp build, the K and V types, the flash attention, the "
            "sliding-window cache or the rope settings changed. "
            "Off: nothing is saved (on the 35B a slot is about 0.3 to 0.6 GB on the disk)."}
MOVE_HELP = {
    "off": "Leave in place (the default): for a model whose chat template CARL does not know (not Qwen, not Gemma 4), "
           "the parts of the prompt that change per project (the folder, the date, AGENTS.md) stay in the system "
           "prompt. The model reads them as standing instructions, but each new project reads the whole prompt again. "
           "Qwen models get them at the start of your first message: they follow AGENTS.md more often there (measured "
           "2026-10-08). Gemma 4 models keep them in place.",
    "auto": "Move to your message: for a model whose chat template CARL does not know, the parts of the prompt that "
            "change per project go to the start of your first message. Then one saved prompt serves every project. "
            "Do not use it with a template that also uses a sliding-window cache: CARL cannot restore its prompts."}
MOVE_TEXT = (("off", "leave in place"), ("auto", "move to your message"))


NUMBER_HELP = {     # (the simple level, the full level)
    "auto": ("Press ← → for a preset: 15 s, 30 s, 45 s, 1 min, 1 min 30 s, 2 min or 3 min. At the full level of "
             "CACHING (L), ← → take finer steps, and Enter types any time.",
             "Press ← → to change it: 10 s steps up to 2 min, 30 s steps up to 10 min, then 1 min steps. Or press "
             "Enter and type a time: 90, 90s, 2m, 1m30."),
    "disk": ("Press ← → for a preset: 2, 5, 10, 20 or 50 GB. At the full level of CACHING (L), ← → take finer steps, "
             "and Enter types any size.",
             "Press ← → to change it: 1 GB steps up to 10 GB, then 5, 10 and 50 GB steps. Or press Enter and type a "
             "size in GB.")}


def save_text(conf: CacheConfig) -> Tuple[Tuple[str, str], ...]:
    return (("auto", f"auto (after {duration(conf.auto_s, secs=True)})"), ("turn", "every turn"),
            ("switch", "when it leaves"), ("stop", "before a stop"))


def number_value(key: str, v: int) -> str:
    """A number row's value: 1 min 30 s, 15 GB."""
    return duration(v, secs=True) if key == "auto" else f"{v} GB"


def number_line(label: str, key: str, v: int, w: int, lw: int, sel: bool, edit: Optional[str], full: bool = True,
                free: int = 0) -> Ln:
    """A number row (B6): its value, in ‹ › when selected (each arrow a click), or the text being typed; its range at
    this level beside it ("10 s to 60 min"; the presets' at the simple level), and for Disk limit the free disk
    ("1 to 1000 GB; 233 GB free"; free in bytes, 0 unknown)."""
    head = (f"{CYN}{B}›{R} " if sel else "  ") + f"{B if sel else ''}{label:<{lw - 2}}{R}"
    spans = []
    if edit is not None and sel:
        cell = f"{edit}▏"
    elif sel:
        val = number_value(key, v)
        cell = f"‹ {val} ›"
        spans = [(lw, lw + 2, cache(key, PREV)), (lw + 3 + vlen(val), lw + 5 + vlen(val), cache(key))]
    else:
        cell = number_value(key, v)
    ends = range_ends(NUMBER_ROWS[key], full=full) + (f"; {free / 10 ** 9:.0f} GB free" if key == "disk" and free
                                                       else "")
    return Ln(fit(f"{head}{cell:<18}{DIM}{ends}{R}", w), spans=spans)


SWA_TEXT = (("auto", "auto (full when it fits)"), ("full", "full cache"), ("window", "window cache"))


def file_rows(files: Sequence[CacheFile], shown: int, full: bool,
              names: Optional[Mapping[str, str]] = None) -> List[CardLine]:
    """The saved files, newest first, as a table (shown rows, then how many more); full: "(shared)" on a
    session stored as changes. names: a session's current name by its ID (Phase 23.5 item 4): the name, the ID grey
    after it, cut."""
    if not files:
        return [f"{DIM}The disk cache is empty. OpenCode and Pi save here while they work.{R}"]
    rows = []
    for f in sorted(files, key=lambda f: -f.mtime):
        model, _, who = describe(f.name).partition(" · ")
        name = (names or {}).get(session_id(f.name), "")
        rows.append((f, model, f"{name}  {who[:12]}…" if name and len(who) > 12 else f"{name}  {who}" if name else who))
    mw = max(len(m) for _, m, _ in rows) + 2
    ww = max(len(x) for _, _, x in rows) + 2
    now = time.time()
    aw = max(len(duration(now - f.mtime)) for f, _, _ in rows[:shown])
    out: List[CardLine] = [f"{DIM}{'Saved':<9}{'Model':<{mw}}{'Agent or session':<{ww}}{'Size':>7}  Age{R}"]
    for f, model, who in rows[:shown]:
        what = "prompt" if f.kind == PROMPT else "slot" if f.kind == SLOT else "shared" if f.packed and full else "session"
        name, sep, sid = who.partition("  ")          # a named session: its name, then its ID in grey
        shown_who = f"{name}{sep}{DIM}{sid}{R}" if sep else who
        out.append(f"{DIM}{what:<9}{R}{model:<{mw}}{shown_who}{' ' * (ww - len(who))}{file_size(f.bytes):>7}  "
                   f"{DIM}{duration(now - f.mtime):<{aw}}{R}")
    if len(rows) > shown:
        out.append(f"{DIM}{plural(len(rows) - shown, 'more file')} {'is' if len(rows) - shown == 1 else 'are'} "
                   f"in the folder.{R}")
    if full and any(f.packed for f, _, _ in rows[:shown]):
        out.append(f"{DIM}Shared: a saved session stored as changes to its saved prompt.{R}")
    return out


def caching_panel(ui: UIState, conf: CacheConfig, files: Sequence[CacheFile], folder: str, cols: int,
                  home: str, api: str = "", height: int = 0, names: Optional[Mapping[str, str]] = None,
                  window: str = "", did: Sequence[StatRow] = (), did_when: str = "",
                  out: Optional[Shown] = None, free: int = 0) -> List[Row]:
    """The sections: CACHING (the settings), ON THE DISK (what it holds, Clear, the files); the Quick tip and
    the help beside them or under them. The page scrolls. free: the free disk where the cache saves (bytes; 0
    unknown). CACHING's full level steps the number rows finely and lets a value be typed (B6)."""
    use = used(files)
    n = len(files)
    out = Shown() if out is None else out
    key = CACHE_ROWS[out.fix("cache_row", max(0, min(ui.cache_row, len(CACHE_ROWS) - 1)))]
    h = body_height(height)
    screen = h + 8                                      # the terminal's rows: 5 files at 40, 10 at 50, all at 60
    shown = 5 if screen < 50 else 10 if screen < 60 else n
    full = ui.levels.get("caching", 1) == 2

    def settings_section(w: int) -> List[Row]:
        L: List[CardLine] = [f"{DIM}CARL saves prompts and sessions on the disk for OpenCode and Pi. Then a session "
                             f"continues without a long read.{R}", ""]
        onoff = (("on", "on"), ("off", "off"))
        rows = {
            "prefix": ([(v, t, cache("prefix", v)) for v, t in onoff], "on" if conf.prefix else "off"),
            "sessions": ([(v, t, cache("sessions", v)) for v, t in onoff], "on" if conf.sessions else "off"),
            "save": ([(k, t, cache("save", k)) for k, t in save_text(conf)], conf.save),
            "share": ([(v, t, cache("share", v)) for v, t in onoff], "on" if conf.share else "off"),
            "keep": ([(v, t, cache("keep", v)) for v, t in onoff], "on" if conf.keep_slots else "off"),
            "swa": ([(k, t, cache("swa", k)) for k, t in SWA_TEXT], conf.swa),
            "move": ([(k, t, cache("move", k)) for k, t in MOVE_TEXT], conf.move)}
        for k in CACHE_ROWS:
            if k in NUMBER_ROWS:
                L.append(number_line(ROW_LABELS[k], k, conf.disk_gb if k == "disk" else conf.auto_s, w - 4, 19,
                                     k == key, ui.edit, full, free))
                continue
            opts, cur = rows[k]
            L += choice_line(ROW_LABELS[k], opts, cur, w - 4, 19, sel=k == key)
            if k == "keep" and ui.handoff:              # the last start's handoff (C-138)
                L.append(f"{'':19}{DIM}{ui.handoff}{R}")
        return section(ui, "caching", "CACHING", f"{gb_text(use)} of {conf.disk_gb} GB", L, w, 3)

    def did_section(w: int) -> List[Row]:
        """WHAT THE CACHE DID: a table (user, 2026-10-11: "less prose and more table")."""
        def cells(r: StatRow) -> str:
            label = f"{DIM}{r.what:<21}{R}"                  # grey like the panel's other labels
            if r.what == "All requests":
                return f"{label}{'':>6}  {r.tokens}"
            return f"{label}{r.count:>6}  {r.tokens:>8}  {r.time:<15}{r.written:>8}".rstrip()
        L: List[CardLine] = [f"{DIM}{cells(StatRow('', 'Count', 'Tokens', 'Time', 'Written'))}{R}"]
        L += [cells(r) for r in did]
        return section(ui, "cachedid", "WHAT THE CACHE DID", f"{did_when}   {DIM}(t changes it){R}", L, w, 2)

    def disk_section(w: int) -> List[Row]:
        full = ui.levels.get("cachedisk", 1) == 2
        L: List[CardLine] = [row("used", f"{bar(use / conf.limit if conf.limit else 0, 18)}  {gb_text(use)} of "
                                         f"{conf.disk_gb} GB"),
                             row("files", str(n)), row("folder", home_short(folder, home))]
        if full:
            L.append(row("server flag", "--slot-save-path (the folder)"))
        packed = [f for f in files if f.packed]
        if packed:
            L.append(row("shared", f"{plural(len(packed), 'saved session')} {'is' if len(packed) == 1 else 'are'} stored "
                                   f"as changes. This saves {gb_text(shared_saving(files))} on the disk."))
        if free and conf.disk_gb * 10 ** 9 > use + free:      # the disk fills before the limit (kept, B6)
            L.append(row("disk limit", f"{YEL}⚠ {conf.disk_gb} GB is more than the disk has for the cache "
                                       f"({gb_text(use + free)}: {free / 10 ** 9:.0f} GB free). The disk fills "
                                       f"first.{R}"))
        L.append(row("other computers", f"{GRN}They use these settings through the dashboard API ({api}).{R}" if api
                     else f"{YEL}Not now. The dashboard API is not running.{R}"))
        if window:              # the running model's cache can't reuse a saved state (Phase 23.5)
            L.append(row("window cache", f"{window} runs with the window cache: OpenCode and Pi save and restore "
                                         f"nothing for it."))
        L += ["", buttons("", [("Clear the disk cache (c)", cache(CacheOp.CLEAR))]), "", *file_rows(files, shown, full, names)]
        return section(ui, "cachedisk", "ON THE DISK", plural(n, "file"), L, w, 3)

    about = SAVE_HELP[conf.save].replace("AUTO", duration(conf.auto_s, secs=True)) if key in ("save", "auto") else \
        SWA_HELP[conf.swa].replace("{this}", computer()) if key == "swa" else MOVE_HELP[conf.move] if key == "move" else ROW_HELP[key]
    about_lines: List[CardLine] = [about]
    if key in NUMBER_ROWS:
        about_lines = [f"{CYN}{NUMBER_HELP[key][full]}{R}", "", about, "",
                       row("range" if full else "presets", range_words(NUMBER_ROWS[key], full=full)),
                       row("config key", f"cache.{NUMBER_ROWS[key]}" + (" (seconds)" if key == "auto" else ""))]
    parts: List[Part] = [
        Part("caching", settings_section), *([Part("cachedid", did_section)] if did else []),
        Part("cachedisk", disk_section),
        help_part(ui, "cachetip", "QUICK TIP", [
            f"{CYN}Press ↑↓ to select a row and ← → to change it. "
            + ("On a number row, press Enter to type a value. " if full else
               "For finer steps and typed values, open the full level of CACHING (L). ")
            + f"CARL saves each change at once.{R}"]),
        help_part(ui, "cacheabout", f"ABOUT: {ROW_LABELS[key].upper()}", about_lines),
        help_part(ui, "cachehow", "HOW IT WORKS", [
            "OpenCode and Pi save their prompts and sessions through the server. Then the server does not read "
            "everything again after a restart, a model switch or many other sessions. A saved file is for one model "
            "file and one llama.cpp build. The sessions of another model wait for that model (router mode loads it "
            "first). To turn the disk cache off on one computer, run its installer with NO_CACHE=1."]),
        help_part(ui, "cacheexp", "EXPERIMENTAL", [f"{YEL}The disk cache is new. If you see a problem, set Saved "
                                                   f"sessions or Saved prompts to off here.{R}"]),
        help_part(ui, "cacheram", "SLOTS AND RAM CACHE", [
            "Caching holds a session in three places: its slot while it runs, the RAM cache when it leaves its slot, "
            "and the disk cache here. The slots and the RAM cache are set in the Server panel (the Slots and RAM cache "
            "rows), since a change to them restarts the server."])]
    rows = layout(out, parts, cols, side_width(cols), balance=True)
    if ui.edit is not None:
        out.keys = [("0-9 s m :" if key == "auto" else "0-9", "type the " + ("time" if key == "auto" else "size")),
                    ("Enter", "keep it"), ("Esc", "drop it"), ("Backspace", "delete")]
        out.keys_more = [f"The range: {range_ends(NUMBER_ROWS[key])}."]
        return scrolled(ui, out, layout(out, parts, cols, side_width(cols), balance=True), h, "page_scroll")
    out.keys = [("↑↓", "row"), ("← →", "change"), *([("Enter", "type a value")] if key in NUMBER_ROWS and full else []),
                ("c", "clear")] + ([("t", "time")] if did else [])
    if len(rows) > h:
        out.keys.append(("PgUp PgDn", "scroll"))
    out.keys += [("Tab", "section"), ("L", "level"), ("[ ]", "panels")]
    out.keys_more = ["You can also click a choice. Clear asks before it removes files."]
    return scrolled(ui, out, rows, h, "page_scroll")
