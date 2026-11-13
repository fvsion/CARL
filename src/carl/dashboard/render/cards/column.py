"""The cards one under the other (a column of the Live tab) and the SERVER card of a stopped server.
Pure: they render a ServerData snapshot and a View."""
from __future__ import annotations

from dataclasses import replace
from typing import Callable, Dict, List

from ....domain.server.snapshot import ServerData
from ....ui.fmt import (R, RED, Card, CardLine, Ln, Row, aligned, cwrap,
                        draw_card, row)
from ...sections import THREE_LEVELS
from .connect import card_connect
from .health import card_health
from .memory import card_memory
from .model import card_model
from .plugin import card_plugin
from .slots import card_slots
from .speed import card_speed
from .thismac import card_thismac
from .view import View

CardFn = Callable[[View, ServerData], Card]
CARDS: Dict[str, CardFn] = {"health": card_health, "model": card_model}
SIZED = {"slots": card_slots, "memory": card_memory, "thismac": card_thismac, "connect": card_connect, "speed": card_speed}       # these take the card's width


def column(v: View, names: List[str], d: ServerData, w: int) -> List[Row]:
    """The named cards drawn one under the other, w columns wide, each at its own level (its title shows it)."""
    rows: List[Row] = []
    plug = {c.name: c for c in v.plugin_cards}
    for nm in names:
        lvl = v.level(nm)
        vv = replace(v, detail="full" if lvl == 2 else "simple")
        card = card_plugin(plug[nm], w) if nm in plug else SIZED[nm](vv, d, w) if nm in SIZED else CARDS[nm](vv, d)
        rows += draw_card(nm, card.title, card.summary, wrapped(aligned(card.lines, w - 4), w - 4), w, lvl,
                          3 if nm in THREE_LEVELS else 2, nm == v.selected)
    return rows


def wrapped(lines: List[CardLine], w: int) -> List[CardLine]:
    """Card text wrapped to w columns (never cut); clickable lines stay as they are."""
    return [x for ln in lines for x in ([ln] if isinstance(ln, Ln) else cwrap(ln, w, "  "))]


def card_stopped(v: View, d: ServerData) -> Card:
    """No server runs: the state, the next action, what a start uses (v.next_start, from the app)."""
    failed = any("start failed" in str(x) for x in v.next_start)
    L: List[CardLine] = [row("state", f"{RED}stopped{R}" + (". The last start failed." if failed else "")), *v.next_start]
    return Card("SERVER", f"{RED}stopped{R}", L)
