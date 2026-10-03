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

