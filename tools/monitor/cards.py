"""The Live tab's cards (SLOTS, SPEED, MEMORY, CONNECT, HEALTH; in full detail also MODEL and LOG;
RECENT REQUESTS; SERVER when no server runs) and the state sentence above them, for the llama.cpp
server. Pure: they render a ServerData snapshot and a View; nothing here reads files, runs commands
or talks to the server. Words and units: reference/glossary.md (carl_core.domain.units)."""
from __future__ import annotations

import collections
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from carl_core.domain.gguf import KV_BPE, kv_bytes_per_token
from carl_core.domain.units import duration, file_size, memory, memory_pair, percent, speed, tokens

from .cacheapi import Client
from .fmt import B, CYN, DIM, GRN, MAG, R, RED, YEL, Card, CardLine, Ln, Row, bar, button_rows, cwrap, draw_card, home_short, lv, wrap
from .logbook import ROUTINE, TS, LogBook, RequestRecord, level_of, offset
from .model import ServerData, SlotInfo, SlowStats, flag, flag_int
from .words import kv_name, plural, spec_name

LEVEL_NAMES = ("slots", "speed", "memory", "connect", "health", "model", "requests", "log", "modelinfo")
NOLOG_TEXT = "No log file: the server writes to its terminal. Start it with ./carl.sh to see the log here."
REQ_HEAD = (f"{'started':8}  {'context':>8}  {'new tokens':>10}  {'read tok/s':>10}  {'output':>6}  "
            f"{'write tok/s':>11}  {'took':>6}  {'guesses OK':>10}")
STOPPED, LOADING, IDLE, READING, WRITING = "STOPPED", "LOADING", "IDLE", "READING", "WRITING"
GIB_NOTE = "GiB is memory: 1 GiB = 1.07 GB."


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
    clients: Tuple[Client, ...] = ()    # the other computers that sync (cacheapi.Client), newest first
    detail: str = "simple"              # simple | full
    drafter_size: int = 0               # the MTP drafter file the server loaded (-md), bytes
    model_quant: str = ""               # the catalogue's quantization of the loaded model
    here: str = ""                      # OpenCode and Pi on this Mac: "set up", "update needed", "not set up"
    next_start: List[CardLine] = field(default_factory=list)    # the SERVER card when no server runs (app.py)

    @property
    def full(self) -> bool:
        return self.detail == "full"

    def level(self, name: str) -> int:
        """A card's level: 0 collapsed, 1 open."""
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


def memory_sentence(pressure: str) -> str:
    """macOS memory pressure as a sentence ('' when unknown)."""
    return {"normal": "Memory is normal.", "WARNING": "Memory is low (macOS warns).",
            "CRITICAL": "Memory is very low: close apps, or use a smaller model."}.get(pressure, "")


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
    mem = memory_sentence(d.system.pressure)
    if label == STOPPED:
        return "The server stopped." if d.exited else "The server is not running."
    if label == LOADING:
        cur = d.router.current if d.router is not None else None
        what = f"The router loads {cur.id}" if cur else "The server starts: the model loads"
        return f"{what}. This takes 30 s to 2 min."
    if d.router is not None and not d.alias:
        return "The server runs in router mode. No model is loaded: OpenCode and Pi load one when they ask for it."
    if not d.slots:
        return f"A server runs on port {port}, but it does not answer like llama.cpp. CARL shows only some figures."
    busy = busy_slots(d)
    free = [x.id for x in d.slot_list if not x.busy and x.id is not None]
    parts: List[str] = []
    if len(busy) > 1:
        doing = [f"slot {x.id} {'writes an answer' if x.decoded else 'reads a prompt'}" for x in busy]
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
        parts.append("No request runs." + (f" The {n} slots are free." if n > 1 else ""))
    if (busy or d.busy) and free:
        parts.append(f"Slot {free[0]} is free." if len(free) == 1 else f"Slots {_slot_list(free)} are free.")
    if mem:
        parts.append(mem)
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
    """What a slot does, in words: writing, reading, free (keeps a session), free."""
    if x.busy:
        return f"{CYN}writing{R}" if x.decoded else f"{YEL}reading{R}"
    return "free (keeps a session)" if x.prompt + x.decoded else "free"


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


