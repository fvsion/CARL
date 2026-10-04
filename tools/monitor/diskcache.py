"""The disk cache (Settings > Caching): ~/.config/carl/slots, the server's --slot-save-path, holds
the prompt states OpenCode and Pi save through the server (client/shared/carl-cache.js): each
agent's prompt (carl-prefix+MODEL+AGENT+HASH.bin, about 120 MB on the 35B) and each session's
conversation (carl-session+MODEL+KEY+SESSION.bin, about 13 KB per token on the 35B, so a
74K-token session is about 1 GB). Together they stay within cache.disk_gb (config.json, default
10 GB): the oldest conversations go first, then the oldest prompts. The clients on this Mac keep
the limit after each save; the dashboard checks it every minute (and `./carl.sh cache trim`).

The settings (config.json "cache", carl_core.domain.settings.CACHE_KEYS): disk_gb, prefix
(pre-read each agent's prompt), sessions (save the conversations), save (when: auto, turn,
switch, stop), auto_s (auto: the seconds of unsaved reading before a save) and swa (sliding-window models: auto, full, window); the clients on this Mac read
them. With save = auto, switch or stop the clients leave a record per slot of the session it
holds (.resident+MODEL+SLOT.json): the dashboard saves those slots before the server stops
(residents()). Pure, except listing(), legacy(), residents(), drop_resident() and remove().
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from . import slotpack
from .model import JSONDict, jdict

DEFAULT_GB = 10                 # a 74K-token session on the 35B is ~1 GB
SAVES = ("auto", "turn", "switch", "stop")
SWAS = ("auto", "full", "window")
AUTO_S = 120                    # save = auto: after this much unsaved reading (a crash costs at most that)
MIN_FREE_BYTES = 10 * 2 ** 30   # no saves below this much free disk (the clients' rule too)
PROMPT, CONVERSATION = "prompt", "conversation"


@dataclass(frozen=True)
class CacheConfig:
    disk_gb: int = DEFAULT_GB
    prefix: bool = True
    sessions: bool = True
    save: str = "auto"
    swa: str = "auto"
    auto_s: int = AUTO_S
    share: bool = True

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
                       sec.get("share") is not False)


@dataclass
class CacheFile:
    name: str
    bytes: int
    mtime: float
    packed: bool = False            # a conversation stored as a patch against its prompt (slotpack.py)
    whole: int = 0                  # then: its size whole
    base: str = ""                  # then: the prompt file it needs

    @property
    def kind(self) -> str:
        return PROMPT if self.name.startswith("carl-prefix+") else CONVERSATION


def listing(folder: str) -> List[CacheFile]:
    """The saved states in the folder, oldest first: each one in all its forms (the file, and for a
    conversation stored as a patch against its prompt, the patch and its meta: slotpack.py)."""
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    found: Dict[str, CacheFile] = {}
    for n in names:
        if not n.startswith(("carl-prefix+", "carl-session+")):
            continue
        stem = n[:n.index(".bin") + 4] if ".bin" in n else ""
        if not stem or n not in (stem, stem + slotpack.PACK, stem + slotpack.META):
            continue
        try:
            st = os.stat(os.path.join(folder, n))
        except OSError:
            continue
        f = found.setdefault(stem, CacheFile(stem, 0, 0.0))
        f.bytes += st.st_size
        f.mtime = max(f.mtime, st.st_mtime)
        if n.endswith(slotpack.PACK):
            m = slotpack.meta(folder, stem)
            f.packed, f.whole, f.base = True, int(m.get("size") or 0) if m else 0, str(m.get("base") or "") if m else ""
    return sorted(found.values(), key=lambda f: f.mtime)


def legacy(folder: str) -> List[str]:
    """Files of Phase 11 (the dashboard's pre-read and per-slot saves, named with dashes): no longer used."""
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return [n for n in names if n == "carl-sessions.json" or n.startswith(("carl-prefix-", "carl-session-"))]


def describe(name: str) -> str:
    """A saved state's file name as "model · agent" (a prompt) or "model · session" (a conversation)."""
    parts = name[:-len(".bin")].split("+") if name.endswith(".bin") else []
    return f"{parts[1]} · {parts[2 if parts[0] == 'carl-prefix' else 3]}" if len(parts) == 4 else name


@dataclass(frozen=True)
class Resident:
    """A slot's session, as the client that ran it last recorded it: saved to `file` when the
    slot still holds that state (its task id is `task`)."""
    model: str
    slot: int
    file: str
    task: int
    path: str


def residents(folder: str, model: str) -> List[Resident]:
    """The model's slot records (a record whose file names another model, or that can't be
    read, is skipped)."""
    out = []
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    for n in names:
        if not (n.startswith(".resident+") and n.endswith(".json")):
            continue
        path = os.path.join(folder, n)
        try:
            with open(path, encoding="utf-8") as f:
                doc = jdict(json.load(f))
        except (OSError, ValueError):
            continue
        slot, task, file = doc.get("slot"), doc.get("task"), doc.get("file")
        if (doc.get("model") == model and isinstance(slot, int) and isinstance(task, int) and isinstance(file, str)
                and file.startswith("carl-session+") and "/" not in file):
            out.append(Resident(model, slot, file, task, path))
    return out


def free_enough(folder: str, need: int = MIN_FREE_BYTES) -> bool:
    """At least `need` bytes free where the server saves (the clients don't save below it either)."""
    try:
        st = os.statvfs(folder)
    except OSError:
        return False
    return st.f_bavail * st.f_frsize >= need


def drop_resident(r: Resident) -> None:
    try:
        os.remove(r.path)
    except OSError:
        pass


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
    gone = set(out)                 # a conversation stored against a prompt that goes, goes with it
    out += [f.name for f in files if f.packed and f.base in gone and f.name not in gone]
    return out


def shared_saving(files: Sequence[CacheFile]) -> int:
    """Bytes the conversations stored as patches against their prompts save."""
    return sum(max(f.whole - f.bytes, 0) for f in files if f.packed)


def _rank(f: CacheFile) -> Tuple[int, float]:
    return (0 if f.kind == CONVERSATION else 1, f.mtime)


def remove(folder: str, names: Sequence[str]) -> None:
    """Saved states in every form (the file, a patch, its meta)."""
    slotpack.remove(folder, list(names))


def gb(n: int) -> str:
    """Bytes as GB (the unit of the limit)."""
    return f"{n / 10 ** 9:.1f} GB"
