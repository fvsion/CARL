"""The dashboard's API for clients (while the dashboard runs). What OpenCode and Pi on another
computer (a VM) need to use the disk cache as the clients on this Mac do through the slots folder,
and the client config they sync (clientsync.py; client/carl-sync.py):

  GET  /carl/client/config                    the published client config (ETag / If-None-Match: 304)
  GET  /carl/client/events                    server-sent events: "config" with the version when a
                                              new config is published (the client connects out: no
                                              port opens on the client), a comment every 25 s

  GET  /carl/cache/settings                   the Caching settings (prefix, sessions, save, auto_s, disk_gb)
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
                                              a restore (slotpack.py)
  POST /carl/cache/save-recorded {model}      save the recorded sessions of a model about to stop
                                              (a router switch a remote client asks for)

It listens next to llama-server (its address, port + 1) and wants the same API key (Bearer).
Claims and records are the same files the local clients use (client/shared/carl-cache.js:
.claim+MODEL+SLOT, .resident+MODEL+SLOT.json, .turn+MODEL+SLOT in the slots folder), so every client sees the
same state. Inputs are checked (names, slot numbers, a small body); nothing else is served.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import select
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from . import clientsync, slotpack
from .diskcache import CacheConfig
from .model import JSONDict, jdict

MAX_BODY = 4096
MAX_SLOT = 64
CLAIM_STALE_S = 120
TURN_STALE_S = 600       # a turn mark this old is a client that went away (carl-cache.js TURN_STALE_MS)
SAFE = re.compile(r"[^A-Za-z0-9._-]")
SESSION_FILE = re.compile(r"carl-session\+[A-Za-z0-9._+-]{1,300}\.bin")


def safe_name(s: str, n: int) -> str:
    """The client's safeName (carl-cache.js): the same file names on both sides."""
    return SAFE.sub("_", s)[:n]


class CacheState:
    """The claim and record files of one slots folder (shared with the local clients)."""

    def __init__(self, folder: str) -> None:
        self.folder = folder
        self.lock = threading.Lock()

    def _claim_path(self, model: str, slot: int) -> str:
        return os.path.join(self.folder, f".claim+{safe_name(model, 60)}+{slot}")

    def _record_path(self, model: str, slot: int) -> str:
        return os.path.join(self.folder, f".resident+{safe_name(model, 60)}+{slot}.json")

    def claim(self, model: str, slot: int) -> bool:
        path = self._claim_path(model, slot)
        with self.lock:
            for _ in range(2):
                try:
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                    os.write(fd, b"api")
                    os.close(fd)
                    return True
                except FileExistsError:
                    try:
                        if time.time() - os.path.getmtime(path) <= CLAIM_STALE_S:
                            return False
                        os.remove(path)
                    except OSError:
                        pass
                except OSError:
                    return True                 # a folder it can't write: no claims possible
            return False

    def release(self, model: str, slot: int) -> None:
        try:
            os.remove(self._claim_path(model, slot))
        except OSError:
            pass

    def record(self, model: str, slot: int, file: str, task: int, base: int) -> None:
        path = self._record_path(model, slot)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"model": model, "slot": slot, "file": file, "task": task, "base": base}, f)
        os.replace(path + ".tmp", path)

    def recorded(self, model: str, file: str) -> Optional[JSONDict]:
        head = f".resident+{safe_name(model, 60)}+"
        try:
            names = os.listdir(self.folder)
        except OSError:
            return None
        for n in names:
            if not (n.startswith(head) and n.endswith(".json")):
                continue
            try:
                with open(os.path.join(self.folder, n), encoding="utf-8") as f:
                    doc = jdict(json.load(f))
            except (OSError, ValueError):
                continue
            if doc.get("model") == model and doc.get("file") == file:
                return {"slot": doc.get("slot"), "task": doc.get("task"), "base": doc.get("base") or 0}
        return None

    def unrecord(self, model: str, slot: int) -> None:
        try:
            os.remove(self._record_path(model, slot))
        except OSError:
            pass

    def _turn_path(self, model: str, slot: int) -> str:
        return os.path.join(self.folder, f".turn+{safe_name(model, 60)}+{slot}")

    def turn(self, model: str, slot: int, session: str, running: bool) -> None:
        """A turn runs in the slot (each request refreshes it), or it ended (only its own mark goes)."""
        path = self._turn_path(model, slot)
        if running:
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                json.dump({"model": model, "slot": slot, "session": session}, f)
            os.replace(path + ".tmp", path)
            return
        try:
            with open(path, encoding="utf-8") as f:
                if jdict(json.load(f)).get("session") == session:
                    os.remove(path)
        except (OSError, ValueError):
            pass

    def turns(self, model: str, now: Optional[float] = None) -> List[int]:
        """The slots of a model where a client's turn runs (marks newer than TURN_STALE_S)."""
        return sorted(slot for slot, _ in self.turn_marks(model, now))

    def turn_marks(self, model: str, now: Optional[float] = None) -> List[Tuple[int, str]]:
        """(slot, session) of each running turn of a model."""
        now = time.time() if now is None else now
        head = f".turn+{safe_name(model, 60)}+"
        out: List[Tuple[int, str]] = []
        try:
            names = os.listdir(self.folder)
        except OSError:
            return out
        for n in names:
            if n.startswith(head) and n[len(head):].isdigit():
                path = os.path.join(self.folder, n)
                try:
                    if now - os.path.getmtime(path) <= TURN_STALE_S:
                        with open(path, encoding="utf-8") as f:
                            out.append((int(n[len(head):]), str(jdict(json.load(f)).get("session") or "")))
                except (OSError, ValueError):
                    continue
        return out


