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


def ctx_label(v: object) -> str:
    """A context size in tokens as 96K (K = 1024); anything that is not a whole number as itself."""
    return tokens(int(str(v))) if str(v).isdigit() else str(v)


def speed(v: Optional[float]) -> str:
    """Tokens per second: 534 tok/s, 43.1 tok/s."""
    if v is None:
        return DASH
    return f"{v:.0f} tok/s" if v >= 100 else f"{v:.1f} tok/s"


def duration(s: Optional[float], secs: bool = False) -> str:
    """Seconds as 1.2 s / 42 s / 3 min / 1 h 12 min. secs: a setting's time, with its seconds under 10 min (90 s is
    1 min 30 s, not 1 min) and whole seconds under 10 s (W1.6)."""
    if s is None:
        return DASH
    if s < 10 and not (secs and s == int(s)):
        return f"{s:.1f} s"
    if s < 60:
        return f"{s:.0f} s"
    m = int(s // 60)
    if secs and m < 10 and int(s) % 60:
        return f"{m} min {int(s) % 60} s"
    if m < 60:
        return f"{m} min"
    return f"{m // 60} h {m % 60} min" if m % 60 else f"{m // 60} h"


def percent(frac: Optional[float]) -> str:
    """A fraction as a whole percent: 79%."""
    return DASH if frac is None else f"{frac * 100:.0f}%"


def etime_seconds(etime: Optional[str]) -> Optional[float]:
    """ps's elapsed time ([[dd-]hh:]mm:ss) in seconds; None when it can't be read."""
    if not etime:
        return None
    try:
        days, _, rest = etime.rpartition("-")
        parts = [int(x) for x in rest.split(":")]
    except ValueError:
        return None
    secs = 0
    for p in parts:
        secs = secs * 60 + p
    return secs + (int(days) * 86400 if days.isdigit() else 0)
