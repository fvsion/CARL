"""The units every screen shows (reference/glossary.md, section 9): one formatter per kind of number.

- Files, downloads and disk: GB / MB (1000-based), as Hugging Face and Finder show them.
- Memory (RAM, GPU, context memory, memory needed, kept free): GiB / MiB (1024-based).
- Tokens: K = 1024 (96K), one decimal when not a whole K.
- Speed: tok/s. Time: 1.2 s, 42 s, 3 min, 1 h 12 min.
Pure; None shows as an en dash.
"""
from __future__ import annotations

from typing import Optional

DASH = "–"
GB, MB = 1000 ** 3, 1000 ** 2
GIB, MIB = 1024 ** 3, 1024 ** 2


def file_size(n: Optional[float]) -> str:
    """A file, download or disk size: 14.1 GB, 280 MB, 12 kB."""
    if n is None:
        return DASH
    if abs(n) >= GB:
        return f"{n / GB:.1f} GB"
    if abs(n) >= MB:
        return f"{n / MB:.0f} MB"
    return f"{n / 1000:.0f} kB"


def memory(n: Optional[float]) -> str:
    """A memory size: 13.1 GiB, 512 MiB, 64 KiB."""
    if n is None:
        return DASH
    if abs(n) >= GIB:
        return f"{n / GIB:.1f} GiB"
    if abs(n) >= MIB:
        return f"{n / MIB:.0f} MiB"
    return f"{n / 1024:.0f} KiB"


def memory_pair(used: float, total: float) -> str:
    """Part of a memory total, one unit: 15.3 of 25.0 GiB."""
    return f"{used / GIB:.1f} of {total / GIB:.1f} GiB"


def tokens(n: Optional[float]) -> str:
    """A token count with K = 1024: 96K, 52.7K, 950."""
    if n is None:
        return DASH
    v = int(n)
    if abs(v) < 1024:
        return str(v)
    if v % 1024 == 0:
        return f"{v // 1024}K"
    return f"{v / 1024:.1f}K"


def speed(v: Optional[float]) -> str:
    """Tokens per second: 534 tok/s, 43.1 tok/s."""
    if v is None:
        return DASH
    return f"{v:.0f} tok/s" if v >= 100 else f"{v:.1f} tok/s"


def duration(s: Optional[float]) -> str:
    """Seconds as 1.2 s / 42 s / 3 min / 1 h 12 min."""
    if s is None:
        return DASH
    if s < 10:
        return f"{s:.1f} s"
    if s < 60:
        return f"{s:.0f} s"
    m = int(s // 60)
    if m < 60:
        return f"{m} min"
    return f"{m // 60} h {m % 60} min" if m % 60 else f"{m // 60} h"


def percent(frac: Optional[float]) -> str:
    """A fraction as a whole percent: 79%."""
    return DASH if frac is None else f"{frac * 100:.0f}%"