def card_slots(v: View, d: ServerData, w: int = 66) -> Card:
    """Each slot's fill and what it does; the busy request; in full detail the context memory, the
    recurrent state and the trained length."""
    kv = kv_info(d)
    cmd, sl, n_ctx = d.cmd, d.slot_list, d.n_ctx
    nslots = len(sl) or flag_int(cmd, "--parallel", "-np", default=1)
    bw = max(min(w - 51, 16), 6)
    L: List[CardLine] = []
    for i in range(nslots):
        x = sl[i] if i < len(sl) else None
        if x:
            u = x.prompt + x.decoded
            L.append(f"slot {x.id}  {bar(u / x.n_ctx if x.n_ctx else 0, bw)}  {tokens(u)} of {tokens(x.n_ctx)} · "
                     f"{slot_state(x)}")
        else:
            L.append(f"slot {i}  {bar(0, bw)}  0 of {tokens(n_ctx) if n_ctx else '–'}")
    req = request_line(d)
    if req:
        L += ["", *cwrap(req, w - 4)]
    swa = swa_line(d)
    if swa:
        L += cwrap(swa, w - 4)
    if v.full and kv:
        if kv.k != kv.v:
            L.append(f"{RED}The two context memory types differ: prompts read about 5 × more slowly.{R}")
        used = sum(x.prompt + x.decoded for x in sl)
        pool_tokens = flag_int(cmd, "-c", "--ctx-size", default=0)
        frac = used / pool_tokens if pool_tokens else 0
        L.append(f"context memory {MAG}{kv.k}{R} K · {MAG}{kv.v}{R} V: {memory(kv.kv)} for "
                 f"{plural(kv.slots, 'slot')}, {memory(kv.kv * frac)} in use")
        if kv.rs:
            L.append(f"recurrent state {memory(kv.rs)} · checkpoints up to {memory(kv.ckpt_max)} "
                     f"({kv.ckpt_n} per slot)")
        if n_ctx and pool_tokens and pool_tokens != n_ctx * nslots:
            L.append(f"one shared pool of {tokens(pool_tokens)} tokens; each slot can use up to {tokens(n_ctx)}")
        if d.shape:
            shp = d.shape
            L.append(f"{memory(kv.per_tok)} per token ({shp['attn_layers']} attention layers × {shp['kvh']} KV "
                     f"heads × ({shp['kl']} + {shp['vl']}) × {kv.k}) · RAM cache up to {memory(kv.cache_ram)}")
            L.append(f"The model was trained for {tokens(shp['ctx_train'])} tokens per slot.")
    summary = f"{nslots} × {tokens(n_ctx)}" if n_ctx else ""
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


def card_speed(v: View, d: ServerData) -> Card:
    """Write and read speed now and on average, the share of correct guesses; in full detail the last
    request, the guesses per step and position, the totals and the queue."""
    m = d.metrics
    pp_avg, tg_avg = averages(d)
    writing = d.busy and d.decoded > 0
    reading = d.busy and d.decoded == 0
    now_w = f"{speed(d.tg_rate)} now · " if writing and d.tg_rate else ""
    now_r = f"{speed(d.pp_rate)} now · " if reading and d.pp_rate else ""
    L: List[CardLine] = [
        lv("write", f"{now_w}{speed(tg_avg)} average" if tg_avg else now_w.rstrip(" ·") or f"{DIM}not measured yet{R}", 8),
        lv("read", f"{now_r}{speed(pp_avg)} average" if pp_avg else now_r.rstrip(" ·") or f"{DIM}not measured yet{R}", 8)]
    drafted = m.get("spec_decode_num_draft_tokens_total", 0)
    acc = m.get("spec_decode_num_accepted_tokens_total", 0)
    mode = flag(d.cmd, "--spec-type", default="none")
    if drafted:
        L.append(lv("speculation", f"{percent(acc / drafted)} of the guesses are correct", 13))
    else:
        L.append(lv("speculation", "off" if mode == "none" else f"{DIM}no guesses yet{R}", 13))
    if v.full:
        r = v.log.requests[-1] if v.log.requests else None
        if r:
            took = (r.t1 or 0) - (r.t0 or 0)
            L.append(f"last request: read {speed(r.pp)} · write {speed(r.tg)} · {duration(took)}")
        L.append(f"{DIM}The averages count every request since the server started.{R}")
        steps = m.get("spec_decode_num_drafts_total", 0)
        n = flag(d.cmd, "--spec-draft-n-max", default="1")
        if steps:
            L.append(f"{acc / steps:.2f} tokens accepted per step · {spec_name(mode, n)}")
        pos = d.accepted_by_pos
        if pos and steps:
            L.append("accepted by guess position: " + " · ".join(f"{i + 1}. {percent(pos[i] / steps)}"
                                                                for i in sorted(pos)[:4]))
        if d.slots:
            L.append(f"totals: {tokens(m.get('prompt_tokens_total', 0))} read · "
                     f"{tokens(m.get('prompt_tokens_cached_total', 0))} reused · "
                     f"{tokens(m.get('tokens_predicted_total', 0))} written")
            L.append(f"queue: {m.get('requests_deferred', 0):.0f} waiting · largest context so far "
                     f"{tokens(m.get('n_tokens_max', 0))} tokens")
    return Card("SPEED", "", L)


