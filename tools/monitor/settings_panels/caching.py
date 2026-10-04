"""The Caching panel: the disk cache of prompt states (diskcache.py), its limit, the prompt and
conversation switches, when CARL saves, what it holds, and Clear. Pure, except the clock (the
files' ages)."""
from __future__ import annotations

import time
from typing import List, Sequence

from ..diskcache import PROMPT, CacheConfig, CacheFile, describe, gb as gb_text, shared_saving, used
from ..fmt import (B, DIM, R, YEL, CardLine, Row, bar, button_rows, draw_card, dur, fit, heading, home_short, indent,
                   lv, size, with_side)
from ..state import UIState
from .common import choice_line

DISK_CHOICES = (2, 5, 10, 20, 50)                      # the Caching panel's disk limits (GB)
AUTO_CHOICES = (30, 120, 300, 600)                    # save = auto: seconds of unsaved reading (default 120)
SAVE_TEXT = (("auto", "auto"), ("turn", "every turn"), ("switch", "on a switch"), ("stop", "before a stop"))
SAVE_HELP = (
    ("auto", "auto: CARL saves a session when its unsaved part would take AUTO to read again. The Auto after row "
             "sets AUTO, and the measured read speed of this model gives the time. CARL also saves before a session "
             "leaves the server: a different session needs its slot, a router switch, or a stop. This mode writes "
             "little. After a crash, each session loses at most AUTO of read time."),
    ("turn", "every turn: CARL saves after each reply. A crash loses nothing. This mode writes the most to the disk: "
             "up to about 1 GB per turn for a long session on the 35B."),
    ("switch", "on a switch: CARL saves before a session leaves the server: a different session needs its slot, a "
               "router switch, or a stop or restart from the dashboard. A crash or a stop outside the dashboard "
               "(Ctrl-C) loses the unsaved part."),
    ("stop", "before a stop: CARL saves only before the dashboard stops or restarts the server, and before a router "
             "switch. The stop loses the sessions that moved to the RAM cache before it."),
)
SWA_TEXT = (("auto", "auto"), ("full", "full cache"), ("window", "window only"))
SWA_HELP = (
    ("auto", "auto: models with sliding-window layers (Gemma) keep all layers at full length when that fits this "
             "Mac. Then CARL can restore saved states. If not, the model keeps only the window: it uses less memory, "
             "but CARL cannot restore states. The change applies at the next start."),
    ("full", "full cache: CARL can restore saved states, but the model uses more memory (Gemma 4 E4B at 2 × 96K: "
             "+2.3 GB). The change applies at the next start."),
    ("window", "window only: the model uses the least memory. CARL cannot restore its saved states, so llama.cpp "
               "reads them again. The change applies at the next start."),
)