CLIENT_ID = re.compile(r"[0-9a-f]{6,32}")
CLIENT_KEYS = ("host", "user", "os", "applied", "mode")


@dataclass
class Client:
    """A client that syncs (client/carl-sync.py), as it last said it is."""
    id: str
    host: str = ""
    user: str = ""
    os: str = ""
    applied: str = ""                    # the client config version it has
    auto_apply: bool = True
    mode: str = ""                       # "service" (it listens) or "check" (OpenCode / Pi check at start)
    address: str = ""
    last_seen: float = 0.0
    connected: int = 0                   # its open event streams (the service: 1)


def parse_client(header: str) -> Optional[Client]:
    """A client from its X-Carl-Client header (JSON, checked: the id, short printable fields); None if bad."""
    if not header or len(header) > 1024:
        return None
    try:
        doc = jdict(json.loads(header))
    except ValueError:
        return None
    cid = doc.get("id")
    if not isinstance(cid, str) or not CLIENT_ID.fullmatch(cid):
        return None
    f = {k: "".join(c for c in str(doc.get(k) or "") if c.isprintable())[:60] for k in CLIENT_KEYS}
    return Client(cid, host=f["host"], user=f["user"], os=f["os"], applied=f["applied"], mode=f["mode"],
                  auto_apply=doc.get("auto_apply") is not False)


