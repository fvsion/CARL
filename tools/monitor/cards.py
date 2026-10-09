"""The Live tab's cards (SLOTS, SPEED, MEMORY, CONNECT, HEALTH; in full detail also MODEL and LOG;
RECENT REQUESTS; SERVER when no server runs) and the state sentence above them, for the llama.cpp
server. Pure: they render a ServerData snapshot and a View; nothing here reads files, runs commands
or talks to the server. Words and units: reference/glossary.md (carl_core.domain.units)."""
from __future__ import annotations

import collections
import os
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from carl_core.domain.gguf import KV_BPE, kv_bytes_per_token
from carl_core.domain.llamacpp import VersionCheck, health_rows
from carl_core.domain.units import duration, file_size, memory, memory_pair, percent, speed, tokens

from .cacheapi import Client
from .fmt import (B, CYN, DIM, GRN, MAG, R, RED, YEL, Card, CardLine, Ln, Row, aligned, bar, button_rows, cwrap,
                  draw_card, home_short, row, wrap)
from .logbook import ROUTINE, TS, LogBook, RequestRecord, level_of, offset
from .model import ServerData, SlotInfo, SlowStats, flag, flag_int
from .words import plural, spec_name

LEVEL_NAMES: List[str] = ["slots", "speed", "memory", "thismac", "connect", "health", "model", "requests", "log",
                          "server", "modelinfo", "srv", "srvmem", "srvfit", "srvabout", "srvlist", "mlist", "msel",
                          "reqtab", "logtab"]
THREE_LEVELS = {"slots", "speed", "memory", "thismac", "connect", "health", "model", "modelinfo", "srv", "srvmem",
                "mlist", "msel", "requests", "log", "reqtab", "logtab"}
NO_COLLAPSE = {"reqtab", "logtab"}      # a tab with one section: simple and full only


def sections(three: Sequence[str] = (), two: Sequence[str] = ()) -> None:
    """Register a module's sections (at import, before the UI state exists): three = collapsed / simple / full,
    two = collapsed / open. Their level is then saved, set by D and cycled by L like Live's."""
    for nm in (*three, *two):
        if nm not in LEVEL_NAMES:
            LEVEL_NAMES.append(nm)
    THREE_LEVELS.update(three)   # collapsed / simple / full
NOLOG_TEXT = "No log file: the server writes to its terminal. Start it with ./carl.sh to see the log here."
REQ_HEAD = (f"{'Started':8}  {'Context':>8}  {'New tokens':>10}  {'Read tok/s':>10}  {'Output':>6}  "
            f"{'Write tok/s':>11}  {'Took':>6}  {'Guesses OK':>10}")
STOPPED, LOADING, IDLE, READING, WRITING = "STOPPED", "LOADING", "IDLE", "READING", "WRITING"


@dataclass
class View:
    """Everything the cards show besides the snapshot: the detail level, card levels (open or
    collapsed), the connection, the log, and what the app worked out (the disk cache, the client
    setup, the next start)."""
    levels: Dict[str, int]
    host: str
    port: int
    base: str
    key: str
    key_file: str
    key_shown: bool
    server_pid: Optional[int]           # the launcher's server (--server-pid)
    log: LogBook
    log_path: Optional[str]
    model_path: Optional[str]           # the GGUF the server loaded
    model_size: Optional[int]
    gpu_limit: Optional[Tuple[int, str]]
    slow: SlowStats
    total_mem: int
    home: str
    wrap: bool = False                  # log lines wrapped
    errors_only: bool = False
    log_scroll: int = 0                 # lines back from the end
    level_override: Dict[str, int] = field(default_factory=dict)
    cache: str = ""                     # what the disk cache holds (tools/monitor/diskcache.py)
    api: str = ""                       # the dashboard API for other computers (host:port; "" when not running)
    listeners: int = 0                  # other computers holding its config event stream (their sync service)
    pushed: str = ""                    # the last config sent: "VERSION at TIME"
    unsent: bool = False                # the config CARL would send now differs from the last one sent (23.4.4)
    single: str = ""                    # single-model mode: the model the server runs (the configs list only it)
    clients: Tuple[Client, ...] = ()    # the other computers that sync (cacheapi.Client), newest first
    detail: str = "simple"              # simple | full
    drafter_size: int = 0               # the MTP drafter file the server loaded (-md), bytes
    model_quant: str = ""               # the catalogue's quantization of the loaded model
    here: str = ""                      # OpenCode and Pi on this Mac: "set up", "update needed", "not set up"
    coder_here: str = ""                # the coder's model on this Mac: "main", PROVIDER/MODEL, "" (not set up; 23.4.5)
    next_start: List[CardLine] = field(default_factory=list)    # the SERVER card when no server runs (app.py)
    reuse_from: str = ""                # where the busy request's reused tokens came from (Phase 23.5; mocked now)
    selected: str = ""                  # the selected section (Tab): its title is drawn reversed
    gate: int = 0                       # config.json delegation.gate: carl-delegation's new-file gate (0 = off)
    llama: Optional[VersionCheck] = None    # the installed llama.cpp against the tested version (HEALTH)

    @property
    def full(self) -> bool:
        return self.detail == "full"

    def level(self, name: str) -> int:
        """A section's level: 0 collapsed, 1 simple, 2 full (two-level sections: 0 or 1)."""
        return self.level_override.get(name, self.levels.get(name, 1))


