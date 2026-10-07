"""Split text into words and sentences."""
from __future__ import annotations

import re
from typing import List

_WORD = re.compile(r"[A-Za-z0-9']+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def words(text: str) -> List[str]:
    """The words of the text, in lower case, without punctuation."""
    return [w.lower() for w in _WORD.findall(text)]


def sentences(text: str) -> List[str]:
    """The sentences of the text (split after . ! or ?), without empty ones."""
    return [s.strip() for s in _SENTENCE_END.split(text.strip()) if s.strip()]