class Registry:
    """The clients seen (~/.config/carl/clients.json: kept across dashboard restarts)."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.clients: Dict[str, Client] = {}
        self.saved_at = 0.0
        try:
            with open(path, encoding="utf-8") as f:
                for cid, v in jdict(json.load(f)).items():
                    c = parse_client(json.dumps({"id": cid, **jdict(v)}))
                    if c:
                        c.address, c.last_seen = str(jdict(v).get("address", ""))[:60], float(jdict(v).get("last_seen") or 0)
                        self.clients[cid] = c
        except (OSError, ValueError, TypeError):
            pass

    def seen(self, c: Client, address: str, connected: int = 0) -> None:
        with self.lock:
            old = self.clients.get(c.id)
            c.address, c.last_seen = address, time.time()
            c.connected = (old.connected if old else 0) + connected
            changed = old is None or (old.applied, old.mode, old.auto_apply) != (c.applied, c.mode, c.auto_apply)
            self.clients[c.id] = c
            if changed or time.time() - self.saved_at > 60:
                self._save()

    def gone(self, cid: str) -> None:
        """A client's event stream ended: one connection less, seen now; what it reported since (its
        applied config) stays, not the values from when the stream started."""
        with self.lock:
            c = self.clients.get(cid)
            if c:
                c.connected, c.last_seen = max(c.connected - 1, 0), time.time()
                self._save()

    def _save(self) -> None:
        doc = {cid: {k: getattr(c, k) for k in (*CLIENT_KEYS, "auto_apply", "address", "last_seen")}
               for cid, c in self.clients.items()}
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path + ".tmp", "w", encoding="utf-8") as f:
                json.dump(doc, f, indent=1)
            os.replace(self.path + ".tmp", self.path)
            self.saved_at = time.time()
        except OSError:
            pass

    def forget(self, cid: str) -> None:
        with self.lock:
            self.clients.pop(cid, None)
            self._save()

    def list(self) -> List[Client]:
        with self.lock:
            return sorted(self.clients.values(), key=lambda c: -c.last_seen)


def closed(conn: socket.socket, wait: float) -> bool:
    """Has the other end closed the connection? Waits up to `wait` s for it (a client of the event
    stream sends nothing: readable then means it left)."""
    try:
        ready, _, _ = select.select([conn], [], [], wait)
        return bool(ready) and conn.recv(1, socket.MSG_PEEK) == b""
    except (OSError, ValueError):
        return True


Reply = Tuple[int, JSONDict]


def check_slot(doc: JSONDict) -> Tuple[str, int]:
    """(model, slot) from a request body; ValueError when they aren't usable."""
    model, slot = doc.get("model"), doc.get("slot")
    if not isinstance(model, str) or not model or len(model) > 200:
        raise ValueError("model")
    if not isinstance(slot, int) or isinstance(slot, bool) or not 0 <= slot < MAX_SLOT:
        raise ValueError("slot")
    return model, slot


def handle(state: CacheState, conf: Callable[[], CacheConfig], method: str, path: str, query: Dict[str, str],
           body: JSONDict, save_recorded: Callable[[str], int] = lambda model: 0, carl_dir: str = "",
           etag: str = "") -> Reply:
    """One API call (pure, except the files and save_recorded): (status, JSON). etag: the client's
    If-None-Match (the config version it has)."""
    try:
        if method == "GET" and path == "/carl/client/config":
            doc = clientsync.published(carl_dir) if carl_dir else None
            if doc is None:
                return 404, {"error": "no client config published yet (the Connect tab's Push, or ./carl.sh push)"}
            return (304, {}) if etag.strip('"') == doc["version"] else (200, doc)
        if method == "POST" and path == "/carl/cache/unpack":
            ufile = body.get("file")
            if not isinstance(ufile, str) or not SESSION_FILE.fullmatch(ufile):
                return 400, {"error": "a session file name"}
            return (200, {"ok": True}) if slotpack.unpack(state.folder, ufile) else (404, {"error": "no such state"})
        if method == "POST" and path == "/carl/cache/save-recorded":
            model = body.get("model")
            if not isinstance(model, str) or not model or len(model) > 200:
                return 400, {"error": "model"}
            return 200, {"saved": save_recorded(model)}
        if method == "GET" and path == "/carl/cache/settings":
            c = conf()
            return 200, {"prefix": c.prefix, "sessions": c.sessions, "save": c.save, "auto_s": c.auto_s,
                         "disk_gb": c.disk_gb}
        if method == "GET" and path == "/carl/cache/turns":
            tmodel = query.get("model", "")
            if not tmodel or len(tmodel) > 200:
                return 400, {"error": "model"}
            return 200, {"turns": [{"slot": sl, "session": ses} for sl, ses in sorted(state.turn_marks(tmodel))]}
        if method == "GET" and path == "/carl/cache/record":
            model, file = query.get("model", ""), query.get("file", "")
            if not model or not SESSION_FILE.fullmatch(file):
                return 400, {"error": "model and a session file name"}
            rec = state.recorded(model, file)
            return (200, rec) if rec else (404, {"error": "no record"})
        if method != "POST":
            return 404, {"error": "not found"}
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
            if not (isinstance(session, str) and 0 < len(session) <= 200 and isinstance(running, bool)):
                return 400, {"error": "session and running"}
            state.turn(model, slot, session, running)
            return 200, {"ok": True}
        return 404, {"error": "not found"}
    except ValueError as e:
        return 400, {"error": f"bad {e}"}
    except OSError as e:
        return 500, {"error": e.strerror or "file error"}


