"""Conversations stored as patches, for the disk cache (Settings > Caching): a saved conversation is stored as a patch
against the agent's prompt file it starts with (zstd --patch-from), so the prompt's part is kept
once, in the prompt file, and each session file holds only what is its own.

Measured (35B IQ3, 2026-10-04): a build session of 8.6K tokens, 115 MB whole, shares ~45 MB with the
build prompt; the rest is the recurrent state (~59 MB per conversation, never shared) and the
session's own tokens. A hybrid model's state can't be split by llama.cpp (it saves whole
sequences), so the patches are made after the save, on the files:

  name.bin            llama.cpp's file (--slot-save-path): written by a save, read by a restore
  name.bin.zst        the patch against the base (zstd, --long=31, its own checksum)
  name.bin.json       {base, size, packed_at}: the prompt file it is a patch against

pack() turns a new name.bin into the pair (only when the patch is clearly smaller), unpack() makes
name.bin again before a restore (its mtime set to packed_at: tidy() then knows it is a copy, not a
new save, and removes it after a minute). Prompt files (carl-prefix+…) stay whole: they are the
bases. The base is the model's prompt file whose tokens the session starts with (the file header:
magic "ggsq", version, a count, then -1, 1, N and the N tokens). zstd missing: nothing is packed.
"""
from __future__ import annotations

import array
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import List, Optional

from .model import JSONDict, jdict

MAGIC = 0x67677371            # "ggsq": llama_state_seq file
MIN_GAIN = 0.8                # pack only when the patch is at most 80% of the file
SETTLE_S = 30                 # a new save is packed when it is this old (llama.cpp has closed it)
COPY_S = 60                   # an unpacked copy goes when it is this old (the restore read it)
PACK = ".zst"
META = ".json"


def zstd() -> Optional[str]:
    return shutil.which("zstd")


def tokens(path: str, limit: int = 1 << 20) -> Optional[List[int]]:
    """The tokens a saved state holds (its header), None when the format isn't the one known."""
    try:
        with open(path, "rb") as f:
            raw = f.read(24)
            if len(raw) < 24:
                return None
            head = array.array("I")
            head.frombytes(raw)
            if head[0] != MAGIC or head[3] != 0xFFFFFFFF or head[4] != 1:
                return None
            n = head[5]
            if n > limit:
                return None
            raw = f.read(4 * n)
            if len(raw) != 4 * n:
                return None
            toks = array.array("i")
            toks.frombytes(raw)
            return list(toks)
    except OSError:
        return None


def base_for(folder: str, name: str) -> Optional[str]:
    """The model's prompt file the session starts with (the longest one), or None."""
    parts = name.split("+")
    if len(parts) < 4 or parts[0] != "carl-session":
        return None
    mine = tokens(os.path.join(folder, name))
    if not mine:
        return None
    best, best_n = None, 0
    head = f"carl-prefix+{parts[1]}+"
    for n in os.listdir(folder):
        if not (n.startswith(head) and n.endswith(".bin")):
            continue
        theirs = tokens(os.path.join(folder, n))
        if theirs and best_n < len(theirs) <= len(mine) and mine[:len(theirs)] == theirs:
            best, best_n = n, len(theirs)
    return best


def meta(folder: str, name: str) -> Optional[JSONDict]:
    try:
        with open(os.path.join(folder, name + META), encoding="utf-8") as f:
            doc = jdict(json.load(f))
    except (OSError, ValueError):
        return None
    base = doc.get("base")
    if not (isinstance(base, str) and base.startswith("carl-prefix+") and os.path.basename(base) == base):
        return None                          # the base is a prompt file in this folder, never a path elsewhere
    return doc if isinstance(doc.get("packed_at"), (int, float)) else None


def _zstd(args: List[str]) -> bool:
    tool = zstd()
    if not tool:
        return False
    try:
        return subprocess.run([tool, "-q", "-f", "--long=31", *args], stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def pack(folder: str, name: str) -> bool:
    """name.bin -> name.bin.zst + name.bin.json against its prompt file; False when it stays whole
    (no base, no zstd, no gain)."""
    src = os.path.join(folder, name)
    base = base_for(folder, name)
    if not base:
        return False
    tmp = src + PACK + ".tmp"
    try:
        size = os.path.getsize(src)
        if not _zstd(["-3", "-T0", f"--patch-from={os.path.join(folder, base)}", src, "-o", tmp]):
            return False
        if os.path.getsize(tmp) > size * MIN_GAIN:
            os.remove(tmp)
            return False
        packed_at = int(time.time())
        with open(src + META + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"base": base, "size": size, "packed_at": packed_at}, f)
        os.replace(tmp, src + PACK)
        os.replace(src + META + ".tmp", src + META)
        os.remove(src)
        return True
    except OSError:
        for p in (tmp, src + META + ".tmp"):
            if os.path.exists(p):
                os.remove(p)
        return False


def unpack(folder: str, name: str) -> bool:
    """name.bin again (from its patch and base) before a restore; True when it is there."""
    path = os.path.join(folder, name)
    if os.path.exists(path):
        return True
    m = meta(folder, name)
    base = os.path.join(folder, str(m["base"])) if m else ""
    if not m or not os.path.exists(path + PACK) or not os.path.exists(base):
        return False
    tmp = path + ".tmp"
    if not _zstd(["-d", f"--patch-from={base}", path + PACK, "-o", tmp]):
        return False
    try:
        if os.path.getsize(tmp) != m.get("size"):
            os.remove(tmp)
            return False
        t = float(m["packed_at"])
        os.utime(tmp, (t, t))                    # a copy: tidy() removes it after a minute
        os.replace(tmp, path)
        return True
    except OSError:
        return False


@dataclass
class Tidied:
    packed: int = 0
    copies: int = 0
    orphans: int = 0


def tidy(folder: str, now: Optional[float] = None) -> Tidied:
    """Pack new session saves, remove unpacked copies once read, remove patches whose base is gone."""
    now = time.time() if now is None else now
    out = Tidied()
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for n in names:
        if not n.startswith("carl-session+"):
            continue
        path = os.path.join(folder, n)
        if n.endswith(".bin"):
            try:
                st = os.stat(path)
            except OSError:
                continue
            m = meta(folder, n)
            if m and int(st.st_mtime) == int(m["packed_at"]) and os.path.exists(path + PACK):
                if now - st.st_ctime > COPY_S:     # an unpacked copy, read by now
                    os.remove(path)
                    out.copies += 1
            elif now - st.st_mtime > SETTLE_S and zstd() and pack(folder, n):
                out.packed += 1
        elif n.endswith(".bin" + PACK):
            stem = n[:-len(PACK)]
            m = meta(folder, stem)
            if not m or not os.path.exists(os.path.join(folder, str(m["base"]))):
                remove(folder, [stem])           # its base is gone: it can't be restored
                out.orphans += 1
    return out


def remove(folder: str, names: List[str]) -> None:
    """A saved state in every form: the file, its patch, its meta."""
    for n in names:
        for p in (n, n + PACK, n + META):
            try:
                os.remove(os.path.join(folder, p))
            except OSError:
                pass
