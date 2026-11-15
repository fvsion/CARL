"""The sections that have a level, in one table.

A section is a block of a screen whose level is saved and cycled (0 collapsed, 1 simple, 2 full; a two-level section
is collapsed or open). The table lists every section of every screen and panel, so ui.levels, the saved levels and
THREE_LEVELS do not depend on which module was imported first (before W5.1 each panel registered its names when it
was imported). A new section is added here, next to its screen's.
"""
from __future__ import annotations

from typing import FrozenSet, List

from ..ports.plugin import SECTION_PREFIX

LEVEL_NAMES: List[str] = [
    # the Overview tab, the Settings tab's lists and the Requests and Log tabs
    "slots", "speed", "memory", "thismac", "connect", "health", "model", "requests", "log",
    "server", "modelinfo", "srv", "srvmem", "srvfit", "srvabout", "srvlist", "mlist", "msel", "reqtab", "logtab",
    # the Settings panels: Agents, Auto fit, Caching, Router, Auto-tune
    "agents",
    "fit", "fitrank", "fittip", "fithow", "fitmac", "fitgoal", "fitscope", "fitrankhelp", "fitother",
    "cachedisk", "caching", "cachedid", "cachetip", "cacheabout", "cachehow", "cacheexp", "cacheram",
    "router", "rtsettings", "rtmodels", "rtclients", "rtswitches", "rtcost", "rttip", "rtwho", "rtload",
    "tune", "tunesteps", "tunerun", "tuneall", "tunetip", "tunemodel", "tunelen",
    # the Connect tab
    "csetup", "clients", "cpreview", "cinstall", "cpackage", "chow", "clhow",
]

THREE_LEVELS: FrozenSet[str] = frozenset((
    "slots", "speed", "memory", "thismac", "connect", "health", "model", "modelinfo", "srv", "srvmem", "mlist", "msel",
    "requests", "log", "reqtab", "logtab",
    "agents", "fit", "fitrank", "caching", "cachedisk", "router", "rtsettings", "tune", "tunesteps",
    "csetup", "clients",
))

NO_COLLAPSE = frozenset(("reqtab", "logtab"))      # a tab with one section: simple and full only


def is_plugin_section(name: str) -> bool:
    """A section of a plugin (WP): its card on the Live tab, or its panel in Settings. The table above lists none: a
    plugin's sections are named by ports/plugin.py section_name when they are drawn, start at the simple level, and
    have two levels (open, collapsed). Their saved levels are kept whether the plugin is there or not."""
    return name.startswith(SECTION_PREFIX)