# ---------------------------------------------------------------- the state
def busy_slots(d: ServerData) -> List[SlotInfo]:
    return [x for x in d.slot_list if x.busy]


def status_of(d: ServerData, server_pid: Optional[int]) -> Tuple[str, str]:
    """(word, pill background) for the header: STOPPED, LOADING, IDLE, READING, WRITING, BUSY ×N."""
    if d.exited or (not d.up and not d.pid):
        return STOPPED, "41"
    if not d.up:
        return LOADING, "43"
    if d.router is not None and not d.alias:
        cur = d.router.current
        return (LOADING, "43") if cur and cur.status == "loading" else (IDLE, "42")
    n = len(busy_slots(d)) or (1 if d.busy else 0)
    if not d.slots or n == 0:
        return IDLE, "42"
    if n > 1:
        return f"BUSY ×{n}", "46"
    return (READING, "43") if d.decoded == 0 else (WRITING, "46")


def _slot_list(ids: List[int]) -> str:
    names = [str(i) for i in ids]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def read_left(d: ServerData) -> int:
    """Prompt tokens of the busy request still to read."""
    return max(d.prompt - d.cached - d.processed, 0)


def live_sentence(d: ServerData, port: int) -> str:
    """The Live tab's first line: the state in one plain sentence (the next action when stopped is in the
    SERVER card)."""
    label, _ = status_of(d, None)
    if label == STOPPED:
        return "The server stopped." if d.exited else "The server is not running."
    if label == LOADING:
        cur = d.router.current if d.router is not None else None
        what = f"The router is loading {cur.id}" if cur else "The server is starting: the model is loading"
        return f"{what}. This takes 30 s to 2 min."
    if d.router is not None and not d.alias:
        bad = [m.id for m in d.router.models if m.failed]
        if bad:
            return f"The router could not load {bad[0]}: the log (tab 4) says why. OpenCode and Pi can ask for another model."
        return "The server is running in router mode. No model is loaded: OpenCode and Pi load one when they ask for it."
    if not d.slots:
        return f"A server runs on port {port}, but it does not answer like llama.cpp. CARL shows only some figures."
    busy = busy_slots(d)
    free = [x.id for x in d.slot_list if not x.busy and x.id is not None]
    parts: List[str] = []
    if len(busy) > 1:
        doing = [f"slot {x.id} {'is writing an answer' if x.decoded else 'is reading a prompt'}" for x in busy]
        parts.append("Busy: " + ", ".join(doing) + ".")
    elif busy or d.busy:
        sid = busy[0].id if busy else 0
        if d.decoded == 0:
            done = d.cached + d.processed
            left = read_left(d)
            eta = f", about {duration(left / d.pp_rate)} left" if d.pp_rate else ""
            rate = f" at {speed(d.pp_rate)}" if d.pp_rate else ""
            parts.append(f"Reading a prompt in slot {sid}{rate}: {percent(done / d.prompt if d.prompt else 0)} "
                         f"done{eta}.")
        else:
            parts.append(f"Writing an answer in slot {sid}" + (f" at {speed(d.tg_rate)}." if d.tg_rate else "."))
    else:
        n = len(d.slot_list)
        parts.append("No request is running." + (f" The {n} slots are free." if n > 1 else ""))
    if (busy or d.busy) and free:
        parts.append(f"Slot {free[0]} is free." if len(free) == 1 else f"Slots {_slot_list(free)} are free.")
    return " ".join(parts)


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