def card_memory(v: View, d: ServerData, w: int = 66) -> Card:
    """The server's memory: the total of RAM, its parts; in full detail this Mac's memory, swap, GPU,
    power, the GPU memory limit, CPU and disk."""
    kv = kv_info(d)
    s = d.system
    rss = d.rss or 0
    weights = v.model_size or 0
    ctx = (kv.kv + kv.rs) if kv else 0
    pc = GRN if s.pressure == "normal" else YEL if s.pressure == "WARNING" else RED
    L: List[CardLine] = []
    if rss:
        L.append(f"Server uses {memory_pair(rss, v.total_mem)} RAM. macOS memory pressure: {pc}{s.pressure}{R}.")
        L.append(bar(rss / v.total_mem if v.total_mem else 0, max(min(w - 16, 50), 10)))
        parts = [f"model {memory(weights)}"] + ([f"drafter {memory(v.drafter_size)}"] if v.drafter_size else [])
        parts += [f"context memory {memory(ctx)}", f"buffers {memory(max(rss - weights - v.drafter_size - ctx, 0))}"]
        L.append(" · ".join(parts))
    else:
        L.append(f"{DIM}No server process.{R}")
    if not v.full:
        L.append(f"{DIM}{GIB_NOTE}{R}")
        return Card("MEMORY", "", L)
    L.append(f"This Mac: {memory_pair(s.used, v.total_mem)} used (wired {memory(s.wired)} · compressed "
             f"{memory(s.comp)} · free {memory(s.free)})")
    su, st = s.swap
    swap = f"swap {memory_pair(su, st)}" if st else "swap 0"
    gpu = f"GPU {s.gpu}% busy" if s.gpu is not None else "GPU –"
    L.append(f"{swap} · {gpu} · {s.power or 'power –'} · thermal {v.slow.thermal or '–'}")
    lim = f"GPU memory limit {memory(v.gpu_limit[0])} ({v.gpu_limit[1]})" if v.gpu_limit else "GPU memory limit –"
    L.append(lim + (f" · GPU memory in use by all apps {memory(s.gpumem)}" if s.gpumem else ""))
    disk = f" · disk {file_size(v.slow.disk[0])} free of {file_size(v.slow.disk[1])}" if v.slow.disk else ""
    L.append(f"CPU: the server {d.cpu:.0f}% · load {s.load[0]:.1f} {s.load[1]:.1f} {s.load[2]:.1f}{disk}")
    return Card("MEMORY", "", L)


def reach_of(host: str) -> str:
    return "this Mac only" if host in ("127.0.0.1", "::1", "localhost") else "this Mac and other computers"


def card_connect(v: View, d: ServerData) -> Card:
    """The address, the key (hidden unless shown), the connections, OpenCode and Pi here, the disk
    cache; in full detail the model name, the key file and the copy buttons."""
    shown = v.key if v.key_shown else (("•" * 8 + v.key[-4:]) if v.key else f"{RED}no key file{R}")
    hosts = collections.Counter(h for h, _ in d.conns)
    L: List[CardLine] = [lv("Address", f"{B}{v.base}/v1{R}   {DIM}({reach_of(v.host)}){R}", 9),
                         Ln(lv("Key", f"{MAG}{shown}{R}   ", 9) + f"{DIM}{'hide' if v.key_shown else 'show'} (k){R}",
                            "key")]
    conns = plural(len(d.conns), "connection")
    L.append(conns + (f" · OpenCode and Pi {v.here}" if v.here else ""))
    L.append(f"Disk cache: {v.cache}" if v.cache else f"Disk cache: {DIM}empty{R}")
    if v.full:
        if d.alias:
            L.append(lv("Model", d.alias, 9))
        if hosts:
            L.append(lv("From", ", ".join(hosts), 9))
        L.append(lv("Key file", home_short(v.key_file, v.home), 9))
        L += button_rows(f"{DIM}{'Copy':<9}{R}", [("OpenCode config (o)", "opencode"), ("Pi config (p)", "pi"),
                                                  ("curl test (c)", "curl")], 50)
    return Card("CONNECT", "", L)


