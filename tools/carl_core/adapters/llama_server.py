"""The llama-server Auto-tune measures (implements TuneServer).

It is started through host/serve-llama.sh (so the measured server is the one CARL runs),
on its own port, local only, in its own process group so a stop takes the whole server down.
"""
from __future__ import annotations

import http.client
import json
import os
import signal
import subprocess
import time
import urllib.request
from typing import Callable, Dict, Optional, Set

from ..domain.errors import ConfigError
from ..domain.types import JsonObject, JsonValue

START_TIMEOUT = 600          # a cold load of a large model from disk takes minutes
STOP_TIMEOUT = 60
HEALTH_TIMEOUT = 2
PORT_FREE_WAIT = 60
# Variables the launcher would take over from the caller's environment: the tune sets each
# value itself (flags or below), so a value exported in the user's shell can't skew it.
CLEARED_ENV = ("CTX", "KV", "KV_K", "KV_V", "MODEL", "ALIAS", "CACHE_RAM")


class LlamaServerControl:
    def __init__(self, launcher: str, model_path: str, port: int, api_key: str, log_path: str,
                 listening: Callable[[], Set[int]]) -> None:
        self.launcher = launcher
        self.model_path = model_path
        self.port = port
        self.api_key = api_key
        self.log_path = log_path
        self.listening = listening
        self.proc: Optional[subprocess.Popen[bytes]] = None

    def call(self, path: str, body: Optional[JsonObject] = None, timeout: float = 1800) -> JsonValue:
        """POST body (or GET without one) to the server's API; the JSON answer."""
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
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
            with open(self.log_path, encoding="utf-8", errors="replace") as f:
                lines = f.read().strip().splitlines()
        except OSError:
            return ""
        return lines[-1][:200] if lines else ""

    def start(self, spec: str, n: int, ctx: int) -> float:
        self.stop()
        env = dict(os.environ, SETTINGS_FILE="none", MONITOR="0", KEEP_AWAKE="0", LOG_FILE="none",
                   PORT=str(self.port), SLOTS="1", SPEC=spec, SPEC_N=str(n), FIT_CHECK="0")
        for k in CLEARED_ENV:
            env.pop(k, None)
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        with open(self.log_path, "w", encoding="utf-8") as log:
            self.proc = subprocess.Popen([self.launcher, "--model", self.model_path, "--local", "--ctx", str(ctx),
                                          "--kv", "q4"], env=env, stdout=log, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, start_new_session=True)
        t0 = time.monotonic()
        while time.monotonic() - t0 < START_TIMEOUT:
            if self.proc.poll() is not None:
                raise ConfigError("the server exited: " + self._last_log_line())
            if self._healthy():
                return time.monotonic() - t0
            time.sleep(1)
        raise ConfigError("the server did not start in 10 min")

    def stop(self) -> None:
        p, self.proc = self.proc, None
        if p is None:
            return
        if p.poll() is None:
            os.killpg(p.pid, signal.SIGTERM)
            try:
                p.wait(STOP_TIMEOUT)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()
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

    def count_tokens(self, text: str) -> Optional[int]:
        try:
            answer = self.call("/tokenize", {"content": text})
        except (OSError, ValueError, http.client.HTTPException):
            return None
        tokens = answer.get("tokens") if isinstance(answer, dict) else None
        return len(tokens) if isinstance(tokens, list) else None
