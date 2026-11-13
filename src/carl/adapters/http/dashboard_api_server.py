"""The dashboard API's HTTP server (while the dashboard runs), for OpenCode and Pi on other computers: the key, the
request body, the event stream and the addresses; each call's route is the application's (application/cache_api.py,
contracts C-300 to C-314), handed in as `handle` by the composition (composition/cache_api.py).

  GET  /carl/ping                            no key: {carl: this CARL's ID, version} (a client checks it before it
                                              sends the key, Phase 23.5)
  GET  /carl/client/events                    server-sent events: "config" with the version when a
                                              new config is published (the client connects out: no
                                              port opens on the client), a comment every 25 s
  everything else                            handle(method, path, query, body, If-None-Match, the caller's address)

It listens next to llama-server (its address, port + 1) and wants the same API key (Bearer); with no
key known it refuses every call (fail closed). Inputs are checked (a small body, a time limit per connection);
nothing is logged on the dashboard's terminal. The clients that call are kept in the registry
(adapters/common/client_registry.py).
"""
from __future__ import annotations

import errno
import hmac
import json
import select
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, Optional, Tuple

from ...domain.server.snapshot import JSONDict, jdict
from ..common.client_registry import Registry, parse_client

MAX_BODY = 4096
CONN_TIMEOUT_S = 30      # a connection that sends or reads nothing for this long is closed
Reply = Tuple[int, JSONDict]
# one call: (method, path, query, body, the client's If-None-Match, the caller's address) -> (status, JSON)
Route = Callable[[str, str, Dict[str, str], JSONDict, str, str], Reply]


def closed(conn: socket.socket, wait: float) -> bool:
    """Has the other end closed the connection? Waits up to `wait` s for it (a client of the event
    stream sends nothing: readable then means it left)."""
    try:
        ready, _, _ = select.select([conn], [], [], wait)
        return bool(ready) and conn.recv(1, socket.MSG_PEEK) == b""
    except (OSError, ValueError):
        return True


def body_length(header: Optional[str]) -> Optional[int]:
    """The request body's size from its Content-Length (none: 0); None when it is not a number."""
    text = (header or "0").strip()
    return int(text) if text.isdigit() else None


def authorized(want: str, got: str) -> bool:
    """The Authorization header carries the key (compared in constant time); no key: nobody is."""
    return bool(want) and hmac.compare_digest(got.encode(), f"Bearer {want}".encode())


class QuietServer(ThreadingHTTPServer):
    """A threading HTTP server that writes nothing on the terminal: a broken connection or a bad
    request ends that request only (the dashboard draws on stdout)."""
    daemon_threads = True

    def server_bind(self) -> None:
        """Windows (W6.8): the port for this server alone (SO_EXCLUSIVEADDRUSE), not SO_REUSEADDR, with which a bind
        there shares a port another program holds; elsewhere as before."""
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            self.allow_reuse_address = False
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        super().server_bind()

    def handle_error(self, request: object, client_address: object) -> None:
        pass


class QuietServer6(QuietServer):
    address_family = socket.AF_INET6


class HttpApi:
    """The HTTP server (a thread); start() returns why it couldn't start, else None. handle: every keyed call but the
    event stream; published(): the client config now (None: none yet; the event stream sends its version)."""

    def __init__(self, host: str, port: int, key: Callable[[], str], handle: Route,
                 published: Callable[[], Optional[JSONDict]], registry: Registry, carl_version: str = "",
                 carl_id: str = "") -> None:
        self.host, self.port, self.key, self.handle, self.published = host, port, key, handle, published
        self.carl_id = carl_id                   # /carl/ping (no key): this CARL's ID (runtime_files.carl_id)
        self.server6: Optional[QuietServer] = None
        self.carl_version = carl_version         # sent as X-Carl-Version: /carl on a client compares its package
        self.listeners = 0                       # clients holding /carl/client/events
        self._listeners_lock = threading.Lock()
        self.registry = registry
        self.server: Optional[QuietServer] = None

    def _listening(self, step: int) -> None:
        with self._listeners_lock:
            self.listeners += step

    def start(self) -> Optional[str]:
        api = self

        class Handler(BaseHTTPRequestHandler):
            timeout = CONN_TIMEOUT_S                         # a slow or silent client can't hold a thread

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
                if api.carl_version:
                    self.send_header("X-Carl-Version", api.carl_version)
                self.end_headers()
                self.wfile.write(data)

            def _events(self) -> None:
                """Server-sent events until the client goes: the published version now and on change."""
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                if api.carl_version:
                    self.send_header("X-Carl-Version", api.carl_version)
                self.end_headers()
                api._listening(1)
                who = parse_client(self.headers.get("X-Carl-Client", ""))
                if who:
                    api.registry.seen(who, self.client_address[0], connected=1)
                sent, ping = None, 0.0
                try:
                    while api.server is not None:
                        doc = api.published()
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
                    api._listening(-1)
                    if who:
                        api.registry.gone(who.id)

            def _call(self, method: str) -> None:
                if method == "GET" and urllib.parse.urlsplit(self.path).path == "/carl/ping":
                    # no key (Phase 23.5): a client asks first whether this port is CARL's (the ID it knows), and
                    # sends its key only then
                    self._reply(200, {"carl": api.carl_id, "version": api.carl_version})
                    return
                if not authorized(api.key(), self.headers.get("Authorization", "")):
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
                    n = body_length(self.headers.get("Content-Length"))
                    if n is None:
                        self._reply(400, {"error": "bad Content-Length"})
                        return
                    if n > MAX_BODY:
                        self._reply(413, {"error": "too large"})
                        return
                    try:
                        body = jdict(json.loads(self.rfile.read(n) or b"{}"))
                    except ValueError:
                        self._reply(400, {"error": "not JSON"})
                        return
                self._reply(*api.handle(method, url.path, query, body, self.headers.get("If-None-Match", ""),
                                        self.client_address[0]))

            def do_GET(self) -> None:
                self._call("GET")

            def do_POST(self) -> None:
                self._call("POST")

        try:
            self.server = QuietServer((self.host, self.port), Handler)
        except OSError as e:
            return f"dashboard API: port {self.port} on {self.host}: {e.strerror or e}"
        if self.host == "127.0.0.1":
            # this Mac only: the same port on ::1 too, so a client that reaches "localhost" over IPv6 does not find
            # another program there (Phase 23.5: an ssh SOCKS proxy held [::1]:8081); no IPv6: v4 only
            try:
                self.server6 = QuietServer6(("::1", self.port), Handler)
            except OSError as e:
                if e.errno in (errno.EADDRINUSE, errno.EACCES):
                    self.server.server_close()
                    self.server = None
                    return f"dashboard API: port {self.port} on ::1: {e.strerror or e}"
        for srv in (self.server, self.server6):
            if srv:
                threading.Thread(target=srv.serve_forever, daemon=True).start()
        return None

    def stop(self) -> None:
        for srv in (self.server, self.server6):
            if srv:
                srv.shutdown()
                srv.server_close()
        self.server = self.server6 = None
