"""What the disk cache did (Phase 23.5.1.1, research 7, the counters): the events OpenCode's and Pi's carl-cache write
(client/shared/carl-cache.js `event()`), counted over a time window and matched with the server log's requests.

- The events: one JSON line each in CARL_DIR/cache-events/DAY.jsonl (DAY: the local date). A client on this Mac
  appends them; a client on another computer sends them to the dashboard API (POST /carl/cache/events), which appends
  them here (append()). Kinds: "place" (a request of a session put in a slot: from = slot | ram | window | session |
  start | prompt | read, the tokens restored, history: the session had earlier turns), "save" (what = session | prompt,
  the tokens, the bytes written), "retire" (a saved state that came back short or failed). Files older than two days
  go (prune()).
- The server log (LogBook.tally): each answered request's prompt tokens and the tokens read, so the tokens reused.
- The Settings > Caching section "WHAT THE CACHE DID" (table()) and the Live tab's SLOTS card's Cache column
  (slot_column()).
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from carl_core.domain.units import duration, file_size

EVENTS_DIR = "cache-events"
KEEP_S = 2 * 86400                  # events this old are dropped, and their day files removed
DAY_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl$")
KINDS = ("place", "save", "retire")
FROM = ("slot", "ram", "window", "session", "start", "prompt", "read")
RESTORED = ("session", "start", "prompt")       # CARL put these tokens in the slot from a file
MAX_TEXT = 200                       # a name or a file name in an event (a remote client's: cut)
WINDOWS = ("last hour", "today", "since the start")

Event = Dict[str, object]
Tally = Tuple[float, int, int, int, float]       # (wall time at its end, slot, prompt tokens, tokens read, read seconds)


def events_dir(carl_dir: str) -> str:
    return os.path.join(carl_dir, EVENTS_DIR)


def day_name(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(t)) + ".jsonl"


def clean(doc: object, now: float) -> Optional[Event]:
    """An event from a client on another computer, kept only with its known fields and types (None: not one)."""
    if not isinstance(doc, dict) or doc.get("e") not in KINDS:
        return None
    t = doc.get("t")
    out: Event = {"e": doc["e"], "t": float(t) if isinstance(t, (int, float)) and not isinstance(t, bool) and
                  abs(float(t) - now) < KEEP_S else now}
    for k in ("model", "session", "agent", "from", "what", "file"):
        v = doc.get(k)
        if isinstance(v, str) and v:
            out[k] = v[:MAX_TEXT]
    for k in ("slot", "tokens", "bytes"):
        v = doc.get(k)
        if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 1 << 50:
            out[k] = v
    for k in ("sub", "history"):
        if isinstance(doc.get(k), bool):
            out[k] = doc[k]
    if out["e"] == "place" and out.get("from") not in FROM:
        return None
    return out


def append(carl_dir: str, docs: Sequence[object], host: str, slots_dir: str = "", now: Optional[float] = None) -> int:
    """A remote client's events (the API): appended to today's file with the client's address; a save's bytes from the
    file here when the client could not see it. How many were kept."""
    now = time.time() if now is None else now
    kept = [e for e in (clean(d, now) for d in docs[:100]) if e]
    if not kept:
        return 0
    d = events_dir(carl_dir)
    os.makedirs(d, mode=0o700, exist_ok=True)
    with open(os.path.join(d, day_name(now)), "a", encoding="utf-8") as f:
        for e in kept:
            if e["e"] == "save" and not e.get("bytes") and slots_dir and isinstance(e.get("file"), str):
                try:
                    e["bytes"] = os.path.getsize(os.path.join(slots_dir, os.path.basename(str(e["file"]))))
                except OSError:
                    pass
            f.write(json.dumps({**e, "host": host[:MAX_TEXT]}) + "\n")
    return len(kept)


def prune(carl_dir: str, now: Optional[float] = None) -> int:
    """Remove the day files older than KEEP_S (the dashboard's minute job). How many went."""
    now = time.time() if now is None else now
    d = events_dir(carl_dir)
    try:
        names = os.listdir(d)
    except OSError:
        return 0
    gone = 0
    for n in names:
        m = DAY_FILE.match(n)
        if not m:
            continue
        try:
            day = time.mktime(time.strptime(m.group(1), "%Y-%m-%d"))
        except ValueError:
            continue
        if day + 86400 < now - KEEP_S:
            try:
                os.remove(os.path.join(d, n))
                gone += 1
            except OSError:
                pass
    return gone


class EventLog:
    """The events of the last two days, read from the day files as they grow (each poll reads only what is new)."""

    def __init__(self, carl_dir: str) -> None:
        self.dir = events_dir(carl_dir)
        self.read_to: Dict[str, int] = {}
        self.events: List[Event] = []

    def poll(self, now: Optional[float] = None) -> List[Event]:
        now = time.time() if now is None else now
        try:
            names = sorted(n for n in os.listdir(self.dir) if DAY_FILE.match(n))
        except OSError:
            names = []
        for n in names[-3:]:
            path = os.path.join(self.dir, n)
            try:
                with open(path, "rb") as f:
                    f.seek(self.read_to.get(n, 0))
                    data = f.read()
            except OSError:
                continue
            end = data.rfind(b"\n") + 1                 # a line still being written waits for the next poll
            self.read_to[n] = self.read_to.get(n, 0) + end
            for line in data[:end].splitlines():
                try:
                    e = clean(json.loads(line), now)
                except ValueError:
                    continue
                if e:
                    self.events.append(e)
        self.events = [e for e in self.events if float(e["t"]) >= now - KEEP_S]       # type: ignore[arg-type]
        self.read_to = {n: v for n, v in self.read_to.items() if n in names}
        return self.events


def window_start(window: str, now: float, server_start: Optional[float]) -> float:
    """The time a window starts: the last hour, today (local midnight), or since the server started."""
    if window == "today":
        lt = time.localtime(now)
        return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    if window == "since the start":
        return server_start or now - KEEP_S
    return now - 3600


def big(n: float) -> str:
    """Tokens in a table cell: 98K, 2.9M."""
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e3:
        return f"{n / 1e3:.0f}K" if n >= 1e4 else f"{n / 1e3:.1f}K"
    return str(int(n))


def read_speed(tally: Sequence[Tally]) -> Optional[float]:
    """The measured prompt read speed (tokens read / seconds reading) of the requests given."""
    new = sum(t[3] for t in tally if t[4] > 0)
    secs = sum(t[4] for t in tally if t[4] > 0)
    return new / secs if secs > 0 and new > 0 else None


def first_read(tally: Sequence[Tally], slot: int, after: float) -> Optional[Tally]:
    """The first answered request on that slot that ended after a placement (the request it placed)."""
    for t in tally:
        if t[1] == slot and t[0] >= after - 1:
            return t
    return None


@dataclass(frozen=True)
class StatRow:
    what: str
    count: str = ""
    tokens: str = ""
    time: str = ""
    written: str = ""


def table(events: Iterable[Event], tally: Sequence[Tally], since: float) -> List[StatRow]:
    """The rows of WHAT THE CACHE DID for the events and requests since `since`."""
    ev = [e for e in events if float(e["t"]) >= since]          # type: ignore[arg-type]
    req = [t for t in tally if t[0] >= since]
    speed = read_speed(req) or read_speed(tally)
    saved_s = (lambda n: f"saved {duration(n / speed)}" if speed and n else "")
    places = [e for e in ev if e["e"] == "place"]
    rs = [e for e in places if e.get("from") in ("session", "start")]
    rp = [e for e in places if e.get("from") == "prompt"]
    rs_tok = sum(int(e.get("tokens", 0)) for e in rs)            # type: ignore[call-overload]
    rp_tok = sum(int(e.get("tokens", 0)) for e in rp)            # type: ignore[call-overload]
    prompt_all = sum(t[2] for t in req)
    reused = sum(max(t[2] - t[3], 0) for t in req)
    in_slot = max(reused - rs_tok - rp_tok, 0)
    again = [e for e in places if e.get("history") and not e.get("sub") and e.get("from") in ("read", "prompt")]
    again_reqs = [first_read(tally, int(e.get("slot", -1)), float(e["t"])) for e in again]   # type: ignore[call-overload,arg-type]
    again_tok = sum(t[3] for t in again_reqs if t)
    again_s = sum(t[4] for t in again_reqs if t)
    saves = [e for e in ev if e["e"] == "save"]
    ss = [e for e in saves if e.get("what") == "session"]
    sp = [e for e in saves if e.get("what") == "prompt"]
    written = (lambda xs: file_size(sum(int(e.get("bytes", 0)) for e in xs)) if xs else "")   # type: ignore[call-overload]
    retired = sum(1 for e in ev if e["e"] == "retire")
    return [
        StatRow("Restored sessions", str(len(rs)), big(rs_tok) if rs else "", saved_s(rs_tok)),
        StatRow("Restored prompts", str(len(rp)), big(rp_tok) if rp else "", saved_s(rp_tok)),
        StatRow("Still in their slot", "", big(in_slot) if req else "", saved_s(in_slot)),
        StatRow("Read again", str(len(again)), big(again_tok) if again_tok else "",
                f"took {duration(again_s)}" if again_s else ""),
        StatRow("Saved sessions", str(len(ss)), "", "", written(ss)),
        StatRow("Saved prompts", str(len(sp)), "", "", written(sp)),
        StatRow("Retired files", str(retired)),
        StatRow("All requests", "", f"{big(reused)} of {big(prompt_all)} reused ({round(100 * reused / prompt_all)}%)"
                if prompt_all else "no request yet"),
    ]


FROM_TEXT = {"slot": "its slot", "ram": "the RAM cache", "window": "read (window cache)", "session": "session file",
             "start": "a longer session's file", "read": "read in full"}


def slot_column(events: Sequence[Event], slot: int, model: str, names: Mapping[str, str],
                now: Optional[float] = None) -> Optional[Tuple[str, str, str]]:
    """The SLOTS card's Cache column for the session in a slot: (from, saved, session), from the latest placement
    on that slot (of that model, when it is known); None when there is none."""
    now = time.time() if now is None else now
    place = next((e for e in reversed(events) if e["e"] == "place" and e.get("slot") == slot
                  and (not model or not e.get("model") or e.get("model") == model)), None)
    if not place:
        return None
    src, n = str(place.get("from", "")), int(place.get("tokens", 0))     # type: ignore[call-overload]
    if src == "prompt":
        frm = f"{place.get('agent') or 'agent'} prompt, {big(n)}"
    elif src in ("session", "start"):
        frm = f"{FROM_TEXT[src]}, {big(n)}"
    elif src == "read" and not place.get("history"):
        frm = "read (a new session)"
    else:
        frm = FROM_TEXT.get(src, src)
    session = str(place.get("session", ""))
    save = next((e for e in reversed(events) if e["e"] == "save" and e.get("what") == "session"
                 and e.get("session") == session), None)
    if save:
        saved = f"{big(int(save.get('tokens', 0)))}, {duration(max(now - float(save['t']), 0))} ago"   # type: ignore[call-overload,arg-type]
    else:
        saved = "no (a subagent)" if place.get("sub") else "not yet"
    name = names.get(session) or (session[:12] + "…" if len(session) > 13 else session)
    return frm, saved, name
