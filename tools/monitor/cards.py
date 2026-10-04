"""The Overview cards (CONNECT, CONTEXT, MEMORY, ACTIVITY, MODEL, HEALTH, SYSTEM, recent
requests, log) for the llama.cpp server. Pure: they render a ServerData snapshot and a
View; nothing here reads files, runs commands or talks to the server."""
from __future__ import annotations

import collections
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .fmt import (B, CYN, DIM, GRN, MAG, NA, R, RED, YEL, Card, CardLine, Ln, Row, bar, buttons, draw_card, dur,
                  home_short, knum, lv, size, wrap)
from .gguf import KV_BPE, kv_bytes_per_token
from .logbook import TS, LogBook, RequestRecord, level_of
from .model import ServerData, SlowStats, flag, flag_int

LEVEL_NAMES = ("connect", "context", "memory", "activity", "model", "health", "system", "requests", "log", "modelinfo")
NOLOG_TEXT = "no log file: the server writes to the terminal that started it (start it with ./carl.sh to see it here)"
REQ_HEAD = (f"{'started':8}  {'context':>8}  {'new':>7}  {'read/s':>6}  {'output':>6}  {'gen/s':>5}  {'took':>6}  "
            f"{'drafts':>6}")


@dataclass
class View:
    """Everything the cards show besides the snapshot: card detail levels, the connection,
    the log, and the collector's caches."""
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

    def level(self, name: str) -> int:
        """A card's detail level: 0 collapsed, 1 normal, 2 full (1 for cards without one)."""
        return self.level_override.get(name, self.levels.get(name, 1))


def status_of(d: ServerData, server_pid: Optional[int]) -> Tuple[str, str, str]:
    """(label, pill background, words) for the header and the ACTIVITY card."""
    if d.exited:
        return "EXITED", "41", f"{RED}server process {server_pid} has exited{R}"
    if not d.up:
        return ("LOADING", "43", f"{YEL}loading the model…{R}") if d.pid else ("OFFLINE", "41", f"{RED}not reachable{R}")
    if not d.slots:
        return "UP", "42", "running (unknown server: limited stats)"
    if not d.busy:
        return "IDLE", "42", "idle, waiting for requests"
    nbusy = sum(1 for x in d.slot_list if x.busy)
    if nbusy > 1:
        return f"BUSY ×{nbusy}", "46", f"{nbusy} requests at once (slots in parallel)"
    if d.decoded == 0:
        done = d.cached + d.processed
        return "READING", "43", f"reading the prompt, {done / d.prompt * 100 if d.prompt else 0:.0f}%"
    return "GENERATING", "46", "generating"


# ---------------------------------------------------------------- llama.cpp cards
@dataclass
class KVInfo:
    """KV cache and recurrent-state memory of the running llama.cpp server."""
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
    """KV and state memory from the GGUF shape and the server's flags; None without a shape or window."""
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


def card_connect(v: View, d: ServerData) -> Card:
    """Endpoint, model, API key (hidden unless shown), reach, clients, copy buttons."""
    reach = "the VM and this Mac" if v.host not in ("127.0.0.1", "::1") else "this Mac only"
    hosts = collections.Counter(h for h, _ in d.conns)
    shown = v.key if v.key_shown else (("•" * 16 + v.key[-4:]) if v.key else f"{RED}no key file{R}")
    lvl = v.level("connect")
    L: List[CardLine] = [lv("endpoint", f"{B}{v.base}/v1{R}")]
    if lvl >= 1:
        L.append(lv("model", d.alias or NA))
        L.append(Ln(lv("api key", f"{MAG}{shown}{R}  ") + f"{DIM}{'hide' if v.key_shown else 'show'} (k){R}", "key"))
        L.append(lv("reachable", reach))
        L.append(lv("clients", f"{len(d.conns)} connected" + (f" from {', '.join(hosts)}" if hosts else "")))
        if v.cache:
            L.append(lv("cache", v.cache))
        L.append(buttons(f"{DIM}{'copy':<10}{R}", [("OpenCode", "opencode"), ("Pi", "pi"), ("curl", "curl")]))
    if lvl >= 2:
        L.append(lv("key file", home_short(v.key_file, v.home)))
        L.append(lv("install", "this Mac: ./carl.sh install · a VM: ./carl.sh --vm, then ./install.sh there"))
    return Card("CONNECT", f"{DIM}{v.host}:{v.port}{R}", L)