def slot_word(x: SlotInfo) -> str:
    """What a slot does: writing, reading, between turns, free."""
    if x.busy:
        return f"{CYN}writing{R}" if x.decoded else f"{YEL}reading{R}"
    return f"{DIM}between turns{R}" if x.prompt + x.decoded else f"{DIM}free{R}"


def pool_fill(d: ServerData) -> float:
    """The share of the context the slots hold now (0..1)."""
    total = sum(x.n_ctx for x in d.slot_list)
    return sum(x.prompt + x.decoded for x in d.slot_list) / total if total else 0.0


def card_slots(v: View, d: ServerData, w: int = 66) -> Card:
    """Each slot's fill and what it does; the busy request; at full the context memory, the recurrent state,
    the checkpoints, how far it can grow and the trained length."""
    kv = kv_info(d)
    cmd, sl, n_ctx = d.cmd, d.slot_list, d.n_ctx
    nslots = len(sl) or flag_int(cmd, "--parallel", "-np", default=1)
    narrow = w - 4 < 56                        # the title has the size: a narrow card shows only the fill
    bw = max(min(w - 4 - (34 if narrow else 42), 24), 6)   # "slot 0  " bar "  41.5K of 96K   between turns"
    L: List[CardLine] = []
    for i in range(nslots):
        x = sl[i] if i < len(sl) else None
        u = (x.prompt + x.decoded) if x else 0
        cap = x.n_ctx if x else n_ctx
        size = f"{tokens(u):>6}" if narrow else f"{tokens(u):>6} of {tokens(cap) if cap else '–'}"
        L.append(f"slot {x.id if x else i}  {bar(u / cap if cap else 0, bw)}  {size}   {slot_word(x) if x else ''}")
    if not d.slots and not sl:
        L = [f"{DIM}No model is loaded.{R}"]
    busy = [x for x in sl if x.busy] or ([SlotInfo(0, True, d.task, n_ctx, d.prompt, d.cached, d.processed, d.decoded)]
                                         if d.busy else [])
    for x in busy:                       # one block per busy slot: its own request
        if not x.prompt:
            continue
        L.append("")
        L.append(f"{B}slot {x.id}{R}   {slot_word(x)}")
        L.append(row("request", f"{tokens(x.prompt)} tokens"))
        L.append(row("reused", f"{tokens(x.cached)} ({percent(x.cached / x.prompt)})"
                     + (f"   {DIM}from{R} {v.reuse_from}" if v.reuse_from else "")))
        if x.decoded == 0:
            L.append(row("read", tokens(x.processed)))
            L.append(row("still to read", tokens(max(x.prompt - x.cached - x.processed, 0))))
        else:
            L.append(row("written", plural(x.decoded, "token")))
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
        L.append(row("can grow to", memory(kv.kv + kv.rs + kv.ckpt_max + kv.cache_ram)))
        if n_ctx and pool_tokens and pool_tokens != n_ctx * nslots:
            L.append(row("shared pool", f"{tokens(pool_tokens)} tokens, up to {tokens(n_ctx)} per slot"))
        L.append(row("per token", memory(kv.per_tok)))
        if d.shape:
            L.append(row("trained for", f"{tokens(d.shape['ctx_train'])} tokens per slot"))
    summary = f"{nslots} × {tokens(n_ctx)}   {percent(pool_fill(d))} used" if n_ctx else ""
    return Card("SLOTS", summary, L)


AVG_MIN_S = 0.5         # seconds of reading or writing before an average shows


