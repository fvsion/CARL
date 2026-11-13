"""The dashboard API's routes (contracts C-300 to C-314): what OpenCode and Pi on another computer (a VM) need to use
the disk cache as the clients on this computer do through the slots folder, and the client config they sync. The
HTTP server is an adapter (adapters/http/dashboard_api_server.py: the key, the body, the event stream); it hands each
call here. The claims and records are files of the slots folder (CacheClaims; adapters/common/slot_claims.py), the
rest comes in as CacheApiFiles from the composition (composition/cache_api.py).

  GET  /carl/status                           {state, read, write, progress, last, ram_cache}: the speeds that
                                              OpenCode's sidebar and Pi's status line show (Phase 23.5)
  GET  /carl/client/server                    {port}: the server's port now (a client's sync follows a move)
  GET  /carl/client/config                    the published client config (ETag / If-None-Match: 304)
  GET  /carl/cache/settings[?port=N]         the Caching settings (prefix, sessions, save, auto_s, disk_gb, move) and
                                              the new-file gate of carl-delegation (gate); ?port=N: also "server", the
                                              mode and each model's cache of the server on port N (Phase 23.5)
  POST /carl/cache/claim    {model, slot}     take a slot for a request (409: another client has it)
  POST /carl/cache/release  {model, slot}
  POST /carl/cache/record   {model, slot, file, task, base}   the session a slot holds
  GET  /carl/cache/record?model=M&file=F      where that session is (404: no record)
  POST /carl/cache/unrecord {model, slot}
  POST /carl/cache/turn     {model, slot, session, running}   a turn runs in a slot, or it ended (the
                                              dashboard's Stop can wait for the end of the turn)
  GET  /carl/cache/turns?model=M             the slots where a turn runs: [{slot, session}] (a new
                                              session takes another slot first)
  POST /carl/cache/unpack   {file}            make a conversation stored as a patch whole again, before
                                              a restore (adapters/common/zstd_patch.py)
  POST /carl/cache/retire   {file}            remove a saved state that cannot be used (one restored short, or
                                              the file of a session its client compacted; Phase 23.5.1.3)
  POST /carl/cache/ram      {model, file, task, at}   a session in llama.cpp's RAM cache, not saved (its slot was
                                              taken; Phase 23.5.1.10): any client process takes the RAM path
  GET  /carl/cache/ram?model=M&file=F         that note (404: none)
  POST /carl/cache/unram    {model, file}     the note gone (the session is in a slot again, saved or compacted)
  POST /carl/cache/save-recorded {model}      save the recorded sessions of a model about to stop
                                              (a router switch a remote client asks for)
  POST /carl/cache/events   {events}          what the cache did on another computer (Phase 23.5.1.1, the counters)

Inputs are checked (names, slot numbers); nothing else is served. Contract C-133.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Protocol, Tuple

from ..domain.server.snapshot import JSONDict

MAX_NAME = 200           # a model or session name
MAX_SLOT = 64
SESSION_FILE = re.compile(r"carl-session\+[A-Za-z0-9._+-]{1,300}\.bin")
STATE_FILE = re.compile(r"carl-(?:prefix|session)\+[A-Za-z0-9._+-]{1,300}\.bin")

Reply = Tuple[int, JSONDict]


class CacheClaims(Protocol):
    """The claim, record, turn and RAM-note files of one slots folder (adapters/common/slot_claims.py)."""

    folder: str

    def claim(self, model: str, slot: int) -> bool: ...
    def release(self, model: str, slot: int) -> None: ...
    def record(self, model: str, slot: int, file: str, task: int, base: int) -> None: ...
    def recorded(self, model: str, file: str) -> Optional[JSONDict]: ...
    def unrecord(self, model: str, slot: int) -> None: ...
    def mark_ram(self, model: str, file: str, task: int, at: float) -> None: ...
    def ram_marked(self, model: str, file: str) -> Optional[JSONDict]: ...
    def unmark_ram(self, file: str) -> None: ...
    def retire(self, file: str) -> int: ...
    def turn(self, model: str, slot: int, session: str, running: bool) -> None: ...
    def turn_marks(self, model: str, now: Optional[float] = None) -> List[Tuple[int, str]]: ...


class CacheSettingsView(Protocol):
    """The Caching settings the API serves (adapters/common/slot_folder.py CacheConfig)."""

    prefix: bool
    sessions: bool
    save: str
    auto_s: int
    disk_gb: int
    move: str
    gate: int


@dataclass(frozen=True)
class CacheApiFiles:
    """What the routes read and write in the settings folder: the published client config (None: none yet), another
    computer's cache events appended (the events, the caller's address; how many were kept), and the server file of
    a port ({}: none)."""
    published: Callable[[], Optional[JSONDict]]
    append_events: Callable[[List[object], str], int]
    server_file: Callable[[int], JSONDict]


def check_slot(doc: JSONDict) -> Tuple[str, int]:
    """(model, slot) from a request body; ValueError when they aren't usable."""
    model, slot = doc.get("model"), doc.get("slot")
    if not isinstance(model, str) or not model or len(model) > MAX_NAME:
        raise ValueError("model")
    if not isinstance(slot, int) or isinstance(slot, bool) or not 0 <= slot < MAX_SLOT:
        raise ValueError("slot")
    return model, slot