def card_context(v: View, d: ServerData) -> Card:
    """Context fill per slot, the KV cache type and memory, recurrent state and caches."""
    kv = kv_info(d)
    cmd, sl, n_ctx = d.cmd, d.slot_list, d.n_ctx
    lvl = v.level("context")
    nslots = len(sl) or flag_int(cmd, "--parallel", "-np", default=1)
    used = d.prompt + d.decoded
    frac = used / n_ctx if n_ctx else 0
    summary = f"{bar(frac, 10)} {frac * 100:3.0f}%"
    L: List[CardLine] = []
    if nslots > 1:
        tot = sum(x.prompt + x.decoded for x in sl)
        pool_tokens = flag_int(cmd, "-c", "--ctx-size", default=0)
        pool = pool_tokens or n_ctx * nslots
        frac = tot / pool if pool else 0
        summary = f"{nslots} slots {bar(frac, 8)} {frac * 100:3.0f}%"
        for i in range(nslots):
            x = sl[i] if i < len(sl) else None
            if x:
                u = x.prompt + x.decoded
                st = (f"{CYN}generating{R}" if x.decoded else f"{YEL}reading{R}") if x.busy else f"{DIM}idle{R}"
                L.append(lv(f"slot {x.id}", f"{bar(u / x.n_ctx if x.n_ctx else 0, 12)} {knum(u)} / {knum(x.n_ctx)} · {st}"))
            else:
                L.append(lv(f"slot {i}", f"{bar(0, 12)} 0 / {knum(n_ctx) if n_ctx else NA} · {NA}"))
        if n_ctx and pool_tokens and pool_tokens != n_ctx * nslots:
            L.append(lv("layout", f"{knum(pool_tokens)} shared pool · each slot ≤{knum(n_ctx)} · "
                                  f"{knum(tot)} used ({tot / pool_tokens * 100:.0f}%)"))
    else:
        L.append(lv("fill", f"{bar(frac, 16)} {knum(used)} / {knum(n_ctx) if n_ctx else NA} tokens"))
    if kv:
        mixed = f"  {RED}mixed types: ~5x slower prefill{R}" if kv.k != kv.v else ""
        L.append(lv("KV quant", f"{MAG}{kv.k}{R} K · {MAG}{kv.v}{R} V{mixed}"))
        pool_note = f" (pool for {kv.slots} slots)" if kv.slots > 1 else ""
        L.append(lv("KV RAM", f"{B}{size(kv.kv)}{R} allocated{pool_note} · {size(kv.kv * frac)} in use"))
        if lvl >= 1:
            L.append(lv("state", f"{size(kv.rs)} recurrent · ≤{size(kv.ckpt_max)} checkpoints"))
            L.append(lv("total", f"≈{size(kv.kv + kv.rs)} now · ≤{size(kv.kv + kv.rs + kv.ckpt_max + kv.cache_ram)} max"))
        if lvl >= 2 and d.shape:
            shp = d.shape
            L.append(lv("per token", f"{size(kv.per_tok)} = {shp['attn_layers']} attn layers × {shp['kvh']} KV heads × "
                                     f"({shp['kl']}+{shp['vl']}) × {kv.k}"))
            L.append(lv("MTP head", f"≈{size(kv.mtp)} KV if allocated (estimate)"))
            L.append(lv("caches", f"≤{kv.ckpt_n} checkpoints × {size(kv.rs)} · prompt cache ≤{size(kv.cache_ram)}"))
            L.append(lv("window", f"{knum(kv.n_ctx)} served · {knum(shp['ctx_train'])} trained"))
    else:
        labels = (["KV quant", "KV RAM"] + (["state", "total"] if lvl >= 1 else [])
                  + (["per token", "MTP head", "caches", "window"] if lvl >= 2 else []))
        L += [lv(x, NA) for x in labels]
    return Card("CONTEXT", summary, L)


def _gpu_limit_line(v: View) -> str:
    return lv("GPU limit", f"{size(v.gpu_limit[0])} ({v.gpu_limit[1]})" if v.gpu_limit else NA)


