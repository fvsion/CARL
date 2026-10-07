"""Counts and averages."""
from __future__ import annotations

from collections import Counter
from typing import List, Tuple

from textstats.tokenize import words


def word_count(text: str) -> int:
    return len(words(text))


def top_words(text: str, n: int = 10) -> List[Tuple[str, int]]:
    """The n most common words with their counts; ties in alphabetical order."""
    counts = Counter(words(text))
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


def mean_word_length(text: str) -> float:
    """The mean length of the words, in characters."""
    ws = words(text)
    return len(text) / len(ws)


def longest_word(text: str) -> str:
    """The longest word (the first one when several have the same length); "" for no words."""
    ws = words(text)
    return min(ws, key=len) if ws else ""