def caching_panel(ui: UIState, conf: CacheConfig, files: Sequence[CacheFile], folder: str, cols: int,
                  home: str) -> List[Row]:
    """The disk cache (diskcache.py): its limit, the prompt and conversation switches, what it
    holds, and Clear. Every change is saved at once; none needs a restart."""
    w = cols - 2
    use = used(files)
    n = len(files)

    def main(mw: int) -> List[CardLine]:
        gbs = sorted({*DISK_CHOICES, conf.disk_gb})
        L: List[CardLine] = choice_line("Disk limit", [(str(g), f"{g} GB", f"cache:disk:{g}") for g in gbs],
                                        str(conf.disk_gb), mw)
        L += choice_line("Prompts", [("on", "pre-read each agent's", "cache:prefix:on"),
                                     ("off", "off", "cache:prefix:off")], "on" if conf.prefix else "off", mw)
        L += choice_line("Sessions", [("on", "save conversations", "cache:sessions:on"),
                                      ("off", "off", "cache:sessions:off")], "on" if conf.sessions else "off", mw)
        L += choice_line("Save", [(k, t, f"cache:save:{k}") for k, t in SAVE_TEXT], conf.save, mw)
        autos = sorted({*AUTO_CHOICES, conf.auto_s})
        L += choice_line("Auto after", [(str(a), dur(a), f"cache:auto:{a}") for a in autos], str(conf.auto_s), mw)
        L += choice_line("Shared", [("on", "store conversations against their prompt", "cache:share:on"),
                                    ("off", "off", "cache:share:off")], "on" if conf.share else "off", mw)
        L += choice_line("SWA models", [(k, t, f"cache:swa:{k}") for k, t in SWA_TEXT], conf.swa, mw)
        L += ["", heading("On disk", mw),
              lv("used", f"{bar(use / conf.limit, 18)} {gb_text(use)} of {conf.disk_gb} GB · "
                         f"{n} file{'' if n == 1 else 's'}", 11),
              lv("folder", f"{home_short(folder, home)}{DIM} (the server's --slot-save-path){R}", 11)]
        packed = [f for f in files if f.packed]
        if packed:
            L.append(lv("shared", f"{len(packed)} conversation(s) kept as patches against their prompt: "
                                  f"{gb_text(shared_saving(files))} less on the disk", 11))
        nw = max(mw - 42, 12)
        for f in sorted(files, key=lambda f: -f.mtime)[:12]:
            what = "prompt      " if f.kind == PROMPT else "patch       " if f.packed else "conversation"
            L.append(f"  {DIM}{what}{R}  {fit(describe(f.name), nw):<{nw}} {size(f.bytes):>7}  "
                     f"{DIM}{dur(time.time() - f.mtime)} ago{R}")
        if n > 12:
            L.append(f"  {DIM}… and {n - 12} more{R}")
        if not files:
            L.append(f"  {DIM}empty: OpenCode and Pi save their states here during their work{R}")
        return [*L, "", *button_rows("", [("Clear the disk cache (c)", "cache:clear")], mw)]

    L = with_side(main, "Click an option, or press its key (? shows the keys). CARL saves each change immediately.", [
        ("Experimental", [f"{YEL}{B}EXPERIMENTAL{R}{YEL}: The disk cache is new. It works with the model that runs. "
                          f"Each saved state is for one model file and one llama.cpp build. The sessions of a "
                          f"different model wait for that model (router mode loads it first). If you see a problem, "
                          f"set sessions or prompts to off here.{R}"]),
        ("How it works", [f"OpenCode and Pi save prompt states through the server. Then the server does not read "
                          f"everything again after a restart, a model switch or many other sessions. "
                          f"{B}prompts{R} = the system prompt and tools of each agent. The server reads them once, "
                          f"and a new session reads only its own messages. {B}conversations{R} = each session. "
                          f"CARL saves it as the Save row tells. If the server does not hold it, CARL puts it back "
                          f"before its next request. At the disk limit, CARL removes the oldest conversations first, "
                          f"then the oldest prompts. These settings apply to the clients on this Mac. For a VM, run "
                          f"NO_CACHE=1 ./install.sh there."]),
        (f"Save: {conf.save}", [dict(SAVE_HELP)[conf.save].replace("AUTO", dur(conf.auto_s))]),
        (f"SWA models: {conf.swa}", [dict(SWA_HELP)[conf.swa]]),
        ("RAM cache", ["The llama.cpp prompt cache in RAM, while the server runs. Set it in the RAM cache row of the "
                       "Server panel."])],
        w - 4, main_w=104)
    ui.keys = [("d", "disk limit"), ("p", "prompts"), ("s", "sessions"), ("o", "when to save"), ("t", "auto after"),
               ("h", "shared"), ("w", "SWA models"), ("c", "clear"), ("[ ]", "panels")]
    ui.keys_more = ["Each key selects the next choice of its row. A click selects one choice. Clear asks before "
                    "it removes files."]
    return indent(draw_card("caching", "CACHING (EXPERIMENTAL)", f"{DIM}disk cache: {gb_text(use)} of {conf.disk_gb} GB{R}",
                            L, w, 2))