def log_counts(v: View) -> str:
    c = v.log.counts
    err = plural(c["E"], "error")
    warn = plural(c["W"], "warning")
    return (f"log: {RED + err + R if c['E'] else err} · {YEL + warn + R if c['W'] else warn} · "
            f"{plural(c['notice'], 'routine notice')} · GPU: {plural(c['oom'], 'out-of-memory error')}, "
            f"{plural(c['compute'], 'compute error')}")


def card_health(v: View, d: ServerData) -> Card:
    """One sentence: errors or none, and whether the Mac stays awake; in full detail the log counts,
    the /health time, the last errors and the sleep events."""
    c = v.log.counts
    broken = c["oom"] or c["compute"]
    awake = "The Mac stays awake while the server runs." if d.awake else \
        f"{YEL}The Mac can sleep while the server runs.{R}"
    if broken:
        first = f"{RED}{B}✗ The GPU failed (out of memory or a compute error). Restart the server: Settings > Server, a.{R}"
    elif c["E"]:
        first = f"{YEL}⚠ {plural(c['E'], 'error')} in the log (tab 4).{R}"
    else:
        first = f"{GRN}✓{R} No errors."
    L: List[CardLine] = [f"{first} {awake}"]
    if v.full:
        L.append(log_counts(v))
        if d.health_ms is not None:
            L.append(f"The server answers a health check in {d.health_ms:.0f} ms.")
        errs = list(v.log.errors)[-3:]
        if errs:
            L.append(f"{B}Last errors{R}")
            L += [f"{RED}{log_line(v.log, e)}{R}" for e in errs]
        sleeps = [e for e in v.slow.sleep_events if e[1] == "Sleep"]
        if v.slow.sleep_events:
            L.append(f"{B}Sleep and wake{R} {DIM}(this Mac: {plural(len(sleeps), 'sleep')} since it started){R}")
            L += [f"{DIM}{t} {kind}: {why}{R}" for t, kind, why in v.slow.sleep_events[-3:]]
    return Card("HEALTH", "", L)


def card_model(v: View, d: ServerData) -> Card:
    """Full detail: the loaded file, its weights (and the MTP drafter), speculation, the architecture,
    thinking, the batch, the process."""
    shp, cmd = d.shape, d.cmd
    drafter = flag(cmd, "-md")
    L: List[CardLine] = []
    if shp:
        weights = f"{memory(v.model_size)}" + (f" + MTP drafter {memory(v.drafter_size)}" if drafter else "")
        L.append(lv("file", os.path.basename(v.model_path or ""), 13))
        L.append(lv("weights", weights, 13))
        L.append(lv("speculation", f"{spec_name(flag(cmd, '--spec-type', default='none'), flag(cmd, '--spec-draft-n-max'))}"
                    f" {DIM}(--spec-type {flag(cmd, '--spec-type', default='none')}){R}", 13))
        mtp = "a separate MTP drafter" if drafter else (plural(shp['nextn'], "MTP layer") if shp["nextn"] else "no MTP")
        L.append(lv("layers", f"{shp['blocks']}: {shp['attn_layers']} attention, {shp['rec_layers']} recurrent · {mtp}",
                    13))
        L.append(lv("experts", f"{shp['experts_used']} of {shp['experts']} work on each token (MoE)"
                    if shp.get("experts") else "none (dense: all weights work)", 13))
        L.append(lv("thinking", ("levels and off" if shp.get("effort_levels") else "on / off")
                    + (f" {DIM}(CARL's chat template: a client can turn it off){R}" if "--chat-template-file" in cmd
                       else ""), 13))
        L.append(lv("server", f"batch {flag(cmd, '-ub', default='–')} · flash attention {flag(cmd, '-fa', default='–')} · "
                              f"pid {d.pid} · {shp['arch']}", 13))
    else:
        L.append(f"{DIM}The model file is not known: {'an unknown model' if d.pid else 'no server process'}.{R}")
    quant = v.model_quant or (shp["ftype"] if shp else "")
    return Card("MODEL", quant, L)


