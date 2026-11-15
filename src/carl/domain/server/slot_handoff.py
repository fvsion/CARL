"""The slot handoff (W1.6, cache.keep_slots): the slots a client without CARL's plugins left in the server, saved by
their number before a stop CARL makes (Apply, Stop, Auto-tune, a router load from the dashboard) and put back once
after the next start, so the client's next request finds its prompt in a slot. The rules, as plain data: what a saved
state depends on (the fingerprint) and why two differ, which slots to save, which file goes into which slot after the
start, and the names of the files. The application (server_jobs) runs it; the folder is the disk cache's
(adapters/common/slot_folder.py). Contract C-138. Pure.

llama.cpp checks little on a restore (the token count against the slot's size, the layer count, the K and V types,
the V layout): another quantization of the same model passes and gives wrong answers. So a set is put back only when
every part of the fingerprint is the same; the slot count (with it --kv-unified, which CARL passes with 2 or more
slots), the context per slot, -ub, -b, a drafter in its own file (-md), MTP on or off and where the weights sit are
not in it (a file goes into any idle, empty slot that holds its tokens). Measured on a PC with the 0.6B and llama.cpp
b11429 (W1.6 wave 2): a state saved with 2 slots in one pool goes back into 1 slot without it and the other way, across
a -ub change, and with or without a -md drafter (the file holds the model's own cache only), each time reading only the
new tokens and giving the same answer at temperature 0; llama.cpp itself refuses another K and V type. Measured on a PC
with the Qwen3.6-35B-A3B Q4 and b11738 (W1.6 wave 3): a state saved without MTP goes into a server with it, and the
other way, with the same answer and the drafts accepted as often as in a warm slot.

On b11738 the file of a hybrid Qwen model holds the slot's context checkpoints too: a restored A3B slot went back to
the end of its last prompt when the next request differed there (W1.6 wave 3). An A3B slot file is 0.3 to 0.6 GB for
4K to 11K tokens, mostly the recurrent state and the checkpoints.

llama.cpp b11429's /slots shows a slot that ran no task with no id_task and no tokens, and a restore does not change
that: a slot put back reads as empty until a request uses it. So the jobs keep what they put back (restored: slot to
tokens) and count it at the next stop.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .slot_files import SLOT_PREFIX, safe_name
from .snapshot import JSONDict, SlotInfo, flag, jdict, jlist

RECORD_PREFIX, RECORD_SUFFIX = ".slots+", ".json"       # one record per model: .slots+MODEL.json
MIN_TOKENS = 1024               # a slot with fewer tokens is not saved (the clients' own minimum for a file)
HASH_LEN = 12                   # the fingerprint's short hash in a file name
DEFAULT_KV = "f16"
# The parts of the fingerprint, in order, with the words a difference is told in.
PARTS: Tuple[Tuple[str, str], ...] = (
    ("model", "the model file"), ("build", "the llama.cpp build"), ("ctk", "the K cache type"),
    ("ctv", "the V cache type"), ("fa", "the flash attention setting"), ("swa", "the sliding-window cache"),
    ("rope", "the rope settings"))
ROPE_FLAGS = ("--rope-scaling", "--rope-scale", "--rope-freq-base", "--rope-freq-scale", "--yarn-orig-ctx")


@dataclass(frozen=True)
class SavedSlot:
    """One slot saved to a file: its number then, its tokens, the file's bytes, and its rank (0: the slot used last)."""
    slot: int
    file: str
    tokens: int
    bytes: int = 0
    rank: int = 0


@dataclass(frozen=True)
class HandoffSet:
    """The slots of one model saved at one stop, with the fingerprint of the server that held them."""
    model: str
    fingerprint: Dict[str, str]
    saved: float
    files: Tuple[SavedSlot, ...]

    @property
    def tokens(self) -> int:
        return sum(f.tokens for f in self.files)


def _words(cmd: str) -> List[str]:
    return cmd.split()


def _has(words: Sequence[str], *names: str) -> bool:
    return any(n in words for n in names)


