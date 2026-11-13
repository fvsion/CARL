"""The text of `carl card NAME`: a model's card (domain/catalog/cards.py card_rows), wrapped to the width."""
from __future__ import annotations

from typing import List

from ...domain.catalog import cards
from ...domain.shared.platform_words import PlatformWords
from ...domain.shared.types import ModelInfo
from ...ui.layout import columns, wrap


def card_lines(m: ModelInfo, words: PlatformWords, w: int) -> List[str]:
    """A model's card for the command line, wrapped to w columns."""
    head, rows, hints = cards.card_rows(m, words)
    out = wrap(head, w) + columns(rows, w, max_term=27)
    for h in hints:
        out += [""] + wrap(h, w)
    return out
