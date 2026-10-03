"""The disk cache's budget (Settings > Caching): ~/.config/carl/slots holds OpenCode's pre-read
prompts (prefix.py: about 120 MB each on the 35B) and the saved conversations (sessions.py:
about 13 KB per token on the 35B, so a 74K-token session is about 1 GB). Together they stay
within cache.disk_gb (config.json, default 5 GB): after every save the oldest conversations
go first, then the oldest prompts; the file just saved stays unless it alone is over the limit.

The settings (config.json "cache", carl_core.domain.settings.CACHE_KEYS): disk_gb, prefix
(pre-read OpenCode's prompt) and sessions (save the conversations). Pure, except listing()
and remove().
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Sequence, Tuple

from .model import JSONDict, jdict

DEFAULT_GB = 5
PROMPT, CONVERSATION = "prompt", "conversation"


@dataclass(frozen=True)
class CacheConfig:
    disk_gb: int = DEFAULT_GB
    prefix: bool = True
    sessions: bool = True

    @property
    def limit(self) -> int:
        return self.disk_gb * 10 ** 9


def config_of(cfg: JSONDict) -> CacheConfig:
    """The cache settings from a loaded config.json (defaults for what isn't set)."""
    sec = jdict(cfg.get("cache"))
    gb = sec.get("disk_gb")
    return CacheConfig(gb if isinstance(gb, int) and not isinstance(gb, bool) and gb > 0 else DEFAULT_GB,
                       sec.get("prefix") is not False, sec.get("sessions") is not False)


@dataclass(frozen=True)
class CacheFile:
    name: str
    bytes: int
    mtime: float

    @property
    def kind(self) -> str:
        return PROMPT if self.name.startswith("carl-prefix-") else CONVERSATION


def listing(folder: str) -> List[CacheFile]:
    """The saved states in the folder (CARL's .bin files), oldest first."""
    out = []
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    for n in names:
        if n.startswith(("carl-prefix-", "carl-session-")) and n.endswith(".bin"):
            try:
                st = os.stat(os.path.join(folder, n))
            except OSError:
                continue
            out.append(CacheFile(n, st.st_size, st.st_mtime))
    return sorted(out, key=lambda f: f.mtime)


def used(files: Sequence[CacheFile]) -> int:
    return sum(f.bytes for f in files)


def over_budget(files: Sequence[CacheFile], limit: int, keep: str = "") -> List[str]:
    """The files to remove so the rest fit `limit` bytes: the oldest conversations first, then
    the oldest prompts; `keep` last (only when it alone is over the limit)."""
    total = used(files)
    out = []
    for f in sorted((f for f in files if f.name != keep), key=_rank):
        if total <= limit:
            break
        out.append(f.name)
        total -= f.bytes
    if total > limit and any(f.name == keep for f in files):
        out.append(keep)
    return out


def _rank(f: CacheFile) -> Tuple[int, float]:
    return (0 if f.kind == CONVERSATION else 1, f.mtime)


def remove(folder: str, names: Sequence[str]) -> None:
    for n in names:
        try:
            os.remove(os.path.join(folder, n))
        except OSError:
            pass


def gb(n: int) -> str:
    """Bytes as GB (the unit of the limit)."""
    return f"{n / 10 ** 9:.1f} GB"