def averages(d: ServerData) -> Tuple[float, float]:
    """(read, write) tok/s since the server started (0: not enough time behind it: one 1-token
    answer measures ~1 µs, 1,000,000 tok/s)."""
    m = d.metrics
    pps, tgs = m.get("prompt_seconds_total", 0), m.get("tokens_predicted_seconds_total", 0)
    pp_avg = m.get("prompt_tokens_total", 0) / pps if pps >= AVG_MIN_S else 0
    tg_avg = m.get("tokens_predicted_total", 0) / tgs if tgs >= AVG_MIN_S else 0
    return pp_avg, tg_avg


def _num(x: float, decimals: int = 0) -> str:
    return "–" if not x else f"{x:,.{decimals}f}"


def card_speed(v: View, d: ServerData, w: int = 66) -> Card:
    """A table: read and write tok/s now, on average and in the last request; the share of correct guesses. At
    full also the tokens per step, the guesses by position, the totals and the queue."""
    m = d.metrics
    pp_avg, tg_avg = averages(d)
    writing = d.busy and d.decoded > 0
    reading = d.busy and d.decoded == 0
    r = v.log.requests[-1] if v.log.requests else None
    # the table's label column lines up with the rows below it (aligned(): the longest label + 2)
    lw = len("largest context" if v.full else "guesses OK") + 2
    last = "Last" if w - 4 < 50 else "Last request"            # a narrow card: the short header
    lc = len(last) + 3
    head = f"{DIM}{'Tok/s':<{lw}}{'Now':>7}{'Average':>10}{last:>{lc}}{R}"
    L: List[CardLine] = [
        head,
        f"{DIM}{'Read':<{lw}}{R}{_num(d.pp_rate if reading else 0):>7}{_num(pp_avg):>10}{_num(r.pp if r else 0):>{lc}}",
        f"{DIM}{'Write':<{lw}}{R}{_num(d.tg_rate if writing else 0, 1):>7}{_num(tg_avg, 1):>10}"
        f"{_num(r.tg if r else 0, 1):>{lc}}"]
    drafted = m.get("spec_decode_num_draft_tokens_total", 0)
    acc = m.get("spec_decode_num_accepted_tokens_total", 0)
    mode = flag(d.cmd, "--spec-type", default="none")
    if drafted:
        L.append(row("guesses OK", percent(acc / drafted)))
    else:
        L.append(row("guesses OK", "off" if mode == "none" else f"{DIM}no guesses yet{R}"))
    if v.full:
        steps = m.get("spec_decode_num_drafts_total", 0)
        n = flag(d.cmd, "--spec-draft-n-max", default="1")
        L.append(row("speculation", spec_name(mode, n)))
        if steps:
            L.append(row("per step", f"{acc / steps:.2f} tokens accepted"))
        pos = d.accepted_by_pos
        if pos and steps:
            ks = sorted(pos)[:4]
            L.append(f"{DIM}{'Guess':<{lw}}{R}" + "".join(f"{i + 1:>7}" for i in ks))
            L.append(f"{DIM}{'Accepted':<{lw}}{R}" + "".join(f"{percent(pos[i] / steps):>7}" for i in ks))
        if r:
            took = (r.t1 or 0) - (r.t0 or 0)
            L.append(row("last request", duration(took)))
        if d.slots:
            L.append("")
            L.append(row("total read", tokens(m.get("prompt_tokens_total", 0))))
            L.append(row("total reused", tokens(m.get("prompt_tokens_cached_total", 0))))
            L.append(row("total written", tokens(m.get("tokens_predicted_total", 0))))
            L.append(row("waiting", f"{m.get('requests_deferred', 0):.0f}"))
            L.append(row("largest context", f"{tokens(m.get('n_tokens_max', 0))} tokens"))
    return Card("SPEED", f"write {speed(tg_avg)}" if tg_avg else "", L)


def server_memory(v: View, d: ServerData) -> Tuple[int, int]:
    """(total, own) bytes of the server. own: its physical footprint (the Metal buffers are in it), its RSS when the
    footprint can't be read. total: own + the mapped model and drafter files (not in the footprint) once a model is
    loaded. With --no-mmap the weights are in the footprint already."""
    own = d.footprint or d.rss or 0
    loaded = d.up and not (d.router is not None and not d.alias)
    mapped = (v.model_size or 0) + v.drafter_size if own and loaded and "--no-mmap" not in d.cmd.split() else 0
    return own + mapped, own