class CacheApiService:
    """The routes over one slots folder. conf: the Caching settings now; unpack(file): a conversation stored as a
    patch made whole in the slots folder (False: no such state); files: None without a settings folder (no client
    config, no events, no server file); save_recorded(model): the sessions saved; command(): the command word (a
    message names the push command)."""

    def __init__(self, claims: CacheClaims, conf: Callable[[], CacheSettingsView], unpack: Callable[[str], bool],
                 files: Optional[CacheApiFiles], save_recorded: Callable[[str], int] = lambda model: 0,
                 command: Callable[[], str] = lambda: "carl") -> None:
        self.claims, self.conf, self.unpack, self.files = claims, conf, unpack, files
        self.save_recorded, self.command = save_recorded, command

    def handle(self, method: str, path: str, query: Dict[str, str], body: JSONDict, etag: str = "",
               server_port: int = 0, status: Optional[JSONDict] = None, host: str = "") -> Reply:
        """One API call: (status, JSON). etag: the client's If-None-Match (the config version it has); host: the
        caller's address (the events it sends)."""
        try:
            return self._route(method, path, query, body, etag, server_port, status, host)
        except ValueError as e:
            return 400, {"error": f"bad {e}"}
        except OSError as e:
            return 500, {"error": e.strerror or "file error"}

    def _route(self, method: str, path: str, query: Dict[str, str], body: JSONDict, etag: str, server_port: int,
               status: Optional[JSONDict], host: str) -> Reply:
        state, files = self.claims, self.files
        if method == "GET" and path == "/carl/status":
            # the speeds for OpenCode's sidebar and Pi's status line (Phase 23.5 item 9): the dashboard's last snapshot
            return 200, dict(status or {"state": "idle"})
        if method == "GET" and path == "/carl/client/server":
            # the server's port now (Phase 23.5): a client's sync whose remote.json names another sets itself up again
            return (200, {"port": server_port}) if server_port else (404, {"error": "no server port"})
        if method == "GET" and path == "/carl/client/config":
            doc = files.published() if files else None
            if doc is None:
                return 404, {"error": f"no client config published yet (the Connect tab's Push, or "
                                      f"{self.command()} push)"}
            return (304, {}) if etag.strip('"') == doc["version"] else (200, doc)
        if method == "POST" and path == "/carl/cache/unpack":
            ufile = body.get("file")
            if not isinstance(ufile, str) or not SESSION_FILE.fullmatch(ufile):
                return 400, {"error": "a session file name"}
            return (200, {"ok": True}) if self.unpack(ufile) else (404, {"error": "no such state"})
        if method == "POST" and path == "/carl/cache/retire":
            rfile = body.get("file")
            if not isinstance(rfile, str) or not STATE_FILE.fullmatch(rfile):
                return 400, {"error": "a saved state's name"}
            return 200, {"removed": state.retire(rfile)}
        if method == "POST" and path == "/carl/cache/save-recorded":
            model = body.get("model")
            if not isinstance(model, str) or not model or len(model) > MAX_NAME:
                return 400, {"error": "model"}
            return 200, {"saved": self.save_recorded(model)}
        if method == "POST" and path == "/carl/cache/events":
            # what the cache did on another computer (Phase 23.5.1.1, the counters): appended to this computer's events
            docs = body.get("events")
            if not isinstance(docs, list) or files is None:
                return 400, {"error": "events"}
            return 200, {"kept": files.append_events(docs, host)}
        if method == "GET" and path == "/carl/cache/settings":
            c = self.conf()
            doc: JSONDict = {"prefix": c.prefix, "sessions": c.sessions, "save": c.save, "auto_s": c.auto_s,
                             "disk_gb": c.disk_gb, "move": c.move, "gate": c.gate}
            port = query.get("port", "")
            if files and port.isdigit() and 0 < int(port) < 65536:     # the server's file (Phase 23.5): its cache
                server = files.server_file(int(port))
                if server:
                    doc["server"] = {"mode": server.get("mode"), "models": server.get("models")}
            return 200, doc
        if method == "GET" and path == "/carl/cache/turns":
            tmodel = query.get("model", "")
            if not tmodel or len(tmodel) > MAX_NAME:
                return 400, {"error": "model"}
            return 200, {"turns": [{"slot": sl, "session": ses} for sl, ses in sorted(state.turn_marks(tmodel))]}
        if path in ("/carl/cache/ram", "/carl/cache/unram"):
            return self._ram(method, path, query, body)
        if method == "GET" and path == "/carl/cache/record":
            model, file = query.get("model", ""), query.get("file", "")
            if not model or len(model) > MAX_NAME or not SESSION_FILE.fullmatch(file):
                return 400, {"error": "model and a session file name"}
            rec = state.recorded(model, file)
            return (200, rec) if rec else (404, {"error": "no record"})
        if method != "POST":
            return 404, {"error": "not found"}
        return self._slot(path, body)

    def _ram(self, method: str, path: str, query: Dict[str, str], body: JSONDict) -> Reply:
        """The RAM notes: a session in llama.cpp's RAM cache, not saved (Phase 23.5.1.10)."""
        state = self.claims
        rmodel = query.get("model", "") if method == "GET" else body.get("model")
        rfile = query.get("file", "") if method == "GET" else body.get("file")
        if not (isinstance(rmodel, str) and rmodel and len(rmodel) <= MAX_NAME and isinstance(rfile, str)
                and SESSION_FILE.fullmatch(rfile)):
            return 400, {"error": "model and a session file name"}
        if method == "GET" and path == "/carl/cache/ram":
            doc = state.ram_marked(rmodel, rfile)
            return (200, doc) if doc else (404, {"error": "no note"})
        if method == "POST" and path == "/carl/cache/unram":
            state.unmark_ram(rfile)
            return 200, {"ok": True}
        if method == "POST":
            task, at = body.get("task"), body.get("at")
            if not (isinstance(task, int) and not isinstance(task, bool) and isinstance(at, (int, float))
                    and not isinstance(at, bool)):
                return 400, {"error": "task and at"}
            state.mark_ram(rmodel, rfile, task, float(at))
            return 200, {"ok": True}
        return 404, {"error": "not found"}

    def _slot(self, path: str, body: JSONDict) -> Reply:
        """The calls on one slot of a model: claim, release, record, unrecord, turn."""
        state = self.claims
        model, slot = check_slot(body)
        if path == "/carl/cache/claim":
            return (200, {"ok": True}) if state.claim(model, slot) else (409, {"error": "taken"})
        if path == "/carl/cache/release":
            state.release(model, slot)
            return 200, {"ok": True}
        if path == "/carl/cache/record":
            rec_file, task, base = body.get("file"), body.get("task"), body.get("base", 0)
            if not (isinstance(rec_file, str) and SESSION_FILE.fullmatch(rec_file) and isinstance(task, int)
                    and isinstance(base, int)):
                return 400, {"error": "file, task and base"}
            state.record(model, slot, rec_file, task, base)
            return 200, {"ok": True}
        if path == "/carl/cache/unrecord":
            state.unrecord(model, slot)
            return 200, {"ok": True}
        if path == "/carl/cache/turn":
            session, running = body.get("session"), body.get("running")
            if not (isinstance(session, str) and 0 < len(session) <= MAX_NAME and isinstance(running, bool)):
                return 400, {"error": "session and running"}
            state.turn(model, slot, session, running)
            return 200, {"ok": True}
        return 404, {"error": "not found"}
