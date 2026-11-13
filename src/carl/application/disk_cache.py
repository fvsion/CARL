"""The disk cache's use cases for the command line (`carl cache show|trim|clear`; the start's silent trim): what the
cache holds, a trim to its limit, and a clear. Over the DiskCacheFolder port (contract C-036), so the command line
no longer reaches into the dashboard's modules (W4.1). The words are the CLI's (cli/text/cache.py). Contract C-121.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping

from ..ports.cache import CacheSettings, CachedFile, DiskCacheFolder


@dataclass(frozen=True)
class CacheReport:
    """What `carl cache show` says: the folder, its settings, its files (newest first) and their sessions' names,
    the bytes used, and how many saved sessions are stored as changes and what that saves."""
    folder: str
    settings: CacheSettings
    files: List[CachedFile]
    names: Dict[str, str] = field(default_factory=dict)

    @property
    def used(self) -> int:
        return sum(f.bytes for f in self.files)

    @property
    def prompts(self) -> int:
        return sum(1 for f in self.files if f.prompt)

    @property
    def packed(self) -> int:
        return sum(1 for f in self.files if f.packed)


@dataclass(frozen=True)
class TrimResult:
    """What a trim did: the saved sessions it stored as changes, and the files it removed (to keep the limit)."""
    settings: CacheSettings
    packed: int
    removed: List[str]


class DiskCache:
    """The disk cache of one settings folder."""

    def __init__(self, cache: DiskCacheFolder) -> None:
        self.cache = cache

    @property
    def folder(self) -> str:
        return self.cache.folder()

    def show(self, config: Mapping[str, object]) -> CacheReport:
        """The files, newest first, with the names of their sessions; saving: from the folder as it is."""
        files = sorted(self.cache.files(), key=lambda f: -f.mtime)
        names = self.cache.session_names([f.session for f in files]) if files else {}
        return CacheReport(self.folder, self.cache.settings(config), files, names)

    def saving(self) -> int:
        return self.cache.saving()

    def trim(self, config: Mapping[str, object]) -> TrimResult:
        """The files of earlier versions go; with cache.share on, the new saved sessions are stored as changes; then
        the oldest files go until the rest fit the limit."""
        settings = self.cache.settings(config)
        self.cache.drop_legacy()
        packed = self.cache.pack() if settings.share else 0
        gone = self.cache.over_limit(settings.limit)
        self.cache.remove(gone)
        return TrimResult(settings, packed, gone)

    def clear(self) -> int:
        """Remove every saved state; how many there were."""
        files = self.cache.files()
        self.cache.remove([f.name for f in files])
        return len(files)
