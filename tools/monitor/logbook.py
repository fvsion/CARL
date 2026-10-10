"""What the server log says: recent lines, error / warning counts and finished requests
with their speeds. Pure: lines go in (logtail.LogTail reads the file), records come out.

llama.cpp log lines start with "M.SS.mmm.uuu L " (minutes since start, seconds,
milliseconds, microseconds, level I/W/E/D); a request is "launch_slot_ ... task N",
then its print_timing lines, then "release: ... n_tokens = N".

A router's log (router mode) also carries its model servers' lines, as "[PORT] M.SS..."
with the model server's own clock: the prefix goes, and the time is moved onto the
router's clock from the router's "spawning server instance ... on port PORT" line."""
from __future__ import annotations

import collections
import re
import time
from dataclasses import dataclass
from typing import Counter, Deque, Dict, Optional, Tuple

from carl_core.domain.serverlog import CacheState, cache_state


TS = re.compile(r"^(\d+)\.(\d+)\.(\d+)\.(\d+) ([IWED]) ")
_TASK = re.compile(r"task (\d+)")
_SLOT = re.compile(r"id +(\d+) \|")
_PROMPT = re.compile(r"prompt eval time =\s*[\d.]+ ms /\s*(\d+) tokens.*?([\d.]+) tokens per second")
_EVAL = re.compile(r"\|\s+eval time =\s*[\d.]+ ms /\s*(\d+) tokens.*?([\d.]+) tokens per second")
_DRAFT = re.compile(r"draft acceptance = ([\d.]+)")
_RELEASE = re.compile(r"release: .*n_tokens = (\d+)")
_CHILD = re.compile(r"^\[(\d+)\] ")
_SPAWN = re.compile(r"spawning server instance with name=\S+ on port (\d+)")
# A router logs every request it forwards, the dashboard's own polls included (one line per
# /slots, /metrics, /props every refresh): left out of the lines shown.
_PROXIED = "proxy_reques: proxying request to model"
# Warning (and a few error) lines llama.cpp writes in normal work: counted as notices, so the HEALTH
# card's counts mean something (found in every log, 2026-10-03; the Gemma 4 lines 2026-10-04).
ROUTINE = ("stop: cancel task",                          # a client stopped a request
           "erasing old context checkpoint",              # checkpoints rotate as a session grows
           "making room for prompt cache entry",          # the RAM prompt cache drops its oldest entry
           "server default port will be changed",         # an upstream notice (CARL sets --port)
           "chat template supports preserving reasoning", # CARL sends preserve_thinking itself
           "model has unused tensor",                     # tensors the loader skips (e.g. the MTP head's)
           "requires ctx_other to be set",                # Gemma 4: an error line while llama.cpp fits the memory
           "control-looking token")                       # Gemma 4: token-type notes when the model loads
_SWITCH = re.compile(r"(?:ensure_model: waiting until model name=|load_startup: \(startup\) loading model )(\S+)")


def level_of(line: str) -> str:
    """The log level letter (I W E D) of a line, "" if it has no timestamp."""
    m = TS.match(line)
    return m.group(5) if m else ""


def offset(line: str) -> Optional[float]:
    """Seconds since the server started, from the line's timestamp."""
    m = TS.match(line)
    return int(m.group(1)) * 60 + int(m.group(2)) + int(m.group(3)) / 1000 if m else None


def shifted(line: str, by: float) -> str:
    """line with its timestamp moved by `by` seconds (same format)."""
    m = TS.match(line)
    if not m:
        return line
    us = round((int(m.group(1)) * 60 + int(m.group(2))) * 1e6 + int(m.group(3)) * 1000 + int(m.group(4)) + by * 1e6)
    mins, rest = divmod(us, 60_000_000)
    secs, rest = divmod(rest, 1_000_000)
    return f"{mins}.{secs:02d}.{rest // 1000:03d}.{rest % 1000:03d} {m.group(5)} " + line[m.end():]


def from_router(line: str, spawned: Dict[int, float]) -> str:
    """A router log line as a plain one: a model server's "[PORT] " prefix removed and its time
    put on the router's clock (spawned: port -> router time of the spawn, updated here)."""
    m = _CHILD.match(line)
    if m:
        base = spawned.get(int(m.group(1)))
        rest = line[m.end():]
        return shifted(rest, base) if base is not None else rest
    sp = _SPAWN.search(line)
    t = offset(line)
    if sp and t is not None:
        spawned[int(sp.group(1))] = t
    return line


