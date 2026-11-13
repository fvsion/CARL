"""The llama-server Auto-tune measures (implements TuneServer).

It is started through CARL's own start (python carl.py start, W3.3: so the measured server is the one CARL runs:
--port, --fit off), on its own port, local only, by ProcessControl (W6.9) as a process of its own, so a stop takes the
whole server down: its own session and process group on macOS and Linux, its own console on Windows, where the start
(MONITOR=0) keeps the server and its filter in a job that ends with it. With MONITOR=0 the start becomes llama-server
on macOS and Linux. Nothing here reads llama.cpp's buffer sizes from a log (the memory step uses CARL's estimate), so
no start needs -lv 4. The start's own lines are in the log: load_mode reads its load line (A44: the tune's starts follow
the start's rule). llama-batched-bench is the one the locator found (the same folder as llama-server).
"""
from __future__ import annotations

import http.client
import json
import os
import subprocess
import time
import urllib.request
import re
from typing import Callable, Dict, Mapping, Optional, Sequence, Set, Tuple

from ...domain.config.settings import LLAMA_KEYS, MODEL_KEYS
from ...domain.shared.constants import UBATCH
from ...domain.shared.errors import ConfigError
from ...domain.shared.types import JsonObject, JsonValue
from ...ports import ProcessControl

START_TIMEOUT = 600          # a cold load of a large model from disk takes minutes
STOP_TIMEOUT = 60
HEALTH_TIMEOUT = 2
PORT_FREE_WAIT = 60
PARALLEL_TIMEOUT = 1800      # llama-batched-bench: a dense 27B with 8 requests takes minutes
# a llama-batched-bench table row: | PP | TG | B | N_KV | T_PP s | S_PP t/s | T_TG s | S_TG t/s | T s | S t/s |
_BATCH_ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*\d+\s*\|\s*[\d.]+\s*\|\s*([\d.]+)\s*\|"
                        r"\s*[\d.]+\s*\|\s*([\d.]+)\s*\|")


def parse_batched(text: str) -> Dict[int, Tuple[float, float]]:
    """{requests: (decode tok/s, prompt tok/s)} from llama-batched-bench's table."""
    out: Dict[int, Tuple[float, float]] = {}
    for line in text.splitlines():
        m = _BATCH_ROW.match(line.strip())
        if m:
            out[int(m.group(3))] = (float(m.group(5)), float(m.group(4)))
    return out
# Variables the start would take over from the caller's environment: the tune sets each
# value itself (flags or below), so a value exported in the user's shell can't skew it.
UB = str(LLAMA_KEYS["ub"].env)       # the start's -ub (the tune sets it for each start)
PIN = "THREAD_PIN"                 # the start's threads with experts in the RAM: on or off (A54)
CLEARED_ENV = (str(MODEL_KEYS["ctx"].env), str(MODEL_KEYS["kv"].env), "KV_K", "KV_V", "MODEL",
               str(MODEL_KEYS["alias"].env), str(LLAMA_KEYS["cache_ram"].env), UB, PIN)