def card_memory(v: View, d: ServerData, w: int = 66) -> Card:
    """The server's memory: the total with a bar, then its parts (they add up to the total); at full the server
    process alone, the GPU memory limit, the GPU memory of all apps and the server's CPU."""
    kv = kv_info(d)
    s = d.system
    total, own = server_memory(v, d)
    weights = v.model_size or 0
    ctx = (kv.kv + kv.rs) if kv else 0
    L: List[CardLine] = []
    if total:
        bw = max(min(w - 38, 30), 8)
        share = total / v.total_mem if v.total_mem else 0
        L.append(row("server", f"{bar(share, bw)}  {memory_pair(total, v.total_mem)}"))
        if not d.up:                                     # the model loads: no parts yet (not "model 0 KiB")
            L.append(row("model", f"{YEL}loading{R}"))
        elif d.router is not None and not d.alias:       # a router with no model loaded
            L.append(row("model", f"{DIM}none loaded{R}"))
        else:
            L.append(row("model", f"{memory(weights):>9}" if weights else f"{'–':>9}"))   # (size not known)
            if v.drafter_size:
                L.append(row("drafter", f"{memory(v.drafter_size):>9}"))
            L.append(row("context memory", f"{memory(ctx):>9}"))
            L.append(row("buffers", f"{memory(max(total - weights - v.drafter_size - ctx, 0)):>9}"))
    else:
        L.append(f"{DIM}No server process.{R}")
    if v.full:
        L.append("")
        if d.footprint and total != own:                 # the process without the mapped files (Activity Monitor's)
            L.append(row("server process", memory(own)))
        if v.gpu_limit:
            L.append(row("GPU limit", memory(v.gpu_limit[0]) + ("" if "Metal" in v.gpu_limit[1] else
                                                                  f"   {DIM}{v.gpu_limit[1]}{R}")))
        if s.gpumem:
            L.append(row("GPU, all apps", memory(s.gpumem)))
        L.append(row("server CPU", f"{d.cpu:.0f}%"))
    return Card("MEMORY", f"server {memory(total)}" if total else "", L)


HEAT = {"nominal": "normal", "fair": "warm", "serious": "hot", "critical": "very hot"}


def card_thismac(v: View, d: ServerData, w: int = 66) -> Card:
    """This Mac: its memory with a bar (as Activity Monitor's Memory Used), the memory pressure, swap, the GPU with a
    bar, power and heat; at full the parts of the memory, the cached files, the load and the free disk. Shown with or
    without a server."""
    s = d.system
    bw = max(min(w - 38, 30), 8)
    pc = GRN if s.pressure == "normal" else YEL if s.pressure == "WARNING" else RED
    L: List[CardLine] = []
    if v.total_mem:
        L.append(row("memory", f"{bar(s.used / v.total_mem, bw)}  {memory_pair(s.used, v.total_mem)}"))
    L.append(row("pressure", f"{pc}{s.pressure.lower() if s.pressure != '?' else '–'}{R}"))
    su, st = s.swap
    L.append(row("swap", memory_pair(su, st) if st else f"{DIM}none (no swap file){R}"))
    if s.gpu is not None:
        L.append(row("GPU", f"{bar(s.gpu / 100, bw)}  {s.gpu}% busy"))
    else:
        L.append(row("GPU", "–"))
    batt = "battery" in (s.power or "").lower()
    L.append(row("power", f"{YEL}{s.power}{R}" if batt else (s.power or "–")))
    th = v.slow.thermal or ""
    hc = "" if th in ("", "nominal") else YEL if th == "fair" else RED
    L.append(row("heat", f"{hc}{HEAT.get(th, th or '–')}{R}" if th else "–"))
    if v.full:
        L.append("")
        L.append(row("app memory", f"{memory(s.app):>9}"))           # the three parts of the memory row
        L.append(row("wired", f"{memory(s.wired):>9}"))
        L.append(row("compressed", f"{memory(s.comp):>9}"))
        L.append(row("cached files", f"{memory(s.cached):>9}"))       # not in the memory row: macOS frees them
        L.append(row("free", f"{memory(s.free):>9}"))
        L.append(row("load", f"{s.load[0]:.1f}   {s.load[1]:.1f}   {s.load[2]:.1f}   {DIM}(1, 5, 15 min){R}"))
        if v.slow.disk:
            L.append(row("disk free", f"{file_size(v.slow.disk[0])} of {file_size(v.slow.disk[1])}"))
    if s.gpu is not None and s.gpu >= 90:
        summary = f"{RED}GPU {s.gpu}%{R}"
    elif s.pressure not in ("normal", "?"):
        summary = f"{pc}pressure {s.pressure.lower()}{R}"
    else:
        summary = f"pressure {s.pressure.lower()}" if s.pressure != "?" else ""
    return Card("THIS MAC", summary, L)


