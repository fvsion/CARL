"""The CONNECT card: the address, the key, the copy buttons.
Pure: they render a ServerData snapshot and a View."""
from __future__ import annotations

import collections
from typing import List

from ....domain.server.snapshot import ServerData
from ....ui.fmt import (B, DIM, MAG, R, RED, Card, CardLine, Ln, button_rows, home_short, row)
from ....ui.words import computer
from .view import View
from ...act import Verb, act as make

def reach_of(host: str) -> str:
    return f"{computer()} only" if host in ("127.0.0.1", "::1", "localhost") else f"{computer()} and other computers"


def card_connect(v: View, d: ServerData, w: int = 66) -> Card:
    """The address, the key (hidden unless shown), the connections, OpenCode and Pi here, the disk cache; at
    full the model name, the hosts, the key file and the copy buttons."""
    shown = v.key if v.key_shown else (("•" * 8 + v.key[-4:]) if v.key else f"{RED}no key file{R}")
    here = computer()
    hosts = collections.Counter(here if h in ("127.0.0.1", "::1") else h for h, _ in d.conns)
    L: List[CardLine] = [row("address", f"{B}{v.base}/v1{R}"),
                         Ln(row("key", f"{MAG}{shown}{R}   ") + f"{DIM}{'hide' if v.key_shown else 'show'} (k){R}",
                            "key")]
    other = [h for h in hosts if h != here]
    L.append(row("connections", str(len(d.conns)) + (f"   {DIM}from{R} {', '.join(hosts)}" if other else "")))
    if v.here:
        L.append(row("OpenCode, Pi", v.here))
    used, _, saved = (v.cache or "").partition("   ")            # "2.4 of 10 GB   12 prompts, 5 sessions"
    L.append(row("disk cache", used or f"{DIM}empty{R}"))
    if v.full:
        L.append("")
        if saved:
            L.append(row("saved", saved))
        if d.alias:
            L.append(row("model name", d.alias))
        if hosts and not other:
            L.append(row("from", ", ".join(hosts)))
        L.append(row("key file", home_short(v.key_file, v.home)))
        L += button_rows(f"{DIM}Copy{R}  ", [("OpenCode (o)", make(Verb.OPENCODE)), ("Pi (p)", make(Verb.PI)), ("curl (c)", make(Verb.CURL))], w - 4)
    return Card("CONNECT", reach_of(v.host), L)
