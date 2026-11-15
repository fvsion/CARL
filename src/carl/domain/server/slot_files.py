"""The disk cache's saved states and its settings, as plain data (Settings > Caching, the dashboard API, the trim):
the settings of config.json "cache" (CacheConfig), a saved state in the slots folder (CacheFile), and the pure rules over
them (what to remove to stay in the limit, how a file is named, the bytes saved by sharing). The folder itself is read
and written by adapters/common/slot_folder.py. Pure.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .snapshot import JSONDict, jdict

DEFAULT_GB = 10                 # a 74K-token session on the 35B is ~1 GB
SAVES = ("auto", "turn", "switch", "stop")
SWAS = ("auto", "full", "window")
MOVES = ("off", "auto")             # the project part for templates CARL does not know: off (in place) or auto (moved)
AUTO_S = 120                    # save = auto: after this much unsaved reading (a crash costs at most that)
PROMPT, CONVERSATION, SLOT = "prompt", "conversation", "slot"
SLOT_PREFIX = "carl-slot+"      # a slot CARL saved before a restart for a client without its plugins (slot_handoff.py)
STATE_PREFIXES = ("carl-prefix+", "carl-session+", SLOT_PREFIX)        # the saved states the folder lists
SAFE = re.compile(r"[^A-Za-z0-9._-]")


def safe_name(s: str, n: int) -> str:
    """The client's safeName (carl-cache.js): the same file names on both sides."""
    return SAFE.sub("_", s)[:n]


@dataclass(frozen=True)
class CacheConfig:
    disk_gb: int = DEFAULT_GB
    prefix: bool = True
    sessions: bool = True
    save: str = "auto"
    swa: str = "auto"
    auto_s: int = AUTO_S
    share: bool = True
    move: str = "off"               # the project part for templates CARL does not know: off (in place) or auto (moved)
    gate: int = 0                   # config.json delegation.gate: the new-file gate of carl-delegation (0 = off)
    keep_slots: bool = True         # the slots are saved before a restart CARL makes and put back after it (W1.6)

    @property
    def limit(self) -> int:
        return self.disk_gb * 10 ** 9


def config_of(cfg: JSONDict) -> CacheConfig:
    """The cache settings from a loaded config.json (defaults for what isn't set)."""
    sec = jdict(cfg.get("cache"))
    gb = sec.get("disk_gb")
    save, swa, auto_s = sec.get("save"), sec.get("swa"), sec.get("auto_s")
    return CacheConfig(gb if isinstance(gb, int) and not isinstance(gb, bool) and gb > 0 else DEFAULT_GB,
                       sec.get("prefix") is not False, sec.get("sessions") is not False,
                       save if save in SAVES else "auto", swa if swa in SWAS else "auto",
                       auto_s if isinstance(auto_s, int) and not isinstance(auto_s, bool) and auto_s > 0 else AUTO_S,
                       sec.get("share") is not False, "auto" if sec.get("move") == "auto" else "off",
                       gate_of(cfg), sec.get("keep_slots") is not False)


def gate_of(cfg: JSONDict) -> int:
    """config.json delegation.gate: 0 (off) to 99; 0 when it is not set or not valid."""
    g = jdict(cfg.get("delegation")).get("gate")
    return g if isinstance(g, int) and not isinstance(g, bool) and 0 <= g <= 99 else 0


@dataclass
class CacheFile:
    name: str
    bytes: int
    mtime: float
    packed: bool = False            # a conversation stored as a patch against its prompt (zstd_patch.py)
    whole: int = 0                  # then: its size whole
    base: str = ""                  # then: the prompt file it needs

    @property
    def kind(self) -> str:
        return (PROMPT if self.name.startswith("carl-prefix+") else SLOT if self.name.startswith(SLOT_PREFIX)
                else CONVERSATION)


def describe(name: str) -> str:
    """A saved state's file name as "model · agent" (a prompt), "model · session" (a conversation) or "model · slot N"
    (a slot kept over a restart: carl-slot+MODEL+FP+N.bin)."""
    parts = name[:-len(".bin")].split("+") if name.endswith(".bin") else []
    if len(parts) == 4 and parts[0] == "carl-slot":
        return f"{parts[1]} · slot {parts[3]}"
    return f"{parts[1]} · {parts[2 if parts[0] == 'carl-prefix' else 3]}" if len(parts) == 4 else name


def used(files: Sequence[CacheFile]) -> int:
    return sum(f.bytes for f in files)


def over_budget(files: Sequence[CacheFile], limit: int, keep: str = "") -> List[str]:
    """The files to remove so the rest fit `limit` bytes: the oldest conversations (and kept slots) first, then
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
    gone = set(out)                 # a conversation stored against a prompt that goes, goes with it
    out += [f.name for f in files if f.packed and f.base in gone and f.name not in gone]
    return out


def shared_saving(files: Sequence[CacheFile]) -> int:
    """Bytes the conversations stored as patches against their prompts save."""
    return sum(max(f.whole - f.bytes, 0) for f in files if f.packed)


def _rank(f: CacheFile) -> Tuple[int, float]:
    return (1 if f.kind == PROMPT else 0, f.mtime)


def gb(n: int) -> str:
    """Bytes as GB (the unit of the limit)."""
    return f"{n / 10 ** 9:.1f} GB"


def session_id(file_name: str) -> str:
    """The session ID in a saved session's file name (carl-session+MODEL+KEY+SESSION.bin); "" for another file."""
    parts = file_name[:-len(".bin")].split("+") if file_name.endswith(".bin") else []
    return parts[3] if len(parts) == 4 and parts[0] == "carl-session" else ""


def short_id(session: str) -> str:
    """A session ID as the dashboard shows one with no name: its first 12 characters and … (the SLOTS card's Cache
    column, the plugins' SlotSession names)."""
    return session[:12] + "…" if len(session) > 13 else session


RESIDENT_PREFIX, RESIDENT_SUFFIX = ".resident+", ".json"        # a slot's record: .resident+MODEL+SLOT.json


@dataclass(frozen=True)
class Resident:
    """A slot's session, as the client that ran it last recorded it: saved to `file` when the
    slot still holds that state (its task id is `task`)."""
    model: str
    slot: int
    file: str
    task: int
    path: str


def is_resident_name(name: str) -> bool:
    return name.startswith(RESIDENT_PREFIX) and name.endswith(RESIDENT_SUFFIX)


def resident_of(doc: JSONDict, model: str, path: str) -> Optional[Resident]:
    """The record DOC (the JSON of the file at PATH) as the MODEL's resident session; None for another model's record
    or one that does not name a session file."""
    slot, task, file = doc.get("slot"), doc.get("task"), doc.get("file")
    if (doc.get("model") == model and isinstance(slot, int) and isinstance(task, int) and isinstance(file, str)
            and file.startswith("carl-session+") and "/" not in file):
        return Resident(model, slot, file, task, path)
    return None