def reach_of(host: str) -> str:
    return "this Mac only" if host in ("127.0.0.1", "::1", "localhost") else "this Mac and other computers"


def card_connect(v: View, d: ServerData, w: int = 66) -> Card:
    """The address, the key (hidden unless shown), the connections, OpenCode and Pi here, the disk cache; at
    full the model name, the hosts, the key file and the copy buttons."""
    shown = v.key if v.key_shown else (("•" * 8 + v.key[-4:]) if v.key else f"{RED}no key file{R}")
    hosts = collections.Counter("this Mac" if h in ("127.0.0.1", "::1") else h for h, _ in d.conns)
    L: List[CardLine] = [row("address", f"{B}{v.base}/v1{R}"),
                         Ln(row("key", f"{MAG}{shown}{R}   ") + f"{DIM}{'hide' if v.key_shown else 'show'} (k){R}",
                            "key")]
    other = [h for h in hosts if h != "this Mac"]
    L.append(row("connections", str(len(d.conns)) + (f"   {DIM}from{R} {', '.join(hosts)}" if other else "")))
    if v.here:
        L.append(row("OpenCode, Pi", v.here))
    used, _, saved = (v.cache or "").partition("   ")            # "2.4 of 10 GB   12 prompts, 5 sessions"
    L.append(row("disk cache", used or f"{DIM}empty{R}"))
    if v.full:
        L.append("")
        if saved:
            L.append(row("saved", saved))
        if d.alias:
            L.append(row("model name", d.alias))
        if hosts and not other:
            L.append(row("from", ", ".join(hosts)))
        L.append(row("key file", home_short(v.key_file, v.home)))
        L += button_rows(f"{DIM}Copy{R}  ", [("OpenCode (o)", "opencode"), ("Pi (p)", "pi"), ("curl (c)", "curl")], w - 4)
    return Card("CONNECT", reach_of(v.host), L)


def card_health(v: View, d: ServerData) -> Card:
    """Errors and warnings in the log (or none), whether the Mac stays awake, a warning when llama.cpp is older than
    the tested version (carl_core/domain/llamacpp.py; at full also the same or a newer version); at full the counts,
    the /health time, the last errors and the sleep events."""
    c = v.log.counts
    broken = c["oom"] or c["compute"]
    if broken:
        first = f"{RED}{B}✗ The GPU failed. Restart the server (Settings > Server, a).{R}"
    elif c["E"] or c["W"]:
        first = (f"{YEL}⚠ {plural(c['E'], 'error')}, {plural(c['W'], 'warning')}{R}   {DIM}tab 4{R}")
    else:
        first = f"{GRN}✓{R} No errors."
    L: List[CardLine] = [row("log", first),
                         row("sleep", "The Mac stays awake." if d.awake else f"{YEL}The Mac can sleep.{R}")]
    old_llama = v.llama is not None and v.llama.state == "older"
    for label, text, warn in health_rows(v.llama, v.full) if v.llama else ():
        L.append(row(label, f"{YEL}⚠ {text}{R}" if warn else text))
    if v.full:
        L.append("")
        L.append(row("errors", str(c["E"])))
        L.append(row("warnings", str(c["W"])))
        L.append(row("routine notices", str(c["notice"])))
        L.append(row("GPU errors", f"{c['oom']} out of memory, {c['compute']} compute"))
        if d.health_ms is not None:
            L.append(row("health check", f"{d.health_ms:.0f} ms"))
        errs = list(v.log.errors)[-3:]
        if errs:
            L.append(f"{B}Last errors{R}")
            L += [f"{RED}{log_line(v.log, e)}{R}" for e in errs]
        if v.slow.sleep_events:
            sleeps = [e for e in v.slow.sleep_events if e[1] == "Sleep"]
            L.append(row("sleeps", f"{len(sleeps)} since the Mac started"))
            L += [row(t, f"{kind}: {why}") for t, kind, why in v.slow.sleep_events[-3:]]
    summary = (f"{RED}GPU failed{R}" if broken else f"{YEL}{plural(c['E'] + c['W'], 'problem')}{R}" if c["E"] or c["W"]
               else f"{YEL}old llama.cpp{R}" if old_llama else "no errors")
    return Card("HEALTH", summary, L)


