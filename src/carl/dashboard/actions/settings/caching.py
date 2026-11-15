"""The Caching panel: the choices of the disk cache (saved to config.json at once), Clear and the time of WHAT THE
CACHE DID."""
from __future__ import annotations

import re
from typing import Optional, Tuple

from ....composition.dashboard import host as dashboard_host
from ....domain.config import server_rows
from ....domain.config.settings import CACHE_KEYS
from ....domain.shared.units import duration
from ....ui.fmt import R, RED, home_short, row
from ....ui.words import plural
from ...act import Action, CacheOp, cache
from ...keys import BACKSPACE, DOWN, ENTER, ESC, LEFTKEY, PGDN, PGUP, RIGHT, UP
from ...render.settings.caching import CACHE_ROWS, NUMBER_ROWS, PREV
from ...settings_texts import range_words
from ...state import Confirm
from .context import Context

CACHE_NAMES = {"disk": "disk limit", "prefix": "saved prompts", "sessions": "saved sessions", "save": "when CARL saves",
               "auto": "save after", "share": "shared storage", "keep": "keep slots",
               "swa": "Gemma models (sliding window)", "move": "other templates"}
CACHE_SETTING = {**NUMBER_ROWS, "keep": "keep_slots"}     # a Caching row -> its key of config.json "cache" (else its own)
_TIME = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?")      # 1h, 2m, 1m30, 90s, 90


def typed_value(key: str, text: str) -> Tuple[Optional[int], str]:
    """A value typed into a number row of the Caching panel (B6): Save after takes 90, 90s, 2m, 1m30, 1m30s and 1:30
    (seconds), Disk limit a whole number of GB. (value, "") when it is in the schema's range, else (None, why)."""
    t = text.strip().lower().replace(" ", "")
    v: Optional[int] = None
    if key == "auto":
        if re.fullmatch(r"\d+:\d{1,2}", t):
            mins, secs = t.split(":")
            v = int(mins) * 60 + int(secs)
        else:
            m = _TIME.fullmatch(t)
            if t and m and any(m.groups()):
                h, mi, se = (int(g) if g else 0 for g in m.groups())
                v = h * 3600 + mi * 60 + se
    elif t.isdigit():
        v = int(t)
    if v is None:
        return None, f"That is not a {'time' if key == 'auto' else 'size in GB'}: {text.strip()}."
    spec = CACHE_KEYS[NUMBER_ROWS[key]]
    if (spec.min is not None and v < spec.min) or (spec.max is not None and v > spec.max):
        return None, (f"{text.strip()} is out of range for {CACHE_NAMES[key].capitalize()}: "
                      f"{range_words(NUMBER_ROWS[key]).split(':')[0].split(',')[0]}.")
    return v, ""


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
        keep, save, auto and swa; saved to config.json "cache" at once. clear asks, clearyes removes every saved file (the
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
        if key in NUMBER_ROWS:              # a number row (B6): a typed value, or the next step, stopping at the ends
            cur = conf.disk_gb if key == "disk" else conf.auto_s
            new = int(value) if value.isdigit() else \
                server_rows.step_number(NUMBER_ROWS[key], cur, step, full=ui.levels.get("caching", 1) == 2)
            if new == cur and not value.isdigit():
                return
            text = f"{new} GB" if key == "disk" else duration(int(str(new)), secs=True)
        elif key in ("prefix", "sessions", "share", "keep"):
            cur = {"prefix": conf.prefix, "sessions": conf.sessions, "share": conf.share, "keep": conf.keep_slots}[key]
            new = value == "on" if value in ("on", "off") else not cur
            text = "on" if new else "off"
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

    def edit_key(self, k: str) -> bool:
        """A value being typed into the selected number row (B6): digits (and s m h : for a time), Backspace, Enter
        saves it, Esc drops it."""
        ui = self.c.ui
        if ui.edit is None:
            return False
        key = CACHE_ROWS[min(ui.cache_row, len(CACHE_ROWS) - 1)]
        allowed = "0123456789smh:" if key == "auto" else "0123456789"
        if len(k) == 1 and k.lower() in allowed:
            ui.edit += k.lower()
        elif k in BACKSPACE and len(k) == 1:
            ui.edit = ui.edit[:-1]
        elif k in ENTER:
            text, ui.edit = ui.edit, None
            value, err = typed_value(key, text)
            if value is None:
                ui.toast(err, 5)
            else:
                self.c.act(cache(key, value))
        elif k == ESC:
            ui.edit = None
        return True

    def key(self, k: str) -> bool:
        """The Caching panel: ↑↓ a row, ← → its value (saved at once), Enter types a value on a number row (at the full
        level of CACHING; B6), c clear (asks first), t the time of WHAT THE CACHE DID, PgUp PgDn scroll the page."""
        c = self.c
        ui = c.ui
        if k in ENTER and CACHE_ROWS[min(ui.cache_row, len(CACHE_ROWS) - 1)] in NUMBER_ROWS:
            if ui.levels.get("caching", 1) == 2:
                ui.edit = ""
            else:
                ui.toast("To type a value, open the full level of CACHING (L). Here ← → step through the presets.", 6)
            return True
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