def card_memory(v: View, d: ServerData) -> Card:
    """The server's memory split into weights, context and the rest; the GPU limit."""
    kv = kv_info(d)
    rss = d.rss
    w = v.model_size or 0
    ctx = (kv.kv + kv.rs) if kv else 0
    L: List[CardLine] = [lv("weights", size(w) if rss else NA),
                         lv("context", f"{size(ctx)} (KV + state)" if rss else NA),
                         lv("other", f"{size(max(rss - w - ctx, 0))} (buffers, caches)" if rss else NA)]
    if v.level("memory") >= 2:
        L.append(_gpu_limit_line(v))
        L.append(lv("GPU now", f"{size(d.system.gpumem)} mapped (all apps)" if d.system.gpumem else NA))
    return Card("MEMORY", (f"server {size(rss)}" if rss else f"{DIM}no server process{R}"), L)


def card_activity(v: View, d: ServerData) -> Card:
    """What the server does now, live speeds and the prompt ETA, averages, draft acceptance."""
    m = d.metrics
    pps, tgs = m.get("prompt_seconds_total", 0), m.get("tokens_predicted_seconds_total", 0)
    pp_avg = m.get("prompt_tokens_total", 0) / pps if pps else 0
    tg_avg = m.get("tokens_predicted_total", 0) / tgs if tgs else 0
    lvl = v.level("activity")
    L: List[CardLine] = [lv("now", status_of(d, v.server_pid)[2])]
    summary = f"{DIM}idle{R}"
    prompt, cached, decoded = d.prompt, d.cached, d.decoded
    if d.busy:
        remaining = max(prompt - cached - d.processed, 0) if decoded == 0 else 0
        L.append(lv("prompt", f"{knum(prompt)} tokens · {knum(cached)} cached · {knum(remaining)} left"))
        if decoded == 0:
            rate = d.pp_rate or pp_avg
            eta = dur(remaining / rate) if rate else "N/A"
            L.append(lv("speed", f"read {d.pp_rate or 0:.0f} tok/s · ETA {YEL}{B}{eta}{R}"))
            summary = f"ETA {eta}"
        else:
            L.append(lv("speed", f"generate {d.tg_rate or 0:.1f} tok/s · {decoded} tokens out"))
            summary = f"{d.tg_rate or 0:.1f} tok/s"
    else:
        L.append(lv("prompt", "0 tokens · 0 cached · 0 left"))
        L.append(lv("speed", f"0 tok/s · ETA {DIM}none{R}"))
    llama = d.slots
    if lvl >= 1:
        L.append(lv("average", f"read {pp_avg:.0f} · generate {tg_avg:.1f} tok/s" if llama else NA))
        r = v.log.requests[-1] if v.log.requests else None
        L.append(lv("last", f"read {r.pp or 0:.0f} · generate {r.tg or 0:.1f} tok/s · "
                            f"{dur((r.t1 or 0) - (r.t0 or 0))}" if r else f"{DIM}none{R}"))
        drafted = m.get("spec_decode_num_draft_tokens_total", 0)
        acc = m.get("spec_decode_num_accepted_tokens_total", 0)
        L.append(lv("drafts", f"{acc / drafted * 100:.0f}% accepted · "
                              f"{acc / max(m.get('spec_decode_num_drafts_total', 1), 1):.2f} per draft"
                    if drafted else ("0% accepted · 0 per draft" if llama else NA)))
    if lvl >= 2:
        drafts = max(m.get("spec_decode_num_drafts_total", 0), 1)
        pos = d.accepted_by_pos
        L.append(lv("by position", "  ".join(f"#{i} {pos[i] / drafts * 100:.0f}%" for i in sorted(pos)[:4])
                    if pos else NA, 12))
        L.append(lv("totals", f"{knum(m.get('prompt_tokens_total'))} read · {knum(m.get('prompt_tokens_cached_total'))} cached · "
                              f"{knum(m.get('tokens_predicted_total'))} generated" if llama else NA))
        L.append(lv("queue", f"{m.get('requests_deferred', 0):.0f} waiting · peak context {knum(m.get('n_tokens_max'))}"
                    if llama else NA))
    return Card("ACTIVITY", summary, L)