def card_model(v: View, d: ServerData) -> Card:
    """The loaded file and its speculation; at full the weights, the MTP drafter, the layers, the experts,
    thinking, the batch and the process."""
    shp, cmd = d.shape, d.cmd
    drafter = flag(cmd, "-md")
    L: List[CardLine] = []
    if shp:
        L.append(row("file", os.path.basename(v.model_path or "")))
        L.append(row("speculation", spec_name(flag(cmd, '--spec-type', default='none'), flag(cmd, '--spec-draft-n-max'))))
        if v.full:
            L.append("")
            L.append(row("weights", memory(v.model_size)))
            if drafter:
                L.append(row("MTP drafter", f"{os.path.basename(drafter)}, {memory(v.drafter_size)}"))
            L.append(row("layers", f"{shp['blocks']}: {shp['attn_layers']} attention, {shp['rec_layers']} recurrent"))
            L.append(row("MTP", "a separate drafter" if drafter else
                         (plural(shp['nextn'], "MTP layer") if shp["nextn"] else "none")))
            L.append(row("experts", f"{shp['experts_used']} of {shp['experts']} per token (MoE)"
                         if shp.get("experts") else "none (dense)"))
            L.append(row("thinking", "levels and off" if shp.get("effort_levels") else "on / off"))
            L.append(row("batch", flag(cmd, '-ub', default='–')))
            L.append(row("flash attention", flag(cmd, '-fa', default='–')))
            L.append(row("architecture", shp["arch"]))
            L.append(row("process", f"pid {d.pid}"))
    else:
        L.append(f"{DIM}The model file is not known: {'an unknown model' if d.pid else 'no server process'}.{R}")
    quant = v.model_quant or (shp["ftype"] if shp else "")
    return Card("MODEL", quant, L)


CardFn = Callable[[View, ServerData], Card]
CARDS: Dict[str, CardFn] = {"health": card_health, "model": card_model}
SIZED = {"slots": card_slots, "memory": card_memory, "thismac": card_thismac, "connect": card_connect, "speed": card_speed}       # these take the card's width


def column(v: View, names: List[str], d: ServerData, w: int) -> List[Row]:
    """The named cards drawn one under the other, w columns wide, each at its own level (its title shows it)."""
    rows: List[Row] = []
    for nm in names:
        lvl = v.level(nm)
        vv = replace(v, detail="full" if lvl == 2 else "simple")
        card = SIZED[nm](vv, d, w) if nm in SIZED else CARDS[nm](vv, d)
        rows += draw_card(nm, card.title, card.summary, wrapped(aligned(card.lines, w - 4), w - 4), w, lvl,
                          3 if nm in THREE_LEVELS else 2, nm == v.selected)
    return rows


def wrapped(lines: List[CardLine], w: int) -> List[CardLine]:
    """Card text wrapped to w columns (never cut); clickable lines stay as they are."""
    return [x for ln in lines for x in ([ln] if isinstance(ln, Ln) else cwrap(ln, w, "  "))]


def card_stopped(v: View, d: ServerData) -> Card:
    """No server runs: the state, the next action, what a start uses (v.next_start, from the app)."""
    failed = any("start failed" in str(x) for x in v.next_start)
    L: List[CardLine] = [row("state", f"{RED}stopped{R}" + (". The last start failed." if failed else "")), *v.next_start]
    return Card("SERVER", f"{RED}stopped{R}", L)


# ---------------------------------------------------------------- requests and log
REQ_MORE = f"  {'Reused':>8}"                      # the Requests tab and the full Live card add these columns
REQ_FULL = f"  {'Slot':>4}  {'Reused from':<12}"


