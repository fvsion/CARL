"""The SLOTS card: the slots, the context memory and the Cache column.
Pure: they render a ServerData snapshot and a View."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from ....domain.memory.gguf import KV_BPE, kv_bytes_per_token
from ....domain.server.serverlog import CacheState, counts_cache
from ....domain.server.snapshot import ServerData, SlotInfo, flag, flag_int
from ....domain.shared.units import memory, percent, tokens
from ....ui.fmt import (B, CYN, DIM, MAG, R, RED, YEL, Card, CardLine, aligned, bar, fit, row, vlen)
from ....ui.words import plural
from .view import View, read_left

# ---------------------------------------------------------------- llama.cpp cards
@dataclass
class KVInfo:
    """Context memory and recurrent-state memory of the running llama.cpp server."""
    k: str
    v: str
    n_ctx: int
    per_tok: float
    kv: float           # bytes allocated for the whole pool
    slots: int
    mtp: float
    rs: int
    ckpt_n: int
    ckpt_max: int
    cache_ram: int


def kv_info(d: ServerData) -> Optional[KVInfo]:
    """Context memory and state memory from the GGUF shape and the server's flags; None without a shape
    or a context."""
    shp, cmd = d.shape, d.cmd
    ktype = flag(cmd, "-ctk", "--cache-type-k") or "f16"
    vtype = flag(cmd, "-ctv", "--cache-type-v") or "f16"
    n_ctx = d.n_ctx or flag_int(cmd, "-c", "--ctx-size", default=0)
    if not shp or not n_ctx:
        return None
    nslots = flag_int(cmd, "--parallel", "-np", default=1)
    pool = flag_int(cmd, "-c", "--ctx-size", default=n_ctx)
    per_tok = kv_bytes_per_token(shp, ktype, vtype)
    mtp_tok = shp["kv_elems_per_token_mtp"] / 2 * (KV_BPE.get(ktype, 2) + KV_BPE.get(vtype, 2))
    ckpt_n = flag_int(cmd, "--ctx-checkpoints", default=8)
    return KVInfo(k=ktype, v=vtype, n_ctx=n_ctx, per_tok=per_tok, kv=per_tok * pool, slots=nslots, mtp=mtp_tok * pool,
                  rs=shp["rs_bytes"] * nslots, ckpt_n=ckpt_n, ckpt_max=ckpt_n * shp["rs_bytes"] * nslots,
                  cache_ram=flag_int(cmd, "--cache-ram", default=0) * 2**20)


def slot_state(x: SlotInfo) -> str:
    """What a slot does, in words: writing, reading, free (between turns), free."""
    if x.busy:
        return f"{CYN}writing{R}" if x.decoded else f"{YEL}reading{R}"
    return "free (between turns)" if x.prompt + x.decoded else "free"


def swa_line(d: ServerData) -> str:
    """A model with sliding-window layers (Gemma): which cache it runs with ('' for other models)."""
    if not d.shape or not d.shape.get("swa_window"):
        return ""
    if "--swa-full" in d.cmd:
        return "Sliding window: full cache. CARL can restore saved sessions and prompts."
    return (f"Sliding window: {YEL}window cache{R}. CARL cannot restore saved sessions and prompts "
            f"(Settings > Caching).")


def request_line(d: ServerData) -> str:
    """The busy request in one sentence: its size, the reused tokens, what is read or still to read."""
    if not d.busy or not d.prompt:
        return ""
    reused = f"{tokens(d.cached)} reused ({percent(d.cached / d.prompt)})"
    if d.decoded == 0:
        return (f"This request: {tokens(d.prompt)} tokens. {reused}, {tokens(d.processed)} read, "
                f"{tokens(read_left(d))} still to read.")
    return (f"This request: {tokens(d.prompt)} tokens. {reused}, {tokens(max(d.prompt - d.cached, 0))} read. "
            f"{plural(d.decoded, 'token')} written so far.")


def cache_words(c: CacheState) -> str:
    """The RAM cache's sessions in words: empty, 1 session, 3 sessions (llama.cpp's "prompts": each a session or a
    saved prompt that left a slot)."""
    return "empty" if not c.prompts else plural(c.prompts, "session")


def slot_word(x: SlotInfo) -> str:
    """What a slot does: writing, reading, between turns, free."""
    if x.busy:
        return f"{CYN}writing{R}" if x.decoded else f"{YEL}reading{R}"
    return f"{DIM}between turns{R}" if x.prompt + x.decoded else f"{DIM}free{R}"


def pool_fill(d: ServerData) -> float:
    """The share of the context the slots hold now (0..1)."""
    total = sum(x.n_ctx for x in d.slot_list)
    return sum(x.prompt + x.decoded for x in d.slot_list) / total if total else 0.0


def ram_cache(v: View, d: ServerData, kv: Optional[KVInfo]) -> Optional[CacheState]:
    """The RAM cache to show (Phase 23.4.4 item 12): its last state in the log (empty before the first one), when the
    server runs with a RAM cache and at the log level that reports it (CARL's start: -lv 4 through its log filter);
    None when the dashboard cannot know it (no row)."""
    if not kv or not kv.cache_ram or not counts_cache(d.cmd):
        return None
    return v.log.cache or CacheState(prompts=0, bytes=0, limit_bytes=kv.cache_ram, limit_tokens=0)


CACHE_AT = 30                            # the Cache column starts here in a busy slot's block (Phase 23.5.1.1)


def cache_beside(block: List[str], side: Tuple[str, str, str], w: int) -> List[CardLine]:
    """A busy slot's block with the Cache column beside it: what the cache did for the session in that slot (where its
    state came from, its last save, its name; cache_events.py slot_column). The block's rows are aligned first (the
    label column), so the Cache column lines up whatever the labels."""
    left = aligned(block)
    room = max(w - CACHE_AT - 9, 4)
    right = [f"{B}Cache{R}", *(f"{DIM}{label:<9}{R}{value if vlen(value) <= room else fit(value, room)}"
                               for label, value in zip(("From", "Saved", "Session"), side))]
    out: List[CardLine] = []
    for i, ln in enumerate(left):
        text = str(ln)
        out.append(text + " " * max(CACHE_AT - vlen(text), 1) + right[i] if i < len(right) else text)
    return out


def card_slots(v: View, d: ServerData, w: int = 66) -> Card:
    """Each slot's fill and what it does, and the RAM cache's (its sessions and memory, when the log says them); the
    busy request; at full the context memory, the recurrent state, the checkpoints, the RAM cache, how far it can grow
    and the trained length."""
    kv = kv_info(d)
    cmd, sl, n_ctx = d.cmd, d.slot_list, d.n_ctx
    nslots = len(sl) or flag_int(cmd, "--parallel", "-np", default=1)
    cache = ram_cache(v, d, kv) if (d.slots or sl) else None
    narrow = w - 4 < 56                        # the title has the size: a narrow card shows only the fill
    lab = 9 if cache else 6                    # "RAM cache" or "slot 0": the slot rows' labels line up with it
    bw = max(min(w - 4 - (lab - 6) - (34 if narrow else 42), 24), 6)   # "slot 0  " bar "  41.5K of 96K   between turns"
    L: List[CardLine] = []
    for i in range(nslots):
        x = sl[i] if i < len(sl) else None
        u = (x.prompt + x.decoded) if x else 0
        cap = x.n_ctx if x else n_ctx
        size = f"{tokens(u):>6}" if narrow else f"{tokens(u):>6} of {tokens(cap) if cap else '–'}"
        name = f"slot {x.id if x else i}"
        L.append(f"{name.ljust(lab)}  {bar(u / cap if cap else 0, bw)}  {size}   {slot_word(x) if x else ''}")
    if cache:
        limit = cache.limit_bytes or (kv.cache_ram if kv else 0)
        used = memory(cache.bytes) if cache.bytes else "0"
        size = used if narrow else f"{used} of {memory(limit)}"
        L.append(f"{'RAM cache'.ljust(lab)}  {bar(cache.bytes / limit if limit else 0, bw)}  {size}   "
                 f"{DIM}{cache_words(cache)}{R}")
    if not d.slots and not sl:
        L = [f"{DIM}No model is loaded.{R}"]
    busy = [x for x in sl if x.busy] or ([SlotInfo(0, True, d.task, n_ctx, d.prompt, d.cached, d.processed, d.decoded)]
                                         if d.busy else [])
    for x in busy:                       # one block per busy slot: its own request
        if not x.prompt:
            continue
        side = v.cache_slots.get(x.id) if v.cache_column else None
        block = [f"{B}slot {x.id}{R}   {slot_word(x)}", row("request", f"{tokens(x.prompt)} tokens"),
                 row("reused", f"{tokens(x.cached)} ({percent(x.cached / x.prompt)})"
                     + (f"   {DIM}from{R} {v.reuse_from}" if v.reuse_from and not side else ""))]
        if x.decoded == 0:
            block += [row("read", tokens(x.processed)), row("still to read", tokens(max(x.prompt - x.cached - x.processed, 0)))]
        else:
            block.append(row("written", plural(x.decoded, "token")))
        L.append("")
        L += cache_beside(block, side, w - 4) if side else block
    if kv and kv.k != kv.v:
        L.append(f"{RED}⚠ K and V types differ ({kv.k}, {kv.v}): prompts read about 5 × more slowly.{R}")
    if d.shape and d.shape.get("swa_window"):
        if "--swa-full" in cmd:
            if v.full:
                L.append(row("sliding window", "Full cache: saved sessions can be restored."))
        else:
            L.append(row("sliding window", f"{YEL}Window cache{R}: saved sessions cannot be restored."))
    if v.full and kv:
        used = sum(x.prompt + x.decoded for x in sl)
        pool_tokens = flag_int(cmd, "-c", "--ctx-size", default=0)
        frac = used / pool_tokens if pool_tokens else 0
        L.append("")
        L.append(row("context memory", f"{MAG}{kv.k}{R} K, {MAG}{kv.v}{R} V"))
        L.append(row("allocated", f"{memory(kv.kv)} for {plural(kv.slots, 'slot')}"))
        L.append(row("in use", memory(kv.kv * frac)))
        if kv.rs:
            L.append(row("recurrent state", memory(kv.rs)))
            L.append(row("checkpoints", f"Up to {memory(kv.ckpt_max)} ({kv.ckpt_n} per slot)"))
        if cache:
            limit = cache.limit_bytes or kv.cache_ram
            # narrow: no limit (the bar above shows the fill against it), so the row never wraps
            L.append(row("RAM cache", "empty" if not cache.prompts else
                         f"{cache_words(cache)}, {memory(cache.bytes)}" + ("" if narrow else f" of {memory(limit)}")))
        L.append(row("can grow to", memory(kv.kv + kv.rs + kv.ckpt_max + kv.cache_ram)))
        if n_ctx and pool_tokens and pool_tokens != n_ctx * nslots:
            L.append(row("shared pool", f"{tokens(pool_tokens)} tokens, up to {tokens(n_ctx)} per slot"))
        L.append(row("per token", memory(kv.per_tok)))
        if d.shape:
            L.append(row("trained for", f"{tokens(d.shape['ctx_train'])} tokens per slot"))
    summary = f"{nslots} × {tokens(n_ctx)}   {percent(pool_fill(d))} used" if n_ctx else ""
    return Card("slots".upper(), summary, L)       # the card's name in capitals (SLOTS is also a setting's env name: rule R14)