@dataclass
class RequestRecord:
    """One request: while it runs or finished (from the log). t0 / t1 are seconds since the
    server started."""
    task: Optional[int] = None
    slot: int = 0
    t0: Optional[float] = None
    t1: Optional[float] = None
    ctx: float = 0          # tokens in the context at the end
    new: float = 0          # prompt tokens read (not from the cache)
    pp: Optional[float] = None      # prompt tokens read per second
    gen: float = 0          # tokens generated
    tg: Optional[float] = None      # tokens generated per second
    acc: Optional[float] = None     # share of draft tokens accepted
    error: bool = False
    source: str = ""        # where the reused tokens came from: this slot, the RAM cache, the disk cache (Phase 23.5)


class LogBook:
    """Recent log lines, counts of errors / warnings / GPU failures, and requests."""

    def __init__(self) -> None:
        self.start: Optional[float] = None      # epoch seconds the log started (from its file name)
        self.lines: Deque[str] = collections.deque(maxlen=4000)
        self.counts: Counter[str] = collections.Counter()
        self.errors: Deque[str] = collections.deque(maxlen=8)
        self.requests: Deque[RequestRecord] = collections.deque(maxlen=50)
        self.current: Dict[int, RequestRecord] = {}     # in-flight requests by task id
        self.spawned: Dict[int, float] = {}             # a router's model servers: port -> spawn time
        self.switches: Deque[Tuple[Optional[float], str]] = collections.deque(maxlen=8)   # a router's loads
        self.cache: Optional[CacheState] = None         # the RAM cache, from the last "cache state" line (-lv 4)

    def reset(self) -> None:
        """Forget everything read so far (a new or truncated log); start is kept."""
        self.lines.clear()
        self.counts = collections.Counter()
        self.errors = collections.deque(maxlen=8)
        self.requests = collections.deque(maxlen=50)
        self.current = {}
        self.spawned = {}
        self.switches = collections.deque(maxlen=8)
        self.cache = None

    def add(self, line: str) -> None:
        """Take in one log line (without its newline)."""
        line = from_router(line.rstrip(), self.spawned)
        if line and _PROXIED not in line:
            self.lines.append(line)
            self.parse(line)

    def parse(self, line: str) -> None:
        lvl = level_of(line)
        if lvl in ("W", "E") and any(r in line for r in ROUTINE):
            self.counts["notice"] += 1
        elif lvl in ("E", "W"):
            self.counts[lvl] += 1
            if lvl == "E":
                self.errors.append(line)
        cs = cache_state(line)
        if cs:
            self.cache = cs
        sw = _SWITCH.search(line)
        if sw:
            self.switches.append((offset(line), sw.group(1).rstrip(".")))
        if "OutOfMemory" in line:
            self.counts["oom"] += 1
        if "Compute error" in line:
            self.counts["compute"] += 1
        t = _TASK.search(line)
        task = int(t.group(1)) if t else None
        if "launch_slot_" in line and task is not None:
            sm = _SLOT.search(line)
            self.current[task] = RequestRecord(task=task, slot=int(sm.group(1)) if sm else 0, t0=offset(line))
        elif task is not None and task in self.current:
            c = self.current[task]
            mm = _PROMPT.search(line)
            if mm:
                c.new, c.pp = int(mm.group(1)), float(mm.group(2))
            mm = _EVAL.search(line)
            if mm:
                c.gen, c.tg = int(mm.group(1)), float(mm.group(2))
            mm = _DRAFT.search(line)
            if mm:
                c.acc = float(mm.group(1))
            if "send_error" in line:
                c.error = True
            mm = _RELEASE.search(line)
            if mm:
                c.ctx, c.t1 = int(mm.group(1)), offset(line)
                self.requests.append(c)
                del self.current[task]

    def wall(self, off: Optional[float], ms: bool = False) -> str:
        """A log offset as the local wall-clock time (ms: with milliseconds, 13:10:53.265)."""
        if not self.start or off is None:
            return "--:--:--"
        t = self.start + off
        return time.strftime("%H:%M:%S", time.localtime(t)) + (f".{int(t * 1000) % 1000:03d}" if ms else "")

