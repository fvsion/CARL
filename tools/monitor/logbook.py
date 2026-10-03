"""What the server log says: recent lines, error / warning counts and finished requests
with their speeds. Pure: lines go in (logtail.LogTail reads the file), records come out.

llama.cpp log lines start with "M.SS.mmm.uuu L " (minutes since start, seconds,
milliseconds, microseconds, level I/W/E/D); a request is "launch_slot_ ... task N",
then its print_timing lines, then "release: ... n_tokens = N"."""
from __future__ import annotations

import collections
import re
import time
from dataclasses import dataclass
from typing import Counter, Deque, Dict, Optional

from .model import JSONDict, jdict, jnum

TS = re.compile(r"^(\d+)\.(\d+)\.(\d+)\.(\d+) ([IWED]) ")
_TASK = re.compile(r"task (\d+)")
_SLOT = re.compile(r"id +(\d+) \|")
_PROMPT = re.compile(r"prompt eval time =\s*[\d.]+ ms /\s*(\d+) tokens.*?([\d.]+) tokens per second")
_EVAL = re.compile(r"\|\s+eval time =\s*[\d.]+ ms /\s*(\d+) tokens.*?([\d.]+) tokens per second")
_DRAFT = re.compile(r"draft acceptance = ([\d.]+)")
_RELEASE = re.compile(r"release: .*n_tokens = (\d+)")


def level_of(line: str) -> str:
    """The log level letter (I W E D) of a line, "" if it has no timestamp."""
    m = TS.match(line)
    return m.group(5) if m else ""


def offset(line: str) -> Optional[float]:
    """Seconds since the server started, from the line's timestamp."""
    m = TS.match(line)
    return int(m.group(1)) * 60 + int(m.group(2)) + int(m.group(3)) / 1000 if m else None


@dataclass
class RequestRecord:
    """One request: while it runs (from the log) or finished (log or MTPLX snapshot).
    t0 / t1 are seconds since the server started (llama.cpp) or epoch seconds (MTPLX)."""
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
    restore: str = "cold"   # MTPLX: how the session started (cold, warm, ...)
    cached: float = 0       # MTPLX: tokens re-used from the session bank


def _pick(rec: JSONDict, *names: str) -> object:
    """The first of names that is set (not None) in rec."""
    for n in names:
        v = rec.get(n)
        if v is not None:
            return v
    return None


def mx_request(raw: object) -> RequestRecord:
    """One finished MTPLX request (an entry of the snapshot's recent / latest)."""
    r = jdict(raw)
    t1, took = jnum(_pick(r, "completed_at_s")), jnum(_pick(r, "request_elapsed_s"))
    drafted = jnum(_pick(r, "drafted_tokens")) or 0
    restore = _pick(r, "session_restore_mode")
    return RequestRecord(
        t0=(t1 - took) if (t1 and took) else t1, t1=t1,
        ctx=jnum(_pick(r, "context_len", "prompt_tokens")) or 0,
        new=jnum(_pick(r, "new_prefill_tokens")) or 0,
        pp=jnum(_pick(r, "prefill_tok_s", "prompt_tps")),
        gen=jnum(_pick(r, "completion_tokens")) or 0,
        tg=jnum(_pick(r, "display_decode_tok_s", "decode_tok_s")),
        acc=(jnum(_pick(r, "accepted_drafts")) or 0) / drafted if drafted else None,
        restore="cold" if restore is None else str(restore),
        cached=jnum(_pick(r, "cached_tokens")) or 0,
        error=bool(_pick(r, "error")))


class LogBook:
    """Recent log lines, counts of errors / warnings / GPU failures, and requests."""

    def __init__(self) -> None:
        self.start: Optional[float] = None      # epoch seconds the log started (from its file name)
        self.lines: Deque[str] = collections.deque(maxlen=4000)
        self.counts: Counter[str] = collections.Counter()
        self.errors: Deque[str] = collections.deque(maxlen=8)
        self.requests: Deque[RequestRecord] = collections.deque(maxlen=50)
        self.current: Dict[int, RequestRecord] = {}     # in-flight requests by task id

    def reset(self) -> None:
        """Forget everything read so far (a new or truncated log); start is kept."""
        self.lines.clear()
        self.counts = collections.Counter()
        self.errors = collections.deque(maxlen=8)
        self.requests = collections.deque(maxlen=50)
        self.current = {}

    def add(self, line: str) -> None:
        """Take in one log line (without its newline)."""
        line = line.rstrip()
        if line:
            self.lines.append(line)
            self.parse(line)

    def parse(self, line: str) -> None:
        lvl = level_of(line)
        if lvl in ("E", "W"):
            self.counts[lvl] += 1
            if lvl == "E":
                self.errors.append(line)
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

    def wall(self, off: Optional[float]) -> str:
        """A log offset as the local wall-clock time."""
        return time.strftime("%H:%M:%S", time.localtime(self.start + off)) if self.start and off is not None else "--:--:--"


def mx_wall(ts: Optional[float]) -> str:
    """An MTPLX epoch timestamp as the local wall-clock time."""
    return time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "–"