LOAD_LINE = "Load: "                # the start's line about the load mode (domain/memory/fit.py LoadMode.line)
KV_FLAG = {"q4_0": "q4", "q8_0": "q8", "f16": "f16"}      # the start's --kv words (the flag wins over the CPU's pick, A71)
# The server is on this computer: never through a proxy (Windows reads the system's proxy for urllib, and 127.0.0.1 is
# not one of its local names).
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class LlamaServerControl:
    """carl_py: the clone's carl.py, run by python as `start`; api_key: the server's key, read when a call is made (the
    first start of a new settings folder makes it, after this object was built); env: the environment the start gets
    (the caller's, less what the tune sets itself); listening: the ports in use (PortProbe); processes: starts and stops the server
    (ProcessControl); bench: the llama-batched-bench program (None: no parallel measure)."""

    def __init__(self, carl_py: str, model_path: str, port: int, api_key: Callable[[], str], log_path: str,
                 listening: Callable[[], Set[int]], processes: ProcessControl, env: Mapping[str, str],
                 python: str, bench: Optional[str] = None) -> None:
        self.carl_py = carl_py
        self.model_path = model_path
        self.port = port
        self.api_key = api_key
        self.log_path = log_path
        self.listening = listening
        self.processes = processes
        self.env = env
        self.python = python
        self.bench = bench
        self.pid: Optional[int] = None

    def call(self, path: str, body: Optional[JsonObject] = None, timeout: float = 1800) -> JsonValue:
        """POST body (or GET without one) to the server's API; the JSON answer."""
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {self.api_key()}", "content-type": "application/json"})
        with _DIRECT.open(req, timeout=timeout) as r:
            data: JsonValue = json.load(r)
            return data

    def _healthy(self) -> bool:
        try:
            answer = self.call("/health", timeout=HEALTH_TIMEOUT)
        except (OSError, ValueError, http.client.HTTPException):
            return False                       # still loading (503) or not listening yet
        return isinstance(answer, dict) and answer.get("status") == "ok"

    def _last_log_line(self) -> str:
        try:
            with open(self.log_path, encoding="utf-8", errors="replace", newline="") as f:
                lines = f.read().strip().splitlines()
        except OSError:
            return ""
        return lines[-1][:200] if lines else ""

    def start(self, spec: str, n: int, ctx: int, ub: Optional[int] = None, slots: int = 1,
              pin: Optional[bool] = None, kv: Optional[str] = None) -> float:
        self.stop()
        env = dict(self.env, SETTINGS_FILE="none", MONITOR="0", KEEP_AWAKE="0", LOG_FILE="none",
                   PORT=str(self.port), SLOTS=str(slots), SPEC=spec, SPEC_N=str(n), FIT_CHECK="0")
        for k in CLEARED_ENV:
            env.pop(k, None)
        if ub:
            env[UB] = str(ub)
        if pin is not None:                      # the threads step: pinned against unpinned (the start's own rule else)
            env[PIN] = "on" if pin else "off"
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        with open(self.log_path, "w", encoding="utf-8", newline=""):
            pass                                          # a fresh log: the spawn appends
        argv = [self.python, self.carl_py, "start", "--model", self.model_path, "--local", "--ctx", str(ctx),
                "--kv", KV_FLAG.get(kv or "q4_0", "q4")]
        pid = self.pid = self.processes.spawn(argv, env, self.log_path, True)
        t0 = time.monotonic()
        while time.monotonic() - t0 < START_TIMEOUT:
            if self.processes.wait(pid, 0) is not None:
                self.pid = None
                raise ConfigError("the server exited: " + self._last_log_line())
            if self._healthy():
                return time.monotonic() - t0
            time.sleep(1)
        raise ConfigError("the server did not start in 10 min")

    def load_mode(self) -> str:
        """The load mode of the last start from its load line in the log: none, mapped, or "" (no line: the free RAM
        was not known, or the server options set the load)."""
        try:
            with open(self.log_path, encoding="utf-8", errors="replace", newline="") as f:
                lines = [x for x in f.read().splitlines() if x.startswith(LOAD_LINE)]
        except OSError:
            return ""
        if not lines or "server options" in lines[-1]:
            return ""
        return "none" if "--load-mode none" in lines[-1] else "mapped"

    def stop(self) -> None:
        pid, self.pid = self.pid, None
        if pid is None:
            return
        self.processes.stop(pid, STOP_TIMEOUT)
        for _ in range(PORT_FREE_WAIT):          # the next start needs the port and the memory back
            if self.port not in self.listening():
                return
            time.sleep(1)

    def timings(self, body: JsonObject, timeout: float) -> Dict[str, float]:
        answer = self.call("/v1/chat/completions", body, timeout)
        t = answer.get("timings") if isinstance(answer, dict) else None
        if not isinstance(t, dict):
            return {}
        return {k: float(v) for k, v in t.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}

    def parallel(self, counts: Sequence[int], prompt: int, gen: int, kv: str,
                 ub: int = UBATCH) -> Dict[int, Tuple[float, float]]:
        """llama-batched-bench on the model (the server must be stopped: it loads the model itself). ub: its -ub."""
        self.stop()
        if not self.bench:
            return {}
        argv = [self.bench, "-m", self.model_path, "-c", str(max(counts) * (prompt + gen) + 1024),
                "-b", str(max(2048, ub)), "-ub", str(ub), "-npp", str(prompt), "-ntg", str(gen),
                "-npl", ",".join(str(n) for n in counts), "-fa", "on", "-ctk", kv, "-ctv", kv, "-ngl", "999",
                "--fit", "off"]           # as every CARL start: llama.cpp 0.6 fits by default
        try:
            r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               stdin=subprocess.DEVNULL, timeout=PARALLEL_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired):
            return {}
        return parse_batched(r.stdout)

    def count_tokens(self, text: str) -> Optional[int]:
        try:
            answer = self.call("/tokenize", {"content": text})
        except (OSError, ValueError, http.client.HTTPException):
            return None
        tokens = answer.get("tokens") if isinstance(answer, dict) else None
        return len(tokens) if isinstance(tokens, list) else None