def fingerprint(model_path: str, stamp: Optional[Tuple[int, float]], build: str, cmd: str) -> Dict[str, str]:
    """What a saved state depends on: the model file (its path, size and time; not the catalogue's SHA-256, which
    would mean reading the whole file at each stop), the llama.cpp build, the K and V types, flash attention, the
    sliding-window cache and the rope settings (not MTP: measured, see the module doc). cmd: the server's own command
    line (the loaded model's in router mode; is_server_cmd); stamp: the model file's (bytes, mtime), None when it
    cannot be read."""
    w = _words(cmd)
    size, mtime = stamp if stamp else (-1, -1.0)
    return {
        "model": f"{model_path}|{size}|{int(mtime)}",
        "build": build,
        "ctk": flag(cmd, "-ctk", "--cache-type-k") or DEFAULT_KV,
        "ctv": flag(cmd, "-ctv", "--cache-type-v") or DEFAULT_KV,
        "fa": flag(cmd, "-fa", "--flash-attn") or "auto",
        "swa": "full" if _has(w, "--swa-full") else "window",
        "rope": " ".join(f"{n}={flag(cmd, n)}" for n in ROPE_FLAGS if flag(cmd, n))}


def is_server_cmd(cmd: str) -> bool:
    """Whether cmd is a server's own command line, which names its model file (-m). On Windows a start's own process
    (carl.py start, python.exe) stays the parent of llama-server; until the dashboard finds the listener, the snapshot
    may show its command line, which has no -m: a fingerprint of it would read llama.cpp's defaults (f16)."""
    return bool(flag(cmd, "-m", "--model"))


def model_path_of(props: Mapping[str, object], cmd: str) -> str:
    """The model file the server runs: /props model_path, else the command line's -m."""
    return str(props.get("model_path") or flag(cmd, "-m", "--model") or "")


def short_hash(fp: Mapping[str, str]) -> str:
    return hashlib.sha256(json.dumps(dict(fp), sort_keys=True).encode("utf-8")).hexdigest()[:HASH_LEN]


def difference(old: Mapping[str, str], new: Mapping[str, str]) -> str:
    """Why a set saved under old cannot go into a server with new ("" when it can): the first part that differs, with
    both values when they are short ("the V cache type changed, q4_0 to q8_0")."""
    for key, words in PARTS:
        a, b = str(old.get(key, "")), str(new.get(key, ""))
        if a == b:
            continue
        if key in ("ctk", "ctv", "fa", "swa") and a and b:
            return f"{words} changed, {a} to {b}"
        return f"{words} changed"
    return ""


def tokens_of(s: SlotInfo) -> int:
    return s.prompt + s.decoded


def task_rank(s: SlotInfo) -> int:
    """The slot's last task id (a larger one ran later); -1 when there is none."""
    return s.task if isinstance(s.task, int) else -1


def with_restored(slots: Sequence[SlotInfo], restored: Mapping[int, int]) -> List[SlotInfo]:
    """The slots as they hold: a slot the jobs put a file back into (restored: slot to tokens) that ran no task since
    reads as empty in /slots, but holds the file's tokens."""
    return [replace(s, prompt=restored[s.id]) if s.id in restored and task_rank(s) < 0 and tokens_of(s) == 0 else s
            for s in slots]


def to_save(slots: Sequence[SlotInfo], owned: Sequence[int], restored: Optional[Mapping[int, int]] = None
            ) -> List[SlotInfo]:
    """The slots to save, the one used last first: idle, holding at least MIN_TOKENS, and not a plugin's (owned: the
    slots a client with CARL's plugins recorded or placed a session in; the plugin saves those). restored: the slots
    put back since the start, the one used last first, which no request may have used yet (with_restored): they are
    saved again, after the slots a request used since, in their old order."""
    order = {slot: i for i, slot in enumerate(restored or {})}
    picks = [s for s in with_restored(slots, restored or {})
             if s.id is not None and not s.busy and s.id not in owned and tokens_of(s) >= MIN_TOKENS]
    return sorted(picks, key=lambda s: (-task_rank(s), order.get(s.id, 0) if s.id is not None else 0))


def is_empty(s: SlotInfo) -> bool:
    """Idle and holding nothing: a file may go in."""
    return s.id is not None and not s.busy and tokens_of(s) == 0


