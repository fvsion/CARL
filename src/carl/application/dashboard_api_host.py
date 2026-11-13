"""Where the dashboard API runs (Phase 23.5 item 7): next to the server, on llama.api_port, or with auto on the
server's port + 1, else the next free port; never on a port another CARL holds. It moves when the server moves or the
setting changes, and is tried again every RETRY_S while it does not run. The port it took goes into the server's file
(server-PORT.json) and the client folder's remote.json, where the clients look.

The HTTP server itself is an adapter (adapters/http/dashboard_api_server.py), which the composition builds
(composition/dashboard.py); this is the use case over it. ApiServer and ClientRegistry say what it needs of that
server until W5.3 splits the handlers from it."""
from __future__ import annotations

import os
import time
from typing import Callable, List, NamedTuple, Optional, Protocol, Tuple

from ..domain.server.snapshot import JSONDict, jdict
from ..ports import ServerWatch

RETRY_S = 30.0          # the dashboard API tries again this often while it does not run (Phase 23.5)
TRIES = 10              # auto: the ports tried (the server's port + 1, then the next free ones)


class ClientRegistry(Protocol):
    """The computers that synced (clients.json)."""

    def list(self) -> List[JSONDict]: ...


class ApiServer(Protocol):
    """The dashboard API's server for one address, built but not started."""
    host: str
    port: int
    listeners: int                      # clients holding the events stream
    registry: ClientRegistry

    def start(self) -> Optional[str]:
        """Run it; None when it runs, else why it cannot."""
        ...

    def stop(self) -> None: ...


class Placed(NamedTuple):
    """Where the API went after a try: its port (None: it does not run), the note for the API Port row, and whether
    this was a new place (the dashboard says so when the API cannot start there)."""
    port: Optional[int]
    note: str
    moved: bool


class DashboardApiHost:
    """Runs the dashboard API for the server the dashboard watches. make(host, port): the server for that address;
    publish(server port, API port, URL): the port into the server's file and remote.json; load_config: config.json
    (llama.api_port)."""

    def __init__(self, make: Callable[[str, int], ApiServer], publish: Callable[[int, int, str], None],
                 load_config: Callable[[], JSONDict], watch: ServerWatch) -> None:
        self.make = make
        self.publish = publish
        self.load_config = load_config
        self.watch = watch
        self.api: Optional[ApiServer] = None
        self._where: Optional[Tuple[str, int, object]] = None
        self._tried = 0.0
        self._conf: Tuple[float, object] = (0.0, "auto")

    def setting(self) -> object:
        """llama.api_port: "auto" or a port (read again every few seconds)."""
        if time.time() - self._conf[0] > 5:
            v = jdict(self.load_config().get("llama")).get("api_port", "auto")
            self._conf = (time.time(), v if isinstance(v, int) and 1024 <= v <= 65535 else "auto")
        return self._conf[1]

    def place(self, host: str, port: int) -> Optional[Placed]:
        """Run the API for the server on host:port; None when nothing changed (it runs there, or it failed there and
        the retry time has not passed)."""
        want = self.setting()
        where = (host, port, want)
        if self._where == where and (self.api or time.time() - self._tried < RETRY_S):
            return None
        moved = self._where != where
        if self.api:
            self.api.stop()
        self.api, self._where, self._tried = None, where, time.time()
        first = port + 1
        tries = [want] if isinstance(want, int) else [first]
        if not isinstance(want, int):
            sockets = self.watch.sockets()
            free: Optional[int] = first
            while len(tries) < TRIES and free is not None:
                free = sockets.next_free_port(free)
                if free is not None and free != port:
                    tries.append(free)
        err = ""
        for api_port in tries:
            api = self.make(host, api_port)
            err = api.start() or ""
            if not err:
                self.api = api
                note = "" if api_port == first or isinstance(want, int) else f"{first} is in use{self.port_user(first)}"
                self.publish(port, api_port, f"http://{host}:{api_port}")
                return Placed(api_port, note, moved)
        return Placed(None, err.removeprefix("dashboard API: "), moved)

    def port_user(self, port: int) -> str:
        """" (ssh)": what listens on port, for the API Port row."""
        pid = self.watch.sockets().listen_pid(port)
        info = self.watch.process(pid) if pid else None
        name = os.path.basename(info.cmd.split()[0]) if info and info.cmd.split() else ""
        return f" ({name})" if name else ""