def card_model(v: View, d: ServerData) -> Card:
    """The GGUF file, its quant and size, speculation, architecture."""
    shp, cmd = d.shape, d.cmd
    lvl = v.level("model")
    if shp:
        L: List[CardLine] = [
            lv("file", os.path.basename(v.model_path or "")),
            lv("weights", f"{shp['ftype']} · {size(v.model_size)}"),
            lv("spec", f"{flag(cmd, '--spec-type', default='none')} · {flag(cmd, '--spec-draft-n-max', default='0')} draft tokens")]
        if lvl >= 2:
            L.append(lv("arch", f"{shp['arch']} · {shp['blocks']} layers ({shp['attn_layers']} attention, "
                                f"{shp['rec_layers']} recurrent, {shp['nextn']} MTP)"))
            L.append(lv("experts", f"{shp['experts_used']} of {shp['experts']} active per token" if shp.get("experts")
                        else "none (dense)"))
            L.append(lv("thinking", "effort levels + off" if shp.get("effort_levels") else "on/off"
                        + (" (patched: none = off)" if "--chat-template-file" in cmd else "")))
            L.append(lv("batch", f"ub {flag(cmd, '-ub', default='N/A')} · flash-attn {flag(cmd, '-fa', default='N/A')} · "
                                 f"pid {d.pid} · up {d.etime or 'N/A'}"))
    else:
        why = "unknown model" if d.pid else "no server process"
        L = [lv("file", f"{NA} {DIM}({why}){R}"), lv("weights", NA), lv("spec", NA)]
        if lvl >= 2:
            L += [lv(x, NA) for x in ("arch", "experts", "thinking", "batch")]
    return Card("MODEL", (f"{shp['ftype']}" if shp else NA), L)


def _log_counts(v: View) -> str:
    c = v.log.counts
    routine = f" {DIM}· {c['notice']} routine notices{R}" if c["notice"] else ""
    return lv("log", f"{(RED + str(c['E']) + R) if c['E'] else 0} errors · {(YEL + str(c['W']) + R) if c['W'] else 0} "
                     f"warnings{routine}")


def _sleep_line(v: View, d: ServerData) -> str:
    sleeps = [e for e in v.slow.sleep_events if e[1] == "Sleep"]
    return lv("sleep", (f"{GRN}kept awake{R}" if d.awake else f"{YEL}not kept awake{R}") + " · "
              + (f"{YEL}{len(sleeps)} sleeps{R}" if sleeps else "0 sleeps") + " since start")


def _last_three(lines: List[str], empty: str) -> List[CardLine]:
    """Up to three lines, padded with dashes to exactly three (cards keep their height)."""
    shown: List[CardLine] = list(lines) or [f"{DIM}{empty}{R}"]
    return shown + [f"{DIM}–{R}"] * (3 - len(shown))


def _sleep_events(v: View) -> List[CardLine]:
    return _last_three([f"{DIM}{t} {kind} {why}{R}" for t, kind, why in v.slow.sleep_events[-3:]], "sleep/wake: none")


def card_health(v: View, d: ServerData) -> Card:
    """Log errors and warnings, GPU failures (the server must restart), sleep."""
    c = v.log.counts
    broken = c["oom"] or c["compute"]
    if broken:
        summary = f"{RED}{B}BROKEN · restart the server{R}"
    elif d.health_ms is not None:
        summary = f"{GRN}ok{R} {d.health_ms:.0f} ms"
    else:
        summary = NA
    L: List[CardLine] = [
        _log_counts(v),
        lv("GPU", (RED if broken else "") + f"{c['oom']} out-of-memory · {c['compute']} compute errors" + (R if broken else "")),
        _sleep_line(v, d)]
    if v.level("health") >= 2:
        L += _last_three([f"{RED}{e[13:]}{R}" for e in list(v.log.errors)[-3:]], "errors: none") + _sleep_events(v)
    return Card("HEALTH", summary, L)