class CacheApi:
    """The HTTP server (a thread); start() returns why it couldn't start, else None."""

    def __init__(self, host: str, port: int, key: Callable[[], str], folder: str, conf: Callable[[], CacheConfig],
                 save_recorded: Callable[[str], int] = lambda model: 0, carl_dir: str = "") -> None:
        self.host, self.port, self.key, self.conf, self.save_recorded = host, port, key, conf, save_recorded
        self.carl_dir = carl_dir
        self.listeners = 0                       # clients holding /carl/client/events
        self.registry = Registry(os.path.join(carl_dir, "clients.json") if carl_dir else os.devnull)
        self.state = CacheState(folder)
        self.server: Optional[ThreadingHTTPServer] = None

    def start(self) -> Optional[str]:
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:      # no access log on the dashboard's terminal
                pass

            def _reply(self, status: int, doc: JSONDict) -> None:
                data = b"" if status == 304 else json.dumps(doc).encode()
                self.send_response(status)
                if status != 304:
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                version = doc.get("version") if status == 200 else None
                if isinstance(version, str):
                    self.send_header("ETag", f'"{version}"')
                self.end_headers()
                self.wfile.write(data)

            def _events(self) -> None:
                """Server-sent events until the client goes: the published version now and on change."""
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                api.listeners += 1
                who = parse_client(self.headers.get("X-Carl-Client", ""))
                if who:
                    api.registry.seen(who, self.client_address[0], connected=1)
                sent, ping = None, 0.0
                try:
                    while api.server is not None:
                        doc = clientsync.published(api.carl_dir) if api.carl_dir else None
                        version = doc["version"] if doc else None
                        if version and version != sent:
                            self.wfile.write(f"event: config\ndata: {json.dumps({'version': version})}\n\n".encode())
                            self.wfile.flush()
                            sent = version
                        if time.time() - ping > 25:
                            self.wfile.write(b": ping\n\n")
                            self.wfile.flush()
                            ping = time.time()
                        if closed(self.connection, 2.0):     # waits up to 2 s; a client that left: at once
                            break
                except OSError:
                    pass                          # the client went away
                finally:
                    api.listeners -= 1
                    if who:
                        api.registry.gone(who.id)

            def _call(self, method: str) -> None:
                want = api.key()
                got = self.headers.get("Authorization", "")
                if want and not hmac.compare_digest(got.encode(), f"Bearer {want}".encode()):
                    self._reply(401, {"error": "unauthorized"})
                    return
                url = urllib.parse.urlsplit(self.path)
                who = parse_client(self.headers.get("X-Carl-Client", ""))
                if who and url.path != "/carl/client/events":
                    api.registry.seen(who, self.client_address[0])
                if method == "GET" and url.path == "/carl/client/events":
                    self._events()
                    return
                query = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
                body: JSONDict = {}
                if method == "POST":
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > MAX_BODY:
                        self._reply(413, {"error": "too large"})
                        return
                    try:
                        body = jdict(json.loads(self.rfile.read(n) or b"{}"))
                    except ValueError:
                        self._reply(400, {"error": "not JSON"})
                        return
                self._reply(*handle(api.state, api.conf, method, url.path, query, body, api.save_recorded, api.carl_dir,
                                    self.headers.get("If-None-Match", "")))

            def do_GET(self) -> None:
                self._call("GET")

            def do_POST(self) -> None:
                self._call("POST")

        try:
            self.server = ThreadingHTTPServer((self.host, self.port), Handler)
        except OSError as e:
            return f"cache API: port {self.port} on {self.host}: {e.strerror or e}"
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return None

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
