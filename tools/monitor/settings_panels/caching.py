"""The Caching panel: the disk cache (diskcache.py: saved prompts and saved sessions), its limit, when
CARL saves, the shared storage, the cache of the Gemma models (sliding window), what the disk cache
holds, and Clear. Every change is saved at once. Pure, except the clock (the files' ages)."""
from __future__ import annotations

import time
from typing import List, Sequence, Tuple

from carl_core.domain.units import duration, file_size

from ..diskcache import PROMPT, CacheConfig, CacheFile, describe, gb as gb_text, shared_saving, used
from ..fmt import (DIM, GRN, R, YEL, CardLine, Row, Section, bar, button_rows, cwrap, draw_card, fit, heading,
                   home_short, indent, lv, with_side)
from ..state import UIState
from ..words import plural
from .common import choice_line

DISK_CHOICES = (2, 5, 10, 20, 50)                      # the Caching panel's disk limits (GB)
AUTO_CHOICES = (30, 120, 300, 600)                    # save = auto: seconds of unsaved reading (default 120)
CACHE_ROWS = ("disk", "prefix", "sessions", "save", "auto", "share", "swa", "move")   # the rows, in order (↑↓)
ROW_LABELS = {"disk": "Disk limit", "prefix": "Saved prompts", "sessions": "Saved sessions", "save": "When to save",
              "auto": "Save after", "share": "Shared storage", "swa": "Gemma models", "move": "Other templates"}
SAVE_HELP = {
    "auto": "auto: CARL saves a session when its unsaved part would take AUTO to read again (Save after). The measured "
            "read speed of the model gives the time. CARL also saves before a session leaves the server: another "
            "session needs its slot, a router switch, or a stop. This writes little. After a crash, a session loses "
            "at most AUTO of reading.",
    "turn": "every turn: CARL saves after each answer. A crash loses nothing. This writes the most to the disk: up to "
            "about 1 GB per turn for a long session on the 35B.",
    "switch": "when it leaves: CARL saves before a session leaves the server: another session needs its slot, a router "
              "switch, or a stop or restart from the dashboard. A crash or a stop outside the dashboard (Ctrl-C) loses "
              "the unsaved part.",
    "stop": "before a stop: CARL saves only before the dashboard stops or restarts the server, and before a router "
            "switch. The sessions that moved to the RAM cache before it are lost at the stop."}
SWA_HELP = {
    "auto": "auto: a Gemma model keeps its full cache when that fits this Mac. Then CARL can restore saved sessions and "
            "prompts. If not, it uses the window cache: less memory, but CARL cannot restore them. It applies at the "
            "next start.",
    "full": "full cache: CARL can restore saved sessions and prompts, but the model uses more memory (Gemma 4 E4B with "
            "2 slots of 96K tokens: 2.1 GiB more). It applies at the next start.",
    "window": "window cache: the least memory. CARL cannot restore the saved sessions and prompts of a Gemma model, so "
              "the server reads them again. It applies at the next start."}
ROW_HELP = {
    "disk": "The most that the disk cache can use. At the limit, CARL removes the oldest saved sessions first, then the "
            "oldest saved prompts.",
    "prefix": "Saved prompts: each agent's system prompt and tools, read one time and saved. A new session starts "
              "from it and reads only its own messages.",
    "sessions": "Saved sessions: one session at the end of a turn. When the session continues and the server does not "
                "hold it, CARL restores it before its next request.",
    "share": "Shared storage: CARL stores a saved session as its changes to its saved prompt. This uses less disk."}
MOVE_HELP = {
    "off": "leave in place (the default): for a model whose chat template CARL does not know (not Qwen, not Gemma 4), "
           "the parts of the prompt that change per project (the folder, the date, AGENTS.md) stay in the system "
           "prompt. The model reads them as standing instructions, but each new project reads the whole prompt again. "
           "Qwen models get them as system text after the shared part; Gemma 4 models keep them in place.",
    "auto": "move to your message: for a model whose chat template CARL does not know, the parts of the prompt that "
            "change per project go to the start of your first message. Then one saved prompt serves every project. "
            "Do not use it with a template that also uses a sliding-window cache: CARL cannot restore its prompts."}
MOVE_TEXT = (("off", "leave in place"), ("auto", "move to your message"))


def save_text(conf: CacheConfig) -> Tuple[Tuple[str, str], ...]:
    return (("auto", f"auto (after {duration(conf.auto_s)})"), ("turn", "every turn"), ("switch", "when it leaves"),
            ("stop", "before a stop"))


