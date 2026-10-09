"""The Caching panel: the disk cache (diskcache.py: saved prompts and saved sessions), its limit, when
CARL saves, the shared storage, the cache of the Gemma models (sliding window), what the disk cache
holds, and Clear; beside them (or under them) the Quick tip and the help. Every change is saved at
once. Each part is a section with its own level; the page scrolls. Pure, except the clock (the
files' ages)."""
from __future__ import annotations

import time
from typing import List, Sequence, Tuple

from carl_core.domain.units import duration, file_size

from ..cards import sections as register
from ..diskcache import PROMPT, CacheConfig, CacheFile, describe, gb as gb_text, shared_saving, used
from ..fmt import CYN, DIM, GRN, R, YEL, CardLine, Row, bar, buttons, home_short, row
from ..state import UIState
from ..words import plural
from .page import Part, body_height, help_part, layout, scrolled, section, side_width
from .common import choice_line

register(three=["cachedisk"], two=["caching", "cachetip", "cacheabout", "cachehow", "cacheexp", "cacheram"])

DISK_CHOICES = (2, 5, 10, 20, 50)                      # the Caching panel's disk limits (GB)
AUTO_CHOICES = (30, 120, 300, 600)                    # save = auto: seconds of unsaved reading (default 120)
CACHE_ROWS = ("disk", "prefix", "sessions", "save", "auto", "share", "swa", "move")   # the rows, in order (↑↓)
ROW_LABELS = {"disk": "Disk limit", "prefix": "Saved prompts", "sessions": "Saved sessions", "save": "When to save",
              "auto": "Save after", "share": "Shared storage", "swa": "Gemma models", "move": "Other templates"}
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
    "auto": "Auto: a Gemma model keeps its full cache when that fits this Mac. Then CARL can restore saved sessions and "
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
    "share": "Shared storage: CARL stores a saved session as its changes to its saved prompt. This uses less disk."}
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


def save_text(conf: CacheConfig) -> Tuple[Tuple[str, str], ...]:
    return (("auto", f"auto (after {duration(conf.auto_s)})"), ("turn", "every turn"), ("switch", "when it leaves"),
            ("stop", "before a stop"))


SWA_TEXT = (("auto", "auto (full when it fits)"), ("full", "full cache"), ("window", "window cache"))


def file_rows(files: Sequence[CacheFile], shown: int, full: bool) -> List[CardLine]:
    """The saved files, newest first, as a table (shown rows, then how many more); full: "(shared)" on a
    session stored as changes."""
    if not files:
        return [f"{DIM}The disk cache is empty. OpenCode and Pi save here while they work.{R}"]
    rows = []
    for f in sorted(files, key=lambda f: -f.mtime):
        model, _, who = describe(f.name).partition(" · ")
        rows.append((f, model, who))
    mw = max(len(m) for _, m, _ in rows) + 2
    ww = max(len(x) for _, _, x in rows) + 2
    now = time.time()
    aw = max(len(duration(now - f.mtime)) for f, _, _ in rows[:shown])
    out: List[CardLine] = [f"{DIM}{'Saved':<9}{'Model':<{mw}}{'Agent or session':<{ww}}{'Size':>7}  Age{R}"]
    for f, model, who in rows[:shown]:
        what = "prompt" if f.kind == PROMPT else "shared" if f.packed and full else "session"
        out.append(f"{DIM}{what:<9}{R}{model:<{mw}}{who:<{ww}}{file_size(f.bytes):>7}  "
                   f"{DIM}{duration(now - f.mtime):<{aw}}{R}")
    if len(rows) > shown:
        out.append(f"{DIM}{plural(len(rows) - shown, 'more file')} {'is' if len(rows) - shown == 1 else 'are'} "
                   f"in the folder.{R}")
    if full and any(f.packed for f, _, _ in rows[:shown]):
        out.append(f"{DIM}Shared: a saved session stored as changes to its saved prompt.{R}")
    return out


