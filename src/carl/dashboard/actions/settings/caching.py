"""The Caching panel: the choices of the disk cache (saved to config.json at once), Clear and the time of WHAT THE
CACHE DID."""
from __future__ import annotations

from typing import Tuple

from ....composition.dashboard import host as dashboard_host
from ....domain.config.settings import CACHE_KEYS
from ....ui.fmt import R, RED, home_short, row
from ....ui.words import plural
from ...act import Action, CacheOp, cache
from ...keys import DOWN, LEFTKEY, PGDN, PGUP, RIGHT, UP
from ...render.settings.caching import AUTO_CHOICES, CACHE_ROWS, DISK_CHOICES
from ...state import Confirm
from .context import Context

CACHE_NAMES = {"disk": "disk limit", "prefix": "saved prompts", "sessions": "saved sessions", "save": "when CARL saves",
               "auto": "save after", "share": "shared storage", "swa": "Gemma models (sliding window)",
               "move": "other templates"}
CACHE_SETTING = {"disk": "disk_gb", "auto": "auto_s"}     # a Caching row -> its key of config.json "cache" (else its own)
PREV = "prev"                                             # cache:KEY:prev, the choice before the current one


class CacheActions:
    """Acts on the Caching panel."""

    def __init__(self, c: Context) -> None:
        self.c = c
        self._windows: Tuple[str, ...] = ()     # WHAT THE CACHE DID's times (the dashboard's CacheWatch)

    def windows(self) -> Tuple[str, ...]:
        if not self._windows:
            self._windows = tuple(dashboard_host().cache().windows)
        return self._windows

    def act(self, a: Action) -> None:
        """KEY (the next choice), KEY:VALUE (that one) or KEY:prev (the one before) for disk, prefix, sessions, share,
        save, auto and swa; saved to config.json "cache" at once. clear asks, clearyes removes every saved file (the
        jobs' disk cache)."""
        c = self.c
        ui, jobs = c.ui, c.jobs
        folder = jobs.paths.slots
        arg = a.arg
        if arg == CacheOp.CLEAR:
            files = jobs.cache_files()
            if not files:
                ui.toast("The disk cache is empty.", 5)
                return
            n = sum(1 for f in files if f.name.startswith("carl-prefix+"))     # type: ignore[attr-defined]  (a prompt)
            used = sum(f.bytes for f in files)                                 # type: ignore[attr-defined]
            ui.confirm2 = Confirm("CLEAR THE DISK CACHE?", [
                row("saved prompts", str(n)), row("saved sessions", str(len(files) - n)),
                row("size", f"{used / 10 ** 9:.1f} GB"), row("folder", home_short(folder, c.home)), "",
                "The server keeps what it holds now. After the next start, it reads each prompt again when a client "
                "uses it first."], cache(CacheOp.CLEARYES), yes_label="Clear")
            return
        if arg == CacheOp.CLEARYES:
            ui.confirm2 = None
            jobs.clear_cache()
            ui.toast("The disk cache is empty now.", 6)
            return
        conf = jobs.cache_conf(fresh=True)
        key, _, value = arg.partition(":")
        step = -1 if value == PREV else 1
        new: object
        if key == "disk":
            gbs = sorted({*DISK_CHOICES, conf.disk_gb})
            new = int(value) if value.isdigit() else gbs[(gbs.index(conf.disk_gb) + step) % len(gbs)]
            text = f"{new} GB"
        elif key in ("prefix", "sessions", "share"):
            cur = {"prefix": conf.prefix, "sessions": conf.sessions, "share": conf.share}[key]
            new = value == "on" if value in ("on", "off") else not cur
            text = "on" if new else "off"
        elif key == "auto":
            autos = sorted({*AUTO_CHOICES, conf.auto_s})
            new = int(value) if value.isdigit() else autos[(autos.index(conf.auto_s) + step) % len(autos)]
            text = f"after {new} s of reading"
        elif key in ("save", "swa", "move"):
            opts = CACHE_KEYS[key].choices                  # the schema's
            mode = {"save": conf.save, "swa": conf.swa, "move": conf.move}[key]
            new = value if value in opts else opts[(opts.index(mode) + step) % len(opts)]
            text = (str(new) + (". It applies at the next start" if key == "swa" else "") if key != "move" else
                    ("move to your message" if new == "auto" else "leave in place") +
                    ". OpenCode and Pi use it from their next request")
        else:
            return
        try:
            c.svc.set_cache(CACHE_SETTING.get(key, key), new)
        except Exception as e:      # config.json unreadable or not writable: say so
            ui.toast(f"{RED}CARL cannot save config.json: {e}{R}", 10)
            return
        jobs.cache_conf(fresh=True)
        if key == "disk":
            before = len(jobs.cache_files())
            jobs.trim_cache()
            gone = before - len(jobs.cache_files())
            text += f". CARL removed the {plural(gone, 'oldest file')} to stay below it" if gone else ""
        ui.toast(f"Saved. {CACHE_NAMES[key].capitalize()}: {text}.", 8)

    def key(self, k: str) -> bool:
        """The Caching panel: ↑↓ a row, ← → its value (saved at once), c clear (asks first), t the time of WHAT THE
        CACHE DID, PgUp PgDn scroll the page."""
        c = self.c
        ui = c.ui
        if k in (UP, DOWN):
            ui.cache_row = (ui.cache_row + (1 if k == DOWN else -1)) % len(CACHE_ROWS)
            ui.page_scroll = 0                       # the rows are at the top: keep the selected one in view
        elif k in (PGUP, PGDN):
            ui.page_scroll = max(0, ui.page_scroll + (-10 if k == PGUP else 10))
        elif k in (RIGHT, LEFTKEY):
            key = CACHE_ROWS[min(ui.cache_row, len(CACHE_ROWS) - 1)]
            c.act(cache(key, "" if k == RIGHT else PREV))
        elif k == "c":
            c.act(cache(CacheOp.CLEAR))
        elif k == "t":                               # WHAT THE CACHE DID: last hour, today, since the start
            ui.cache_window = (ui.cache_window + 1) % len(self.windows())
        else:
            return False
        return True
