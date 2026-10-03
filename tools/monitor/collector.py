"""Takes a snapshot of the server and the Mac: its process (ps, netstat), its HTTP API
(/health, /slots, /metrics, /props, MTPLX's snapshot and flight), memory and power, and
its log. Read-only towards the server."""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import fsio, gguf, system
from .api import FETCH_ERRORS, Endpoint
from .logtail import LogTail
from .model import JSONDict, ServerData, Shape, SlotInfo, SlowStats, clean_json, flag, jdict, jint, jlist, jnum, jtask
from .system import TcpRow

_POSITION = re.compile(r'position="(\d+)"')
# Unexpected JSON shapes from a server (a list where an object belongs, ...): treated as no data.
SHAPE_ERRORS = (AttributeError, TypeError, IndexError, KeyError)


def get_json(ep: Endpoint, path: str, timeout: float = 2) -> Any:
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


def apply_mtplx(d: ServerData, mx: JSONDict, flight: JSONDict) -> Tuple[Optional[float], Optional[float]]:
    """Fill d from MTPLX's snapshot and flight; returns its own (prompt, generation) rates."""
    d.backend, d.mx = "mtplx", mx
    d.n_ctx = jint(mx.get("context_window"))
    d.busy = bool(mx.get("active_requests"))
    d.prompt = d.cached = d.processed = d.decoded = 0
    d.task = None
    inf = jdict((jlist(mx.get("in_flight")) or [None])[0])
    act = jdict((jlist(flight.get("active")) or [None])[0])
    d.flight = act
    pp: Optional[float] = None
    tg: Optional[float] = None
    if inf:
        ps = jdict(inf.get("prefill_state"))
        d.prompt = jint(inf.get("prompt_tokens")) or jint(ps.get("tokens_total")) or jint(act.get("prompt_tokens"))
        d.task = jtask(inf.get("request_id"))
        if act.get("phase") == "prefill" or (ps and not act.get("gen_tokens")):
            d.cached = jint(ps.get("cached_tokens"))
            d.processed = max(jint(ps.get("tokens_done")) - d.cached, 0)
            pp = jnum(ps.get("live_prefill_tok_s")) or jnum(ps.get("prefill_tok_s"))
        else:
            d.processed = d.prompt
            d.decoded = jint(act.get("gen_tokens")) or 1
            tg = jnum(act.get("tps_now")) or jnum(act.get("tps_avg"))
    return pp, tg


def choose_log(d: ServerData, log_arg: Optional[str], console: str, home: str, now: float) -> Optional[str]:
    """The log to show: the server's --log-file (or --log), else its console output (all MTPLX
    has: only when it is newer than the server), else the latest llama.cpp log."""
    path = flag(d.cmd, "--log-file") or log_arg
    if d.backend == "mtplx" or "mtplx serve" in d.cmd:
        fresh = os.path.exists(console) and (not d.pid or os.path.getmtime(console) >= now - system.etime_seconds(d.etime))
        return console if fresh else None
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
        self.props: JSONDict = {}
        self.props_t = 0.0
        self.last: Optional[Sample] = None
        self.model_path: Optional[str] = None
        self.shape: Optional[Shape] = None
        self.model_size: Optional[int] = None
        self.slow = SlowStats()                         # filled by slow_loop
        self.gpu_limit: Optional[Tuple[int, str]] = None   # filled by a background thread

    def follow(self, port: int, pid: Optional[int], console: str) -> None:
        """Watch the server on port (a backend switch changes the port)."""
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
        self._model(d)
        mx_rates = self._http(d)
        d.pp_rate, d.tg_rate = live_rates(d, self.last)
        if d.slots:
            self.last = Sample(d.task, d.t, d.processed, d.decoded)
        if d.backend == "mtplx":
            d.pp_rate, d.tg_rate = mx_rates
        d.system = system.read_system(self.page)
        console = self.console or os.path.join(self.home, f"models/logs/.console-{ep.port}.out")
        d.log_path = choose_log(d, self.log_arg, console, self.home, time.time())
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

    def _http(self, d: ServerData) -> Tuple[Optional[float], Optional[float]]:
        """Fill d from the server's API; returns MTPLX's own (prompt, generation) rates."""
        ep = self.endpoint
        try:
            t0 = time.time()
            ep.get("/health")
            d.health_ms, d.up = (time.time() - t0) * 1000, True
        except FETCH_ERRORS:
            return None, None
        try:
            all_slots = get_json(ep, "/slots")
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
            d.metrics, d.accepted_by_pos = parse_metrics(ep.get("/metrics"))
        except FETCH_ERRORS:
            pass
        if time.time() - self.props_t > 30:
            try:
                self.props = jdict(get_json(ep, "/props"))
            except FETCH_ERRORS:
                pass
            self.props_t = time.time()
        d.props = self.props
        if not d.slots:         # MTPLX: no /slots; its own snapshot (+ flight for live decode) instead
            try:
                mx = get_json(ep, "/v1/mtplx/snapshot", 4)
            except FETCH_ERRORS:
                return None, None
            if not isinstance(mx, dict):
                return None, None
            try:
                flight = jdict(get_json(ep, "/v1/mtplx/flight", 2))
            except FETCH_ERRORS:
                flight = {}
            return apply_mtplx(d, mx, flight)
        return None, None
