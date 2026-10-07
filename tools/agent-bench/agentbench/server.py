"""CARL's llama.cpp server for one model batch: started with the repo's launcher in a work folder of the harness
(its own settings folder, client folder, API key and log), stopped by its process group's PID."""
from __future__ import annotations

import os
import secrets
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional

from .proc import stop_group, tail

FORBIDDEN_PORTS = (8080,)          # the user's own CARL server


class ServerError(Exception):
    pass


def check_port(port: int) -> None:
    if port in FORBIDDEN_PORTS:
        raise ServerError(f"port {port} is the user's CARL server; the harness never uses it")
    if not 1024 <= port <= 65534:
        raise ServerError(f"port {port} is not a usable port (1024 to 65534)")


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex((host, port)) == 0


def ensure_key(path: str) -> str:
    """The harness's API key file (made once, mode 0600): the server and the clients use it."""
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(20))
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


@dataclass
class ServerSetup:
    repo: str
    model: str
    work: str                       # the harness's work folder: conf/, client/, logs/, api-key
    port: int = 8097
    models_dir: str = os.path.expanduser(os.path.join("~", "models", "gguf"))
    slots: int = 2                  # --slots N for the launcher; 0: the launcher's choice
    extra_env: Mapping[str, str] = field(default_factory=dict)

    @property
    def conf_dir(self) -> str:
        return os.path.join(self.work, "conf")

    @property
    def client_dir(self) -> str:
        return os.path.join(self.work, "client")

    @property
    def key_file(self) -> str:
        return os.path.join(self.work, "api-key")

    @property
    def log_dir(self) -> str:
        return os.path.join(self.work, "logs")

    @property
    def home_dir(self) -> str:
        """The launcher's HOME: it writes the thinking-toggle chat templates to ~/models/templates."""
        return os.path.join(self.work, "home")

    def env(self, log_file: str, base: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
        """The launcher's environment: no dashboard (MONITOR=0), the harness's folders (also as HOME, so nothing
        goes to your HOME), the models folder, the port, the log."""
        env = dict(os.environ if base is None else base)
        env.update(HOME=self.home_dir, MONITOR="0", CARL_CONF_DIR=self.conf_dir, CARL_CLIENT_DIR=self.client_dir, PORT=str(self.port),
                   LOG_FILE=log_file, API_KEY_FILE=self.key_file)
        env["MODELS_DIR"] = self.models_dir             # always: the launcher's HOME is not yours
        env.update(self.extra_env)
        return env

    def argv(self) -> List[str]:
        argv = ["./carl.sh", "--model", self.model, "--local"]
        if self.slots:
            argv += ["--slots", str(self.slots)]
        return argv


class CarlServer:
    """One server: start(), wait_ready(), stop(). Only the process group that start() made is stopped."""

    def __init__(self, setup: ServerSetup) -> None:
        self.setup = setup
        self.proc: Optional["subprocess.Popen[bytes]"] = None
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.log_file = os.path.join(setup.log_dir, f"server-{setup.model}-{stamp}.log")
        self.console = os.path.join(setup.log_dir, f"console-{setup.model}-{stamp}.out")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.setup.port}"

    def start(self) -> int:
        s = self.setup
        check_port(s.port)
        if port_in_use(s.port):
            raise ServerError(f"port {s.port} is in use: another server runs there. Stop it, or use --port.")
        for d in (s.conf_dir, s.client_dir, s.log_dir, s.home_dir):
            os.makedirs(d, exist_ok=True)
        ensure_key(s.key_file)
        with open(self.console, "wb") as out:
            self.proc = subprocess.Popen(s.argv(), cwd=s.repo, env=s.env(self.log_file), stdout=out,
                                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        return self.proc.pid

    def console_tail(self, lines: int = 20) -> str:
        try:
            with open(self.console, encoding="utf-8", errors="replace") as f:
                return tail(f.read(), lines)
        except OSError:
            return ""

    def health(self) -> int:
        key = ensure_key(self.setup.key_file)
        req = urllib.request.Request(self.url + "/health", headers={"Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return int(r.status)
        except urllib.error.HTTPError as e:
            return int(e.code)
        except (urllib.error.URLError, OSError):
            return 0

    def wait_ready(self, timeout: float = 1200.0) -> float:
        """Wait for /health 200. Returns the seconds it took; ServerError when the launcher stops first."""
        if self.proc is None:
            raise ServerError("the server is not started")
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if self.proc.poll() is not None:
                raise ServerError(f"the launcher stopped (code {self.proc.returncode}):\n{self.console_tail()}")
            if self.health() == 200:
                return time.monotonic() - t0
            time.sleep(2)
        raise ServerError(f"no /health 200 after {timeout:.0f} s:\n{self.console_tail()}")

    def stop(self) -> None:
        if self.proc is None:
            return
        stop_group(self.proc.pid, grace=20)
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        self.proc = None
