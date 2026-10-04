"""Takes a snapshot of the server and the Mac: its process (ps, netstat), its HTTP API
(/health, /slots, /metrics, /props), memory and power, and
its log. Read-only towards the server.

A llama.cpp router (router mode) answers /props with role "router" and needs ?model= on
/slots, /metrics and /props: the collector lists its models (/models), asks only about the
loaded one, always with autoload=false (a query must never load a model), and takes the
model server's own process (its port from the router's /models) for memory and flags."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from . import fsio, gguf, system
from .api import FETCH_ERRORS, Endpoint
from .logtail import LogTail
from .model import JSONDict, RouterInfo, ServerData, Shape, SlotInfo, SlowStats, clean_json, flag, jdict
from .system import TcpRow

_POSITION = re.compile(r'position="(\d+)"')
# Unexpected JSON shapes from a server (a list where an object belongs, ...): treated as no data.
SHAPE_ERRORS = (AttributeError, TypeError, IndexError, KeyError)


def get_json(ep: Endpoint, path: str, timeout: float = 2) -> object:
    """Parsed JSON from the server, its strings cleaned of control characters. Raises one of FETCH_ERRORS."""
    return clean_json(json.loads(ep.get(path, timeout)))


def parse_metrics(text: str) -> Tuple[Dict[str, float], Dict[int, float]]:
    """Prometheus /metrics text: (values by name without the llamacpp: prefix, draft
    tokens accepted by position). Lines that are not "name value" are skipped."""
    metrics: Dict[str, float] = {}
    by_pos: Dict[int, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        key, _, v = line.rpartition(" ")
        try:
            num = float(v)
        except ValueError:
            continue
        mm = _POSITION.search(key)
        if mm:
            by_pos[int(mm.group(1))] = num
        elif key:
            metrics[key.replace("llamacpp:", "")] = num
    return metrics, by_pos


@dataclass
class Sample:
    """Progress of the busy request at one refresh: live rates come from two samples."""
    task: object
    t: float
    processed: int
    decoded: int


def live_rates(d: ServerData, last: Optional[Sample]) -> Tuple[Optional[float], Optional[float]]:
    """(prompt, generation) tokens per second since the last sample, same request only."""
    if last and d.busy and last.task == d.task:
        dt = d.t - last.t
        if dt > 0:
            return (max(0, d.processed - last.processed) / dt or None, max(0, d.decoded - last.decoded) / dt or None)
    return None, None


def choose_log(cmd: str, log_arg: Optional[str], console: str, home: str) -> Optional[str]:
    """The log to show: the server's --log-file (cmd: its command line; or --log), else its
    console output, else the latest llama.cpp log."""
    path = flag(cmd, "--log-file") or log_arg
    if not path or not os.path.exists(path) or os.path.getsize(path) == 0:
        return console if os.path.exists(console) else (path or os.path.join(home, "models/logs/llama-server-latest.log"))
    return path


class Collector:
    """Snapshots of one server. Keeps what is slow to get between refreshes: the server's
    pid, /props (every 30 s), the model's GGUF shape, slow stats and the GPU limit."""

    def __init__(self, endpoint: Endpoint, fixed_host: bool, key_file: str, server_pid: Optional[int],
                 console: Optional[str], log_arg: Optional[str], home: str, page: int) -> None:
        self.endpoint = endpoint
        self.fixed_host = fixed_host        # --host given: don't follow the listening address
        self.key_file = key_file
        self.server_pid = server_pid        # the launcher's server (or one a restart started)
        self.console = console              # its console output file
        self.log_arg = log_arg
        self.home = home
        self.page = page
        self.tail = LogTail()
        self.pid: Optional[int] = None
        self.pid_t = 0.0
        self.props: JSONDict = {}           # /props of the server (a router's own when it is one)
        self.props_t = 0.0
        self.model_props: Tuple[str, float, JSONDict] = ("", 0.0, {})   # a router's loaded model: (id, time, /props)
        self.last: Optional[Sample] = None
        self.model_path: Optional[str] = None
        self.shape: Optional[Shape] = None
        self.model_size: Optional[int] = None
        self.slow = SlowStats()                         # filled by slow_loop
        self.gpu_limit: Optional[Tuple[int, str]] = None   # filled by a background thread

    def follow(self, port: int, pid: Optional[int], console: str) -> None:
        """Watch the server on port (a start for another port moves the dashboard there)."""
        if port != self.endpoint.port:
            self.endpoint.port = port
            self.pid, self.pid_t, self.props, self.props_t, self.last = pid, time.time(), {}, 0.0, None
        self.server_pid, self.pid, self.console = pid, pid, console

    def find_pid(self, tcp: Callable[[], List[TcpRow]]) -> Optional[int]:
        """The pid listening on the port, looked up again when it dies or every 20 s."""
        now = time.time()
        if not system.pid_alive(self.pid) or now - self.pid_t > 20:
            self.pid, self.pid_t = system.listen_pid(tcp(), self.endpoint.port), now
        return self.pid

    def slow_loop(self) -> None:
        """Background thread: refresh the slow stats every minute."""
        while True:
            self.slow = system.read_slow(lambda: self.pid, os.path.join(self.home, "models"))
            time.sleep(60)

    def load_gpu_limit(self) -> None:
        """Background thread: the GPU limit (may run a Swift probe once)."""
        self.gpu_limit = gguf.gpu_limit()

    def collect(self) -> ServerData:
        """A snapshot now; also reads the new log lines."""
        ep = self.endpoint
        d = ServerData(t=time.time())
        rows: List[List[TcpRow]] = []

        def tcp() -> List[TcpRow]:         # netstat at most once per refresh
            if not rows:
                rows.append(system.netstat_tcp())
            return rows[0]

        if not self.fixed_host:
            h = system.listen_host(tcp(), ep.port)
            if h and h != ep.host:
                ep.host = h
        ep.key = fsio.read_key(self.key_file) or ep.key
        self._process(d, tcp)
        self._http(d)
        server_cmd = d.cmd
        self._router_child(d, tcp)
        self._model(d)
        d.pp_rate, d.tg_rate = live_rates(d, self.last)
        if d.slots:
            self.last = Sample(d.task, d.t, d.processed, d.decoded)
        d.system = system.read_system(self.page)
        console = self.console or os.path.join(self.home, f"models/logs/.console-{ep.port}.out")
        d.log_path = choose_log(server_cmd, self.log_arg, console, self.home)
        self.tail.update(d.log_path)
        return d

    def _process(self, d: ServerData, tcp: Callable[[], List[TcpRow]]) -> None:
        pid = self.find_pid(tcp)
        d.pid = pid
        if pid:
            info = system.process_info(pid)
            if info:
                d.rss, d.cpu, d.etime, d.cmd = info.rss, info.cpu, info.etime, info.cmd
            d.awake = system.kept_awake(pid)
            d.conns = system.connections(tcp(), self.endpoint.port, pid)
        if self.server_pid and not system.pid_alive(self.server_pid):
            d.exited = True
        d.target_pid = self.server_pid or pid

    def _model(self, d: ServerData) -> None:
        """The GGUF shape of the model on the server's command line (read once per file)."""
        mpath = flag(d.cmd, "-m", "--model")
        if mpath and mpath != self.model_path and os.path.isfile(mpath):
            self.model_path, self.shape, self.model_size = mpath, gguf.read_shape(mpath), os.path.getsize(mpath)
        d.shape = self.shape if mpath else None

    def _router_child(self, d: ServerData, tcp: Callable[[], List[TcpRow]]) -> None:
        """Router mode: memory, CPU and the command line of the loaded model's own server."""
        cur = d.router.current if d.router else None
        port = flag(" ".join(cur.args), "--port") if cur else None
        pid = system.listen_pid(tcp(), int(port)) if port and port.isdigit() else None
        info = system.process_info(pid) if pid else None
        if d.router is not None:
            d.cmd, d.rss, d.cpu = (info.cmd, info.rss, info.cpu) if info else ("", None, 0.0)

    def _http(self, d: ServerData) -> None:
        """Fill d from the server's API (a router: from its loaded model, see the module doc)."""
        ep = self.endpoint
        try:
            t0 = time.time()
            ep.get("/health")
            d.health_ms, d.up = (time.time() - t0) * 1000, True
        except FETCH_ERRORS:
            return
        if time.time() - self.props_t > 30:
            try:
                self.props = jdict(get_json(ep, "/props"))
            except FETCH_ERRORS:
                pass
            self.props_t = time.time()
        q = ""
        if self.props.get("role") == "router":
            try:
                d.router = RouterInfo.from_json(get_json(ep, "/models"))
            except FETCH_ERRORS + SHAPE_ERRORS:
                d.router = RouterInfo()
            cur = d.router.current
            if cur is None or cur.status != "loaded":
                d.props = {}
                return
            q = f"?model={urllib.parse.quote(cur.id, safe='')}&autoload=false"
            mid, t, props = self.model_props
            if mid != cur.id or time.time() - t > 30:
                try:
                    props = jdict(get_json(ep, "/props" + q))
                except FETCH_ERRORS:
                    props = {}
                self.model_props = (cur.id, time.time(), props)
            d.props = props
        else:
            d.props = self.props
        try:
            all_slots = get_json(ep, "/slots" + q)
            if not isinstance(all_slots, list) or not all(isinstance(x, dict) for x in all_slots):
                raise TypeError("/slots is not a list of slots")
            d.slot_list = [SlotInfo.from_json(x) for x in all_slots]
            busy = [x for x in d.slot_list if x.busy]
            s = busy[0] if busy else d.slot_list[0]
            d.n_ctx, d.busy, d.prompt, d.cached, d.processed, d.decoded, d.task = (
                s.n_ctx, s.busy, s.prompt, s.cached, s.processed, s.decoded, s.task)
            d.slots = True
        except FETCH_ERRORS + SHAPE_ERRORS:
            d.slots = False
        try:
            d.metrics, d.accepted_by_pos = parse_metrics(ep.get("/metrics" + q))
        except FETCH_ERRORS:
            pass