SWA_TEXT = (("auto", "auto (full when it fits)"), ("full", "full cache"), ("window", "window cache"))


def caching_panel(ui: UIState, conf: CacheConfig, files: Sequence[CacheFile], folder: str, cols: int,
                  home: str, api: str = "") -> List[Row]:
    """The settings of the disk cache, what it holds, and Clear."""
    w = cols - 2
    use = used(files)
    n = len(files)
    ui.cache_row = max(0, min(ui.cache_row, len(CACHE_ROWS) - 1))
    key = CACHE_ROWS[ui.cache_row]

    def main(mw: int) -> List[CardLine]:
        L: List[CardLine] = [f"{DIM}What CARL saves on the disk for OpenCode and Pi, so a session continues without a "
                             f"long read.{R}", ""]
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
            L += choice_line(ROW_LABELS[k], opts, cur, mw, 18, sel=k == key)
        L += ["", heading("On the disk", mw),
              lv("used", f"{bar(use / conf.limit if conf.limit else 0, 18)} {gb_text(use)} of {conf.disk_gb} GB · "
                         f"{plural(n, 'file')}", 11),
              *cwrap(lv("folder", f"{home_short(folder, home)}{DIM} (llama.cpp saves here"
                                  f"{': --slot-save-path' if ui.full else ''}){R}", 11), mw, " " * 11)]
        packed = [f for f in files if f.packed]
        if packed:
            L.append(lv("shared", f"{plural(len(packed), 'saved session')} stored as changes: they save "
                                  f"{gb_text(shared_saving(files))} of disk", 11))
        L.append(f"Other computers: {GRN}they use these settings (the dashboard API, {api}).{R}" if api else
                 f"Other computers: {YEL}the dashboard API is not running, so they do not use these settings now.{R}")
        L += ["", f"  {DIM}{'kind':<14}{'model':<28}{'agent or session':<{max(mw - 70, 16)}}{'size':>9}  age{R}"]
        aw = max(mw - 70, 16)
        for f in sorted(files, key=lambda f: -f.mtime)[:12]:
            what = "saved prompt" if f.kind == PROMPT else "saved session"
            model, _, who = describe(f.name).partition(" · ")
            shared = f" {DIM}(shared){R}" if f.packed and ui.full else ""
            L.append(f"  {DIM}{what:<14}{R}{fit(model, 27):<27} {fit(who, aw - 1):<{aw - 1}} {file_size(f.bytes):>9}  "
                     f"{DIM}{duration(time.time() - f.mtime)} ago{R}{shared}")
        if n > 12:
            L.append(f"  {DIM}… and {n - 12} more{R}")
        if not files:
            L.append(f"  {DIM}Empty: OpenCode and Pi save here while they work.{R}")
        return [*L, "", *button_rows("", [("Clear the disk cache (c)", "cache:clear")], mw)]

    about = SAVE_HELP[conf.save].replace("AUTO", duration(conf.auto_s)) if key in ("save", "auto") else \
        SWA_HELP[conf.swa] if key == "swa" else MOVE_HELP[conf.move] if key == "move" else ROW_HELP[key]
    sections: List[Section] = [
        (f"About: {ROW_LABELS[key]}", [about]),
        ("How it works", ["OpenCode and Pi save their prompts and sessions through the server. Then the server does not "
                          "read everything again after a restart, a model switch or many other sessions. A saved file "
                          "is for one model file and one llama.cpp build. The sessions of another model wait for that "
                          "model (router mode loads it first). To turn the disk cache off on one computer, run its "
                          "installer with NO_CACHE=1."]),
        ("Experimental", [f"{YEL}The disk cache is new. If you see a problem, set Saved sessions or Saved prompts to "
                          f"off here.{R}"]),
        ("RAM cache", ["The server also keeps sessions in RAM while it runs. Set its size in the RAM cache row of the "
                       "Server panel."])]
    L = with_side(main, "Press ↑↓ to select a row and ← → to change it. CARL saves each change at once.", sections,
                  w - 4, main_w=104)
    ui.keys = [("↑↓", "row"), ("← →", "change"), ("c", "clear"), ("[ ]", "panels")]
    ui.keys_more = ["You can also click a choice. Clear asks before it removes files."]
    return indent(draw_card("caching", "CACHING", f"{DIM}disk cache: {gb_text(use)} of {conf.disk_gb} GB · "
                                                  f"experimental{R}", L, w, 1))