def caching_panel(ui: UIState, conf: CacheConfig, files: Sequence[CacheFile], folder: str, cols: int,
                  home: str, api: str = "", height: int = 0) -> List[Row]:
    """The sections: CACHING (the settings), ON THE DISK (what it holds, Clear, the files); the Quick tip and
    the help beside them or under them. The page scrolls."""
    use = used(files)
    n = len(files)
    ui.cache_row = max(0, min(ui.cache_row, len(CACHE_ROWS) - 1))
    key = CACHE_ROWS[ui.cache_row]
    h = body_height(height)
    screen = h + 8                                      # the terminal's rows: 5 files at 40, 10 at 50, all at 60
    shown = 5 if screen < 50 else 10 if screen < 60 else n

    def settings_section(w: int) -> List[Row]:
        L: List[CardLine] = [f"{DIM}CARL saves prompts and sessions on the disk for OpenCode and Pi. Then a session "
                             f"continues without a long read.{R}", ""]
        gbs = sorted({*DISK_CHOICES, conf.disk_gb})
        onoff = (("on", "on"), ("off", "off"))
        rows = {
            "disk": ([(str(g), f"{g} GB", f"cache:disk:{g}") for g in gbs], str(conf.disk_gb)),
            "prefix": ([(v, t, f"cache:prefix:{v}") for v, t in onoff], "on" if conf.prefix else "off"),
            "sessions": ([(v, t, f"cache:sessions:{v}") for v, t in onoff], "on" if conf.sessions else "off"),
            "save": ([(k, t, f"cache:save:{k}") for k, t in save_text(conf)], conf.save),
            "auto": ([(str(a), duration(a), f"cache:auto:{a}") for a in sorted({*AUTO_CHOICES, conf.auto_s})],
                     str(conf.auto_s)),
            "share": ([(v, t, f"cache:share:{v}") for v, t in onoff], "on" if conf.share else "off"),
            "swa": ([(k, t, f"cache:swa:{k}") for k, t in SWA_TEXT], conf.swa),
            "move": ([(k, t, f"cache:move:{k}") for k, t in MOVE_TEXT], conf.move)}
        for k in CACHE_ROWS:
            opts, cur = rows[k]
            L += choice_line(ROW_LABELS[k], opts, cur, w - 4, 19, sel=k == key)
        return section(ui, "caching", "CACHING", f"{gb_text(use)} of {conf.disk_gb} GB", L, w, 2)

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
        L.append(row("other computers", f"{GRN}They use these settings through the dashboard API ({api}).{R}" if api
                     else f"{YEL}Not now. The dashboard API is not running.{R}"))
        L += ["", buttons("", [("Clear the disk cache (c)", "cache:clear")]), "", *file_rows(files, shown, full)]
        return section(ui, "cachedisk", "ON THE DISK", plural(n, "file"), L, w, 3)

    about = SAVE_HELP[conf.save].replace("AUTO", duration(conf.auto_s)) if key in ("save", "auto") else \
        SWA_HELP[conf.swa] if key == "swa" else MOVE_HELP[conf.move] if key == "move" else ROW_HELP[key]
    parts: List[Part] = [
        Part("caching", settings_section), Part("cachedisk", disk_section),
        help_part(ui, "cachetip", "QUICK TIP", [f"{CYN}Press ↑↓ to select a row and ← → to change it. CARL saves each "
                                                f"change at once.{R}"]),
        help_part(ui, "cacheabout", f"ABOUT: {ROW_LABELS[key].upper()}", [about]),
        help_part(ui, "cachehow", "HOW IT WORKS", [
            "OpenCode and Pi save their prompts and sessions through the server. Then the server does not read "
            "everything again after a restart, a model switch or many other sessions. A saved file is for one model "
            "file and one llama.cpp build. The sessions of another model wait for that model (router mode loads it "
            "first). To turn the disk cache off on one computer, run its installer with NO_CACHE=1."]),
        help_part(ui, "cacheexp", "EXPERIMENTAL", [f"{YEL}The disk cache is new. If you see a problem, set Saved "
                                                   f"sessions or Saved prompts to off here.{R}"]),
        help_part(ui, "cacheram", "RAM CACHE", ["The server also keeps sessions in RAM while it runs. Set its size in "
                                                "the RAM cache row of the Server panel."])]
    rows = layout(ui, parts, cols, side_width(cols), balance=True)
    ui.keys = [("↑↓", "row"), ("← →", "change"), ("c", "clear")]
    if len(rows) > h:
        ui.keys.append(("PgUp PgDn", "scroll"))
    ui.keys += [("Tab", "section"), ("L", "level"), ("[ ]", "panels")]
    ui.keys_more = ["You can also click a choice. Clear asks before it removes files."]
    return scrolled(ui, rows, h, "page_scroll")
