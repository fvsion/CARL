"""Ports of the disk cache of prompt states (the settings folder's slots/, which OpenCode and Pi fill through the
server), of what the dashboard reads of it, and of the client config that the sync of other computers reads
(client-config.json)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

from ..domain.shared.types import JsonObject


@dataclass(frozen=True)
class CacheSettings:
    """The disk cache's settings (config.json "cache"): its limit in GB, saved prompts and saved sessions on or off,
    and whether a saved session is stored as the changes to its saved prompt."""
    disk_gb: int
    prefix: bool
    sessions: bool
    share: bool

    @property
    def limit(self) -> int:
        """The limit in bytes (GB: 1000-based)."""
        return self.disk_gb * 10 ** 9


@dataclass(frozen=True)
class StatRow:
    """One row of the Caching panel's table of what the cache did (WHAT THE CACHE DID)."""
    what: str
    count: str = ""
    tokens: str = ""
    time: str = ""
    written: str = ""


@dataclass(frozen=True)
class CachedFile:
    """One saved state in the disk cache, in all its forms. name: its file name (carl-prefix+... or
    carl-session+...bin); prompt: a saved prompt, else a saved session; packed: a saved session stored as the changes
    to its saved prompt; label: "model · agent" or "model · session"; session: the session's ID ("" for a prompt)."""
    name: str
    prompt: bool
    bytes: int
    mtime: float
    packed: bool
    label: str
    session: str


class DiskCacheFolder(Protocol):
    """The disk cache folder (~/.config/carl/slots, CARL_CONF_DIR/slots). Contract C-036."""

    def folder(self) -> str:
        """The folder's path."""
        ...

    def settings(self, config: Mapping[str, object]) -> CacheSettings:
        """The cache settings of a loaded config.json (the defaults for what it does not set)."""
        ...

    def files(self) -> List[CachedFile]:
        """The saved states, oldest first; [] when the folder cannot be read."""
        ...

    def drop_legacy(self) -> None:
        """Remove the files of earlier versions that nothing uses."""
        ...

    def pack(self) -> int:
        """Store the new saved sessions as the changes to their saved prompts; how many it stored."""
        ...

    def over_limit(self, limit: int) -> List[str]:
        """The files to remove so that the rest fit limit bytes: the oldest sessions first, then the oldest prompts."""
        ...

    def saving(self) -> int:
        """The bytes that the sessions stored as changes save."""
        ...

    def remove(self, names: Sequence[str]) -> None:
        """Remove saved states in every form."""
        ...

    def session_names(self, ids: Sequence[str]) -> Dict[str, str]:
        """Session ID -> its name in OpenCode's or Pi's store on this computer, for the IDs they know."""
        ...


CacheEvent = Dict[str, object]          # one event of a client's cache ("t", "e": place, save, retire, compact, ...)
Tally = Tuple[float, int, int, int, float]  # one answered request: (its end, slot, prompt tokens, read, read seconds)


class CacheWatch(Protocol):
    """What the dashboard reads of the disk cache: the saved states in a folder and what the clients' cache did
    (the settings folder's cache-events/), with the Caching panel's and the SLOTS card's rows made from them.
    Contract C-054.

    The saved states and the table's rows are the adapter's records; the pure part in its own module and the records as
    domain values are open (no slice takes them). A saved state has name, bytes, mtime, packed, whole and kind; a row
    has what, count, tokens, time, written."""

    windows: Tuple[str, ...]        # the Caching panel's times, in order ("last hour", "today", "since the start")

    def files(self, folder: str) -> List[Any]:
        """The saved states in folder, oldest first (each one in all its forms); [] when it cannot be read."""
        ...

    def events(self, carl_dir: str) -> List[CacheEvent]:
        """The events of the last two days in carl_dir's cache-events/, reading only what is new since the last
        call (another carl_dir starts over); a file that cannot be read keeps what was read."""
        ...

    def slot_column(self, events: Sequence[CacheEvent], slot: int, model: str, names: Mapping[str, str],
                    tokens: int = 0) -> Optional[Tuple[str, str, str]]:
        """The SLOTS card's Cache column for the session in a slot: (from, saved, session); None when no event
        placed one there."""
        ...

    def slot_session(self, events: Sequence[CacheEvent], slot: int, model: str, before: Optional[float] = None) -> str:
        """The ID of the session the latest placement put in a slot (of that model, when it is known), of those before
        `before` (epoch seconds; None: of all): the session a request on that slot belonged to. "" for none. The dashboard
        gives these sessions to the plugins' hooks (SlotSession, ports/plugin.py)."""
        ...

    def table(self, events: Sequence[CacheEvent], tally: Sequence[Tally], since: float) -> List[StatRow]:
        """The rows of WHAT THE CACHE DID for the events and requests since `since`."""
        ...

    def window_start(self, window: str, now: float, server_start: Optional[float]) -> float:
        """The time one of the windows starts (today: local midnight; since the start: the server's start)."""
        ...


class ClientConfigStore(Protocol):
    """The client config published for the sync of other computers (client-config.json). Contract C-037."""

    def publish(self, models: JsonObject) -> str:
        """Write the config (atomically); its version, a hash of its content."""
        ...