def placements(files: Sequence[SavedSlot], slots: Sequence[SlotInfo]) -> List[Tuple[SavedSlot, int]]:
    """Which file goes into which slot, the slot used last first: its own number when that slot is empty and holds
    its tokens, else the first empty one that does. A slot a client took already is never used; with fewer slots,
    fewer files go back."""
    free = {s.id: s for s in slots if is_empty(s)}
    out: List[Tuple[SavedSlot, int]] = []
    for f in sorted(files, key=lambda f: f.rank):
        own = free.get(f.slot)
        target = own if own is not None and own.n_ctx >= f.tokens else next(
            (s for _, s in sorted(free.items(), key=lambda kv: kv[0] or 0) if s.n_ctx >= f.tokens), None)
        if target is None or target.id is None:
            continue
        out.append((f, target.id))
        del free[target.id]
    return out


def file_name(model: str, fp: Mapping[str, str], slot: int) -> str:
    """carl-slot+MODEL+HASH+N.bin (MODEL as the plugins' names have it)."""
    return f"{SLOT_PREFIX}{safe_name(model, 60)}+{short_hash(fp)}+{slot}.bin"


def record_name(model: str) -> str:
    """.slots+MODEL.json: a dot file, as a .json next to a .bin is a patch's meta (zstd_patch.META)."""
    return f"{RECORD_PREFIX}{safe_name(model, 60)}{RECORD_SUFFIX}"


def record_doc(s: HandoffSet) -> JSONDict:
    return {"model": s.model, "fingerprint": dict(s.fingerprint), "saved": s.saved,
            "files": [{"slot": f.slot, "file": f.file, "tokens": f.tokens, "bytes": f.bytes, "rank": f.rank}
                      for f in s.files]}


def record_of(doc: object) -> Optional[HandoffSet]:
    """A record read back (None: not one). A file name that is not a kept slot's, or that names a folder, is left
    out: the handoff never touches another file."""
    d = jdict(doc)
    fp = jdict(d.get("fingerprint"))
    if not isinstance(d.get("model"), str) or not fp:
        return None
    files = []
    for x in jlist(d.get("files")):
        e = jdict(x)
        slot, name, tokens = e.get("slot"), e.get("file"), e.get("tokens")
        if not (isinstance(slot, int) and isinstance(name, str) and isinstance(tokens, int)):
            continue
        if not name.startswith(SLOT_PREFIX) or not name.endswith(".bin") or "/" in name or "\\" in name:
            continue
        b, r = e.get("bytes"), e.get("rank")
        files.append(SavedSlot(slot, name, tokens, b if isinstance(b, int) else 0, r if isinstance(r, int) else 0))
    saved = d.get("saved")
    return HandoffSet(str(d["model"]), {str(k): str(v) for k, v in fp.items()},
                      float(saved) if isinstance(saved, (int, float)) else 0.0, tuple(files))


def k_tokens(n: int) -> str:
    """Tokens as the toasts say them: 41K (K = 1024), or the number below 1K."""
    return f"{round(n / 1024)}K" if n >= 1024 else str(n)


def slots_text(n: int) -> str:
    return f"{n} slot" + ("" if n == 1 else "s")


def put_back_toast(n: int, tokens: int) -> str:
    return f"CARL put back {slots_text(n)} ({k_tokens(tokens)} tokens) from before the restart."


def put_back_note(n: int, tokens: int) -> str:
    """The Caching panel's line under Keep slots."""
    return f"{slots_text(n)} put back after the last start, {k_tokens(tokens)} tokens"


def not_put_back_toast(why: str) -> str:
    return f"Slots not put back: {why}."


NO_SLOTS = "the server did not list its slots"


def none_reason(placed: int, refused: int, gone: int) -> str:
    """Why no slot went back, when every part of the fingerprint was the same: no idle, empty slot held a file
    (placed 0), else the server refused the files, else they were gone (carl cache clear)."""
    if not placed:
        return "no idle, empty slot held them"
    return "the server refused them" if refused else "the files are gone" if gone else "nothing to put back"


def saving_status(n: int) -> str:
    return f"CARL is saving {slots_text(n)} for after the start…"
