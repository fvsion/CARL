"""The disk cache (Settings > Caching): ~/.config/carl/slots, the server's --slot-save-path, holds
the prompt states OpenCode and Pi save through the server (client/shared/carl-cache.js): each
agent's prompt (carl-prefix+MODEL+AGENT+HASH.bin, about 120 MB on the 35B) and each session's
conversation (carl-session+MODEL+KEY+SESSION.bin, about 13 KB per token on the 35B, so a
74K-token session is about 1 GB). Together they stay within cache.disk_gb (config.json, default
10 GB): the oldest conversations go first, then the oldest prompts. The clients on this Mac keep
the limit after each save; the dashboard checks it every minute (and `carl cache trim`).

The settings (config.json "cache", carl.domain.config.settings.CACHE_KEYS): disk_gb, prefix
(pre-read each agent's prompt), sessions (save the conversations), save (when: auto, turn,
switch, stop), auto_s (auto: the seconds of unsaved reading before a save) and swa (sliding-window models: auto, full, window); the clients on this Mac read
them. With save = auto, switch or stop the clients leave a record per slot of the session it
holds (.resident+MODEL+SLOT.json): the dashboard saves those slots before the server stops
(residents()). Pure, except listing(), legacy(), residents(), drop_resident() and remove().
"""
from __future__ import annotations

import json
import os
import shutil
from typing import Dict, List, Sequence

from . import zstd_patch as slotpack
from ...domain.server.slot_files import (AUTO_S, CONVERSATION, DEFAULT_GB, MOVES, PROMPT, SAVES, SLOT,  # noqa: F401
                                         STATE_PREFIXES, SWAS, CacheConfig, CacheFile, Resident, config_of, describe,
                                         gate_of, gb, is_resident_name, over_budget, resident_of, shared_saving, used)
from ...domain.server.snapshot import jdict

MIN_FREE_BYTES = 10 * 1000 ** 3  # no saves below 10 GB of free disk (the clients' rule too)


def listing(folder: str) -> List[CacheFile]:
    """The saved states in the folder, oldest first: each one in all its forms (the file, and for a
    conversation stored as a patch against its prompt, the patch and its meta: adapters/common/zstd_patch.py). The
    slots kept over a restart (carl-slot+…, slot_handoff.py) are listed too; their record (.slots+MODEL.json) is not
    a saved state."""
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    found: Dict[str, CacheFile] = {}
    for n in names:
        if not n.startswith(STATE_PREFIXES):
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


def residents(folder: str, model: str) -> List[Resident]:
    """The model's slot records (a record whose file names another model, or that can't be
    read, is skipped)."""
    out = []
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    for n in names:
        if not is_resident_name(n):
            continue
        path = os.path.join(folder, n)
        try:
            with open(path, encoding="utf-8") as f:
                doc = jdict(json.load(f))
        except (OSError, ValueError):
            continue
        r = resident_of(doc, model, path)
        if r:
            out.append(r)
    return out


def free_enough(folder: str, need: int = MIN_FREE_BYTES) -> bool:
    """At least `need` bytes free where the server saves (the clients don't save below it either). disk_usage: the
    same figure as statvfs's on a Mac and Linux, and Windows has it too (W6.8)."""
    try:
        return shutil.disk_usage(folder).free >= need
    except OSError:
        return False


def drop_resident(r: Resident) -> None:
    try:
        os.remove(r.path)
    except OSError:
        pass


def remove(folder: str, names: Sequence[str]) -> None:
    """Saved states in every form (the file, a patch, its meta)."""
    slotpack.remove(folder, list(names))


# What a client that went away leaves (research-2 suggestion 9, Phase 23.5): removed by the dashboard's minute loop.
CLAIM_STALE_S = 120         # a slot claim lives for one request (carl-cache.js CLAIM_STALE_MS)
TURN_STALE_S = 600          # a turn mark this old is a client that went away (carl-cache.js TURN_STALE_MS)
TMP_STALE_S = 3600          # a half-written file (a save or an unpack that stopped)


def reconcile(folder: str, now: float) -> List[str]:
    """Remove what no client will use: claims and turn marks of clients that went away, half-written files, and the
    pieces of a stored session whose other piece is gone (a .json without its .zst or .bin, a .zst without its .json).
    The names removed."""
    try:
        names = set(os.listdir(folder))
    except OSError:
        return []
    gone = []

    def age(n: str) -> float:
        try:
            return now - os.path.getmtime(os.path.join(folder, n))
        except OSError:
            return 0.0
    for n in sorted(names):
        stale = ((n.startswith(".claim+") and age(n) > CLAIM_STALE_S)
                 or (n.startswith(".turn+") and age(n) > TURN_STALE_S)
                 or (n.endswith(".tmp") and n.startswith((".", "carl-")) and age(n) > TMP_STALE_S))
        if n.startswith("carl-session+") and n.endswith(".bin.json"):
            stem = n[:-len(".json")]
            stale = stale or (stem not in names and stem + slotpack.PACK not in names)
        elif n.startswith("carl-session+") and n.endswith(".bin" + slotpack.PACK):
            stale = stale or n[:-len(slotpack.PACK)] + ".json" not in names
        if stale:
            try:
                os.remove(os.path.join(folder, n))
                gone.append(n)
            except OSError:
                pass
    return gone