CardFn = Callable[[View, ServerData], Card]
CARDS: Dict[str, CardFn] = {"connect": card_connect, "speed": card_speed, "health": card_health, "model": card_model}


def column(v: View, names: List[str], d: ServerData, w: int) -> List[Row]:
    """The named cards drawn one under the other, w columns wide."""
    rows: List[Row] = []
    for nm in names:
        if nm == "slots":
            card = card_slots(v, d, w)
        elif nm == "memory":
            card = card_memory(v, d, w)
        else:
            card = CARDS[nm](v, d)
        rows += draw_card(nm, card.title, card.summary, wrapped(card.lines, w - 4), w, v.level(nm))
    return rows


def wrapped(lines: List[CardLine], w: int) -> List[CardLine]:
    """Card text wrapped to w columns (never cut); clickable lines stay as they are."""
    return [x for ln in lines for x in ([ln] if isinstance(ln, Ln) else cwrap(ln, w, "  "))]


def card_stopped(v: View, d: ServerData) -> Card:
    """No server runs: the state, the next action, what a start uses (v.next_start, from the app)."""
    L: List[CardLine] = ["The server stopped." if d.exited else "The server is not running.", ""]
    L += v.next_start
    return Card("SERVER", f"{RED}stopped{R}", L)


# ---------------------------------------------------------------- requests and log
def req_row(r: RequestRecord, wall: Callable[[Optional[float]], str]) -> str:
    """One request as a table row (REQ_HEAD); wall turns its start into a clock time."""
    took = r.t1 - r.t0 if r.t1 is not None and r.t0 is not None else None
    acc = percent(r.acc) if r.acc is not None else "–"
    return (f"{wall(r.t0):8}  {tokens(r.ctx):>8}  {tokens(r.new):>10}  {(r.pp or 0):>10.0f}  "
            f"{tokens(r.gen):>6}  {(r.tg or 0):>11.1f}  {duration(took):>6}  {acc:>10}"
            + (f"  {RED}error{R}" if r.error else ""))


def card_requests(v: View, d: ServerData, n: int) -> Card:
    """The last n finished requests, newest first."""
    reqs = list(v.log.requests)
    running = f" · {len(v.log.current)} running" if v.log.current else ""
    rows = [req_row(r, v.log.wall) for r in reversed(reqs[-n:])] or [f"{DIM}No finished request in this log yet.{R}"]
    return Card("RECENT REQUESTS", f"{len(reqs)} finished{running} {DIM}· tab 3 for all{R}",
                [f"{DIM}{REQ_HEAD}{R}", *rows])


def routine(line: str) -> bool:
    """A log line llama.cpp writes in normal work (logbook.ROUTINE)."""
    return any(r in line for r in ROUTINE)


def log_line(book: LogBook, line: str) -> str:
    """A log line with its clock time (the log's own clock is minutes since the server started)."""
    m = TS.match(line)
    if not m or not book.start:
        return line
    return f"{book.wall(offset(line))} {m.group(5)} {line[m.end():]}"


def log_view(v: View, n: int, width: int) -> List[CardLine]:
    """The last n lines of the log (fewer, wrapped, when wrapping), with clock times; errors red,
    warnings yellow, the lines that are normal at a start dim."""
    if not v.log_path:
        return [f"{DIM}{NOLOG_TEXT}{R}"]
    lines = [ln for ln in v.log.lines if not v.errors_only or (level_of(ln) in ("E", "W") and not routine(ln))]
    end = len(lines) - v.log_scroll
    out: List[CardLine] = []
    for line in lines[max(0, end - n * (4 if v.wrap else 1)):end]:
        lvl = level_of(line)
        col = DIM if routine(line) else RED if lvl == "E" else YEL if lvl == "W" else ""
        text = log_line(v.log, line)
        if not v.wrap:
            out.append(col + text)
        else:
            out += [col + part for part in wrap(text, width)]
    return out[-n:] or [f"{DIM}(no log lines){R}"]