def req_row(r: RequestRecord, wall: Callable[[Optional[float]], str]) -> str:
    """One request as a table row (REQ_HEAD); wall turns its start into a clock time."""
    took = r.t1 - r.t0 if r.t1 is not None and r.t0 is not None else None
    acc = f"{RED}{'error':>10}{R}" if r.error else f"{percent(r.acc) if r.acc is not None else '–':>10}"
    return (f"{wall(r.t0):8}  {tokens(r.ctx):>8}  {tokens(r.new):>10}  {(r.pp or 0):>10.0f}  "
            f"{tokens(r.gen):>6}  {(r.tg or 0):>11.1f}  {duration(took):>6}  {acc}")


def req_table(reqs: List[RequestRecord], wall: Callable[[Optional[float]], str], full: bool, w: int,
              mean: bool = False) -> List[CardLine]:
    """The requests as a table: a header row, an optional "mean" row under the speed columns, one row each (newest
    first as given); full adds the slot and where the reused tokens came from, as columns, or on a second aligned row
    when w is too narrow for them."""
    wide = len(REQ_HEAD + REQ_MORE + REQ_FULL) <= w
    L: List[CardLine] = [f"{DIM}{REQ_HEAD}{REQ_MORE}{REQ_FULL if full and wide else ''}{R}"]
    done = [r for r in reqs if r.tg]
    if mean and done:
        pp = sum(r.pp or 0 for r in done) / len(done)
        tg = sum(r.tg or 0 for r in done) / len(done)
        L.append(f"{DIM}{'Mean':8}  {'':>8}  {'':>10}  {R}{pp:>10.0f}  {'':>6}  {tg:>11.1f}")
    for r in reqs:
        line = req_row(r, wall) + f"  {tokens(max(r.ctx - r.new - r.gen, 0)):>8}"
        extra = f"{r.slot:>4}  {r.source or '–':<12}"
        if full and wide:
            line += "  " + extra
        L.append(line)                    # a failed request says "error" in its guesses column
        if full and not wide:
            L.append(f"{DIM}{'':8}  Slot {r.slot}.   Reused from: {r.source or '–'}.{R}")
    return L


def card_requests(v: View, d: ServerData, n: int, w: int = 200) -> Card:
    """The last n finished requests, newest first (the full level adds the slot and the source)."""
    reqs = list(v.log.requests)
    running = f", {len(v.log.current)} running" if v.log.current else ""
    rows = req_table(list(reversed(reqs[-n:])), v.log.wall, v.full, w) if reqs else \
        [f"{DIM}No finished request in this log yet.{R}"]
    return Card("RECENT REQUESTS", f"{len(reqs)} finished{running}", rows)


def routine(line: str) -> bool:
    """A log line llama.cpp writes in normal work (logbook.ROUTINE)."""
    return any(r in line for r in ROUTINE)


def log_line(book: LogBook, line: str, ms: bool = False) -> str:
    """A log line with its clock time (the log's own clock is minutes since the server started; ms: with
    milliseconds)."""
    m = TS.match(line)
    if not m or not book.start:
        return line
    return f"{book.wall(offset(line), ms)} {m.group(5)} {line[m.end():]}"


def log_view(v: View, n: int, width: int) -> List[CardLine]:
    """The last n lines of the log (fewer, wrapped, when wrapping), with clock times; errors red,
    warnings yellow, the lines that are normal at a start dim."""
    if not v.log_path:
        return [f"{DIM}{NOLOG_TEXT}{R}"]
    lines = [ln for ln in v.log.lines if not v.errors_only or level_of(ln) in ("E", "W")]   # errors and warnings, all
    end = len(lines) - v.log_scroll
    out: List[CardLine] = []
    for line in lines[max(0, end - n * (4 if v.wrap else 1)):end]:
        lvl = level_of(line)
        col = DIM if routine(line) else RED if lvl == "E" else YEL if lvl == "W" else ""
        text = log_line(v.log, line, v.full)
        if not v.wrap:
            out.append(col + text)
        else:
            out += [col + part for part in wrap(text, width)]
    return out[-n:] or [f"{DIM}(no log lines){R}"]