def card_system(v: View, d: ServerData) -> Card:
    """This Mac: memory, swap, GPU, power and thermal state, CPU, disk."""
    s, lvl = d.system, v.level("system")
    pc = GRN if s.pressure == "normal" else YEL if s.pressure == "WARNING" else RED
    L: List[CardLine] = [lv("memory", f"{bar(s.used / v.total_mem, 12)} {size(s.used)} / {size(v.total_mem)}")]
    su, st = s.swap
    L.append(lv("swap", f"{(RED if su / st > 0.6 else YEL if su / st > 0.25 else GRN)}{size(su)}{R} of {size(st)}" if st
                else f"{GRN}0B{R} of 0B"))
    L.append(lv("GPU", f"{bar(s.gpu / 100, 12)} {s.gpu}%" if s.gpu is not None else NA))
    if lvl >= 1:
        L.append(lv("power", f"{(YEL if s.power.startswith('battery') else GRN)}{s.power}{R} · thermal {v.slow.thermal or 'N/A'}"))
    if lvl >= 2:
        L.append(lv("detail", f"wired {size(s.wired)} · compressed {size(s.comp)} · free {size(s.free)}"))
        L.append(lv("CPU", f"server {d.cpu:.0f}% · load {s.load[0]:.1f} {s.load[1]:.1f} {s.load[2]:.1f}"))
        L.append(lv("disk", f"{size(v.slow.disk[0])} free of {size(v.slow.disk[1])}" if v.slow.disk else NA))
    return Card("SYSTEM", f"pressure {pc}{s.pressure}{R}", L)


CardFn = Callable[[View, ServerData], Card]
CARDS: Dict[str, CardFn] = {"connect": card_connect, "context": card_context, "memory": card_memory,
                            "activity": card_activity, "model": card_model, "health": card_health, "system": card_system}


def column(v: View, names: List[str], d: ServerData, w: int) -> List[Row]:
    """The named cards drawn one under the other, w columns wide."""
    rows: List[Row] = []
    for nm in names:
        card = CARDS[nm](v, d)
        rows += draw_card(nm, card.title, card.summary, card.lines, w, v.level(nm))
    return rows


# ---------------------------------------------------------------- requests and log
def req_row(r: RequestRecord, wall: Callable[[Optional[float]], str]) -> str:
    """One request as a table row (REQ_HEAD); wall turns its start into a clock time."""
    took = r.t1 - r.t0 if r.t1 is not None and r.t0 is not None else None
    acc = f"{round(r.acc * 100)}%" if r.acc is not None else "–"
    return (f"{wall(r.t0):8}  {knum(r.ctx):>8}  {knum(r.new):>7}  {(r.pp or 0):>6.0f}  "
            f"{knum(r.gen):>6}  {(r.tg or 0):>5.1f}  {dur(took):>6}  {acc:>6}" + (f"  {RED}error{R}" if r.error else ""))


def card_requests(v: View, d: ServerData, n: int) -> Card:
    """The last n finished requests, newest first (exactly n rows)."""
    reqs = list(v.log.requests)
    running = f" · {len(v.log.current)} running" if v.log.current else ""
    rows = [req_row(r, v.log.wall) for r in reversed(reqs[-n:])] or [f"{DIM}no finished requests in this log yet{R}"]
    L: List[CardLine] = [f"{DIM}{REQ_HEAD}{R}", *rows]
    L += [f"{DIM}–{R}"] * (n - len(rows))
    return Card("RECENT REQUESTS", f"{len(reqs)} finished{running} {DIM}· tab 3 for all{R}", L)


def log_view(v: View, n: int, width: int) -> List[CardLine]:
    """The last n lines of the log (fewer, wrapped, when wrapping), errors red, warnings yellow."""
    if not v.log_path:
        empty: List[CardLine] = [f"{DIM}{NOLOG_TEXT}{R}"]
        return empty + [""] * (n - 1)
    lines = [ln for ln in v.log.lines if not v.errors_only or level_of(ln) in ("E", "W")]
    end = len(lines) - v.log_scroll
    out: List[CardLine] = []
    for line in lines[max(0, end - n * (4 if v.wrap else 1)):end]:
        m = TS.match(line)
        col = RED if m and m.group(5) == "E" else YEL if m and m.group(5) == "W" else ""
        if not v.wrap:
            out.append(col + line if col or not m else f"{DIM}{line[:m.end()]}{R}{line[m.end():]}")
        else:
            out += [col + part for part in wrap(line, width)]
    out = out[-n:] or [f"{DIM}(no log lines){R}"]
    return out + [""] * (n - len(out))             # fixed height: the card does not jump while the log fills
