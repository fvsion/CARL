"""A plugin's card (WP).
Pure: they render a ServerData snapshot and a View."""
from __future__ import annotations

from typing import List

from ....ports.plugin import CardView, InfoLine
from ....ui.fmt import (DIM, GRN, R, RED, YEL, Card, CardLine, aligned, button_rows, fit, row)
from ...act import Verb, act as make

TONES = {"ok": GRN, "warn": YEL, "bad": RED, "dim": DIM}      # InfoLine.tone -> the colour the dashboard gives it


def info_text(ln: InfoLine) -> str:
    """A reading's text in its tone's colour, then its note, dim."""
    colour = TONES.get(ln.tone, "")
    text = f"{colour}{ln.text}{R}" if colour else ln.text
    return text + (f"{DIM}{ln.note}{R}" if ln.note else "")


def card_plugin(c: CardView, w: int) -> Card:
    """A plugin's card (WP): its readings, one row each (a label and a text), then its buttons. The plugin gives data;
    the colours, the widths and the wrapping are the dashboard's."""
    L: List[CardLine] = [row(ln.label, info_text(ln)) if ln.label else info_text(ln) for ln in c.lines]
    if c.actions:
        if L:
            L.append("")
        L += button_rows("", [(f"{a.label} ({a.key})" if a.key else a.label, make(Verb.PLUGACT, f"{c.plugin}:{c.id}:{a.id}"))
                              for a in c.actions], w - 4)
    return Card(c.title.upper(), c.summary, L)



def card_requests_plugin(c: CardView, w: int) -> Card:
    """A plugin's card on the Requests tab (SLOT_REQUESTS): as card_plugin, with the labels in one column and one line a
    reading (a long one is cut at w, the text's width: the card is as wide as the table), and its summary dim."""
    card = card_plugin(c, w + 4)
    labelled = [bool(ln.label) for ln in c.lines]               # aligned() without a width keeps one line a reading
    lines: List[CardLine] = [fit(x, w) if i < len(labelled) and labelled[i] and isinstance(x, str) else x
                             for i, x in enumerate(aligned(card.lines))]          # (draw_card cuts the plain lines)
    return Card(card.title, f"{DIM}{c.summary}{R}" if c.summary else "", lines)
