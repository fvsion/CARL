"""Takes a snapshot of the server and the computer: its process and sockets, its HTTP API
(/health, /slots, /metrics, /props), memory and power, and
its log. Read-only towards the server. The server and the computer are read only through ports (ServerEndpoint,
ServerWatch and its ServerFiles, GpuLimit, ShapeReader), which the composition picks for this computer
(composition/dashboard.py).

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

from ..domain.server.serverlog import counts_cache
from ..domain.server.snapshot import (JSONDict, RouterInfo, ServerData, Shape, SlotInfo, SlowStats, clean_json, flag,
                                      jdict)
from ..ports import GpuLimit, ServerEndpoint, ServerFiles, ServerWatch, ShapeReader, TcpTable
from .logbook import LogTail

_POSITION = re.compile(r'position="(\d+)"')
# What an answer of the server can fail with: no server, a timeout or a broken exchange (OSError), a body that is not
# UTF-8 or JSON (ValueError). The ServerEndpoint port (C-052) raises no other.
FETCH_ERRORS = (OSError, ValueError)
# Unexpected JSON shapes from a server (a list where an object belongs, ...): treated as no data.
SHAPE_ERRORS = (AttributeError, TypeError, IndexError, KeyError)


def get_json(ep: ServerEndpoint, path: str, timeout: float = 2) -> object:
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


STALE_S = 30.0          # how long the last /slots and /metrics stand in for ones that did not answer
SLOW_S = 60.0           # the slow stats (and the GPU limit, after its first read) are read again this often


def live_rates(d: ServerData, last: Optional[Sample]) -> Tuple[Optional[float], Optional[float]]:
    """(prompt, generation) tokens per second since the last sample, same request only."""
    if last and d.busy and last.task == d.task:
        dt = d.t - last.t
        if dt > 0:
            return (max(0, d.processed - last.processed) / dt or None, max(0, d.decoded - last.decoded) / dt or None)
    return None, None


def choose_log(files: ServerFiles, cmd: str, log_arg: Optional[str], console: str, home: str,
               filtered: str = "") -> Optional[str]:
    """The log to show: the server's --log-file (cmd: its command line), or filtered (the log CARL's filter writes
    for a server at -lv 4: it names this start's log, where --log can name an earlier start's after a restart from the
    dashboard), or --log; else its console output, else the latest llama.cpp log. files: where the sizes are read."""
    path = flag(cmd, "--log-file") or filtered or log_arg      # the filter's log first: --log names an older start
    if not path or not files.size(path):
        if files.size(console) is not None:
            return console
        return path or os.path.join(home, "models/logs/llama-server-latest.log")
    return path


class Collector:
    """Snapshots of one server. Keeps what is slow to get between refreshes: the server's
    pid, /props (every 30 s), the model's GGUF shape, slow stats and the GPU limit.
    endpoint: the server's address, key and API; watch: this computer and the server's process, sockets and files; gpu:
    what a model may use on the GPU (None: not read); shapes: the GGUF shapes of model files (None: not read)."""

    def __init__(self, endpoint: ServerEndpoint, fixed_host: bool, key_file: str, server_pid: Optional[int],
                 console: Optional[str], log_arg: Optional[str], home: str, watch: ServerWatch,
                 gpu: Optional[GpuLimit] = None, shapes: Optional[ShapeReader] = None) -> None:
        self.endpoint = endpoint
        self.fixed_host = fixed_host        # --host given: don't follow the listening address
        self.key_file = key_file
        self.server_pid = server_pid        # the launcher's server (or one a restart started)
        self.console = console              # its console output file
        self.log_arg = log_arg
        self.home = home
        self.watch = watch
        self.gpu = gpu
        self.shapes = shapes
        self.files = watch.files()
        self.tail = LogTail(self.files)
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
        self.good_slots: Tuple[float, List[SlotInfo]] = (0.0, [])         # the last /slots that answered
        self.good_metrics: Tuple[float, JSONDict, Dict[int, float]] = (0.0, {}, {})   # the last /metrics

    def follow(self, port: int, pid: Optional[int], console: str) -> None:
        """Watch the server on port (a start for another port moves the dashboard there). pid is the start's process:
        kept for the stop and `exited`; the snapshot's pid is the listener's from the next refresh on (on Windows the
        start, python.exe, stays the parent of llama-server, and its command line is not the server's)."""
        if port != self.endpoint.port:
            self.endpoint.port = port
            self.props, self.props_t, self.last = {}, 0.0, None
        self.server_pid, self.pid, self.pid_t, self.console = pid, pid, 0.0, console

    def find_pid(self, tcp: Callable[[], TcpTable]) -> Optional[int]:
        """The pid listening on the port, looked up again when it dies or every 20 s. After follow, at each refresh
        until a process listens; until then the followed one stays (the server is loading, not stopped)."""
        now = time.time()
        if not self.watch.alive(self.pid) or now - self.pid_t > 20:
            found = tcp().listen_pid(self.endpoint.port)
            if found is None and not self.pid_t and self.watch.alive(self.pid):
                return self.pid
            self.pid, self.pid_t = found, now
        return self.pid

    def slow_loop(self) -> None:
        """Background thread: refresh the slow stats every minute, and the GPU limit once its first read is done
        (load_gpu_limit; a discrete GPU's free memory changes, a Mac's limit is measured once and kept)."""
        while True:
            self.slow = self.watch.slow(lambda: self.pid, os.path.join(self.home, "models"))
            if self.gpu_limit is not None:
                self.load_gpu_limit()
            time.sleep(SLOW_S)

    def load_gpu_limit(self) -> None:
        """Background thread: the GPU limit through the GpuLimit port (on a Mac it may run a Swift probe once)."""
        if self.gpu is not None:
            limit, how = self.gpu.limit()
            self.gpu_limit = (int(limit), str(how))

    def collect(self) -> ServerData:
        """A snapshot now; also reads the new log lines."""
        ep = self.endpoint
        d = ServerData(t=time.time())
        rows: List[TcpTable] = []

        def tcp() -> TcpTable:              # the sockets read at most once per refresh
            if not rows:
                rows.append(self.watch.sockets())
            return rows[0]

        if not self.fixed_host:
            h = tcp().listen_host(ep.port)
            if h and h != ep.host:
                ep.host = h
        ep.key = self.watch.read_key(self.key_file) or ep.key
        self._process(d, tcp)
        self._http(d)
        server_cmd = d.cmd
        self._router_child(d, tcp)
        self._model(d)
        d.pp_rate, d.tg_rate = live_rates(d, self.last)
        if d.slots:
            self.last = Sample(d.task, d.t, d.processed, d.decoded)
        d.system = self.watch.memory()
        console = self.console or os.path.join(self.home, f"models/logs/.console-{ep.port}.out")
        pointer = self.files.pointer(self.home, ep.port) if counts_cache(server_cmd) else {}
        filtered = str(pointer.get("log") or "")
        d.log_path = choose_log(self.files, server_cmd, self.log_arg, console, self.home, filtered)
        # the server at -lv 4 runs, and its log filter does not (it writes the log): the HEALTH card says so
        fpid = pointer.get("pid")
        d.log_filter_stopped = bool(d.pid and isinstance(fpid, int) and not self.watch.alive(fpid))
        self.tail.update(d.log_path)
        return d

    def _process(self, d: ServerData, tcp: Callable[[], TcpTable]) -> None:
        pid = self.find_pid(tcp)
        d.pid = pid
        if pid:
            info = self.watch.process(pid)
            if info:
                d.rss, d.footprint, d.cpu, d.etime, d.cmd = info.rss, info.footprint, info.cpu, info.etime, info.cmd
            d.awake = self.watch.kept_awake(pid)
            d.conns = tcp().connections(self.endpoint.port, pid)
        if self.server_pid and not self.watch.alive(self.server_pid):
            d.exited = True
        d.target_pid = self.server_pid or pid

    def _model(self, d: ServerData) -> None:
        """The GGUF shape of the model on the server's command line (read once per file)."""
        mpath = flag(d.cmd, "-m", "--model")
        if mpath and mpath != self.model_path and self.shapes is not None:
            size = self.files.size(mpath)
            if size is not None:
                self.model_path, self.shape, self.model_size = mpath, self.shapes.local(mpath), size
        d.shape = self.shape if mpath else None

    def _router_child(self, d: ServerData, tcp: Callable[[], TcpTable]) -> None:
        """Router mode: memory, CPU and the command line of the loaded model's own server."""
        cur = d.router.current if d.router else None
        port = flag(" ".join(cur.args), "--port") if cur else None
        pid = tcp().listen_pid(int(port)) if port and port.isdigit() else None
        info = self.watch.process(pid) if pid else None
        if d.router is not None:
            d.cmd, d.rss, d.footprint, d.cpu = ((info.cmd, info.rss, info.footprint, info.cpu) if info
                                                else ("", None, None, 0.0))

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
            self.good_slots = (time.time(), d.slot_list)
        except FETCH_ERRORS + SHAPE_ERRORS:
            d.slots = False
            self._stale_slots(d)
        try:
            d.metrics, d.accepted_by_pos = parse_metrics(ep.get("/metrics" + q))
            self.good_metrics = (time.time(), d.metrics, d.accepted_by_pos)
        except FETCH_ERRORS:
            t, metrics, pos = self.good_metrics
            if time.time() - t <= STALE_S:
                d.metrics, d.accepted_by_pos = metrics, pos

    def _stale_slots(self, d: ServerData) -> None:
        """/slots did not answer while /health did: llama.cpp answers /slots only between two batches, and a batch of
        a long prompt can take longer than the poll waits. The last answer stands in (up to STALE_S old), with a
        request running."""
        t, slots = self.good_slots
        age = time.time() - t
        if not slots or age > STALE_S:
            return
        busy = [x for x in slots if x.busy]
        s = busy[0] if busy else slots[0]
        d.slot_list = slots
        d.n_ctx, d.prompt, d.cached, d.processed, d.decoded, d.task = (
            s.n_ctx, s.prompt, s.cached, s.processed, s.decoded, s.task)
        d.busy, d.slots, d.stale_s = True, True, age
