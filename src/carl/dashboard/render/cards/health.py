"""The HEALTH card: errors and warnings, the sleep guard, the llama.cpp version, the plugins' checks.
Pure: they render a ServerData snapshot and a View."""
from __future__ import annotations

from typing import List

from ....domain.llama.version import health_rows
from ....domain.server.snapshot import ServerData
from ....ui.fmt import (B, DIM, GRN, R, RED, YEL, Card, CardLine, row)
from ....ui.words import (plural, the_computer, the_computer_cap,
                          update_hint)
from .requests import log_line
from .view import View

def card_health(v: View, d: ServerData) -> Card:
    """Errors and warnings in the log (or none), whether the Mac stays awake, a warning when llama.cpp is older than
    the tested version (domain/llama/version.py; at full also the same or a newer version); at full the counts,
    the /health time, the last errors and the sleep events."""
    c = v.log.counts
    broken = c["oom"] or c["compute"]
    if broken:
        first = f"{RED}{B}✗ The GPU failed. Restart the server (Settings > Server, a).{R}"
    elif c["E"] or c["W"]:
        first = (f"{YEL}⚠ {plural(c['E'], 'error')}, {plural(c['W'], 'warning')}{R}   {DIM}tab 4{R}")
    else:
        first = f"{GRN}✓{R} No errors."
    L: List[CardLine] = [row("log", first),
                         row("sleep", f"{the_computer_cap()} stays awake." if d.awake
                             else f"{YEL}{the_computer_cap()} can sleep.{R}")]
    if d.log_filter_stopped:                   # Phase 23.4.4 item 12: the server writes its log through the filter
        L.insert(1, row("log filter", f"{YEL}⚠ CARL's log filter stopped: the log is not written. Restart the server "
                                      f"(Settings > Server, a).{R}"))
    old_llama = v.llama is not None and v.llama.state == "older"
    for label, text, warn in health_rows(v.llama, v.full, update_hint()) if v.llama else ():
        L.append(row(label, f"{YEL}⚠ {text}{R}" if warn else text))
    for pr in v.plugin_health:                 # a plugin's check, or why a plugin or one of its hooks failed (WP)
        L.append(row(pr.label, f"{GRN}✓{R} {pr.text}".rstrip() if pr.ok else f"{YEL}⚠ {pr.text}{R}"))
    if v.full:
        L.append("")
        L.append(row("errors", str(c["E"])))
        L.append(row("warnings", str(c["W"])))
        L.append(row("routine notices", str(c["notice"])))
        L.append(row("GPU errors", f"{c['oom']} out of memory, {c['compute']} compute"))
        if d.health_ms is not None:
            L.append(row("health check", f"{d.health_ms:.0f} ms"))
        errs = list(v.log.errors)[-3:]
        if errs:
            L.append(f"{B}Last errors{R}")
            L += [f"{RED}{log_line(v.log, e)}{R}" for e in errs]
        if v.slow.sleep_events:
            sleeps = [e for e in v.slow.sleep_events if e[1] == "Sleep"]
            L.append(row("sleeps", f"{len(sleeps)} since {the_computer()} started"))
            L += [row(t, f"{kind}: {why}") for t, kind, why in v.slow.sleep_events[-3:]]
    plugin_bad = sum(1 for pr in v.plugin_health if not pr.ok)
    summary = (f"{RED}GPU failed{R}" if broken else f"{YEL}{plural(c['E'] + c['W'], 'problem')}{R}" if c["E"] or c["W"]
               else f"{YEL}old llama.cpp{R}" if old_llama
               else f"{YEL}{plural(plugin_bad, 'plugin problem')}{R}" if plugin_bad else "no errors")
    return Card("HEALTH", summary, L)
