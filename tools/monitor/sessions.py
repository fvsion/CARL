"""A rolling context cache: each slot's conversation saved to disk, so a server restart doesn't
mean re-reading every open session (a 74K-token session takes ~13 min on the 27B).

The dashboard saves a slot (/slots/N?action=save, into ~/.config/carl/slots: the server's
--slot-save-path) before it stops or restarts the server, and while it runs once a slot holds
a new conversation state and has been idle for a while. After a start it restores each saved
slot into the same slot. OpenCode resends a conversation token for token, so the next turn
continues the restored state (measured: 16 new tokens read instead of 9.9K); a request that
doesn't match it is read in full, as without a cache.

One file per model and slot (the latest state: "rolling"); a manifest says what each file is
valid for (the model file, the KV type, the llama.cpp build), so a file is only ever restored
into the server it came from. Pure, except the manifest file helpers.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .model import JSONDict, jdict

MANIFEST = "carl-sessions.json"
MIN_TOKENS = 4096                   # a shorter conversation is quick to read again
IDLE_SECONDS = 120                  # save a changed slot after it has been idle this long
MIN_FREE_BYTES = 10 * 2 ** 30       # leave the disk this much


def server_key(model_file: str, kv: str, build: str) -> str:
    """What a saved state depends on besides its tokens: the model file, the KV type, the build."""
    try:
        st = os.stat(model_file)
        ident = f"{model_file}:{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        ident = model_file
    return f"{ident}|{kv}|{build}"


def file_name(model: str, slot: int) -> str:
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in model)[:80]
    return f"carl-session-{safe}-s{slot}.bin"


@dataclass
class SlotWatch:
    """What the dashboard last saw of a slot: its state (prompt tokens + decoded, task id), since
    when it is unchanged, and the state last saved."""
    state: Tuple[int, int] = (0, 0)
    since: float = 0.0
    saved: Tuple[int, int] = (0, 0)


def due(watch: Dict[int, SlotWatch], slots: Sequence[Tuple[int, bool, int, int]], now: float,
        idle: float = IDLE_SECONDS) -> List[int]:
    """The slots to save now: idle, holding at least MIN_TOKENS, changed since their last save
    and unchanged for `idle` seconds. slots: (id, busy, prompt tokens, task id). Updates watch."""
    out = []
    for sid, busy, n, task in slots:
        w = watch.setdefault(sid, SlotWatch())
        state = (n, task)
        if state != w.state or busy:
            w.state, w.since = state, now
            continue
        if n >= MIN_TOKENS and state != w.saved and now - w.since >= idle:
            out.append(sid)
    return out


def read_manifest(folder: str) -> JSONDict:
    try:
        with open(os.path.join(folder, MANIFEST), encoding="utf-8") as f:
            return jdict(json.load(f))
    except (OSError, ValueError):
        return {}


def write_manifest(folder: str, doc: JSONDict) -> None:
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, MANIFEST)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
    os.replace(path + ".tmp", path)


def record(doc: JSONDict, name: str, model: str, slot: int, key: str, tokens: int, when: float) -> JSONDict:
    """The manifest with a saved file recorded."""
    out = dict(doc)
    out[name] = {"model": model, "slot": slot, "key": key, "tokens": tokens, "saved": when}
    return out


def restorable(doc: JSONDict, folder: str, model: str, key: str, slots: Sequence[int]) -> List[Tuple[int, str, int]]:
    """(slot, file, tokens) saved for this model and server and present on disk, for these slots."""
    out = []
    for name, v in doc.items():
        e = jdict(v)
        sid = e.get("slot")
        if (e.get("model") == model and e.get("key") == key and isinstance(sid, int) and sid in slots
                and os.path.exists(os.path.join(folder, name))):
            out.append((sid, name, int(e.get("tokens") or 0)))
    return sorted(out)


def free_enough(folder: str) -> bool:
    try:
        st = os.statvfs(folder)
    except OSError:
        return False
    return st.f_bavail * st.f_frsize >= MIN_FREE_BYTES


def forget(doc: JSONDict, folder: str, name: str) -> JSONDict:
    """The manifest without a file (and the file gone)."""
    try:
        os.remove(os.path.join(folder, name))
    except OSError:
        pass
    return {k: v for k, v in doc.items() if k != name}


def stale(doc: JSONDict, model: str, key: str) -> List[str]:
    """This model's saved files that belong to another server setup (a new file, KV, build)."""
    return [n for n, v in doc.items() if jdict(v).get("model") == model and jdict(v).get("key") != key]


def summary(restored: Sequence[Tuple[int, str, int]]) -> Optional[str]:
    if not restored:
        return None
    return "conversations restored: " + ", ".join(f"slot {s} ({n / 1000:.1f}K tokens)" for s, _, n in restored)
