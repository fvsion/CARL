"""This Mac's power from pmset: the battery (`pmset -g batt`), the thermal state (`pmset -g therm`), sleep and wake
(`pmset -g log`) and the power assertions (`pmset -g assertions`: caffeinate -w keeps the Mac awake for the server).
The parse_* functions are pure and take the commands' text output; system_stats.py runs the commands."""
from __future__ import annotations

import re
import time
from typing import List

from ...domain.server.snapshot import SleepEvent

BATT_ARGS = ["pmset", "-g", "batt"]
THERM_ARGS = ["pmset", "-g", "therm"]
LOG_ARGS = ["pmset", "-g", "log"]
ASSERTIONS_ARGS = ["pmset", "-g", "assertions"]


def parse_power(text: str) -> str:
    """"AC 100%" / "battery 54%" from `pmset -g batt`."""
    p = re.search(r"(\d+)%", text)
    return ("AC" if "AC Power" in text else "battery") + (f" {p.group(1)}%" if p else "")


def parse_thermal(text: str) -> str:
    """"nominal", or the warnings of `pmset -g therm` (60 characters at most)."""
    warn = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("Note:")]
    return "nominal" if not warn else "; ".join(warn)[:60]


_PMSET_EVENT = re.compile(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) [+-]\d{4} (Sleep|Wake|DarkWake)\s+(.*)")


def parse_sleep_events(text: str, since: float) -> List[SleepEvent]:
    """Sleep / wake events at or after since (epoch seconds) from `pmset -g log`."""
    events = []
    for line in text.splitlines():
        m = _PMSET_EVENT.match(line)
        if m and time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")) >= since:
            events.append((m.group(1)[11:], m.group(2), m.group(3).strip()[:50]))
    return events


def awake_for(text: str, pid: int) -> bool:
    """True if `pmset -g assertions` shows a power assertion (caffeinate -w) made on behalf of pid."""
    return re.search(rf"on behalf of Process ID {pid}(?!\d)", text) is not None
