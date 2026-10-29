"""The client config sync service under systemd on Linux, in Docker containers (Dockerfile here: systemd
as PID 1, a user with lingering). The real dashboard API (tools/monitor/cacheapi.py) runs in this
process with a temp folder and a generated key; the containers get the client folder with a test
remote.json and api-key, run install.sh, and the test checks:

  the unit is written, enabled and active; the registry lists the container (service, connected)
  a push (clientsync.publish + the SSE "config" event) is applied within seconds
  the API goes away: the service retries after 5 s, then 10 s, and reconnects when it is back
  NO_SYNC_SERVICE=1 ./install.sh removes the unit; three containers at once all sync
  the client package (Phase 22): unzip, ./setup --yes, the service syncs (PackageInstallTest)

Opt-in (it builds an image and starts containers): CARL_DOCKER_TESTS=1, with docker running.
  CARL_DOCKER_TESTS=1 python3 -m unittest -v tests/integration/test_sync_docker.py
Nothing outside the temp folder and the containers is touched (no ~/.config/carl here)."""
from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from typing import Any, Callable, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "tools"))

from monitor import clientsync                    # noqa: E402
from monitor.cacheapi import CacheApi, Client      # noqa: E402
from monitor.diskcache import CacheConfig          # noqa: E402

IMAGE = "carl-sync-test:latest"
LABEL = "carl-sync-test"
USER_ENV = ["-e", "XDG_RUNTIME_DIR=/run/user/1000", "-e", "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus"]
SKIP_FILES = {"remote.json", "api-key", "installed-models.json"}   # the repo's own: never copied
Json = Dict[str, Any]


def docker_ok() -> Optional[str]:
    """Why the test can't run, else None."""
    if os.environ.get("CARL_DOCKER_TESTS") != "1":
        return "set CARL_DOCKER_TESTS=1 to run the Docker tests"
    if not shutil.which("docker"):
        return "docker is not installed"
    if subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode != 0:
        return "docker is not running"
    return None


def docker(*args: str, timeout: float = 120, check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if check and p.returncode != 0:
        raise AssertionError(f"docker {' '.join(args[:3])}: {p.stderr.strip() or p.stdout.strip()}")
    return p


def models(*ids: str) -> Json:
    return {"schema": 1, "default": ids[0], "models": [{"id": i, "label": i.upper(), "ctx": 32768, "thinking": "on-off"}
                                                       for i in ids]}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait(what: str, cond: Callable[[], Any], timeout: float, step: float = 0.25) -> Any:
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(step)
    raise AssertionError(f"timed out after {timeout:.0f} s waiting for {what}")


class Recorder:
    """Stands in for the API while it is away: accepts and closes at once, noting when."""

    def __init__(self, host: str, port: int) -> None:
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))
        self.sock.listen(8)
        self.times: List[float] = []
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            self.times.append(time.time())
            c.close()

    def close(self) -> None:
        self.sock.close()


class Container:
    """A Linux client: systemd, the user carl, the client folder at `bundle`."""

    def __init__(self, name: str, bundle: str) -> None:
        self.name, self.bundle = name, bundle

    def start(self, stage: str) -> None:
        self.boot()
        self.root("mkdir", "-p", os.path.dirname(self.bundle))
        docker("cp", stage, f"{self.name}:{self.bundle}")
        self.root("chown", "-R", "carl:carl", self.bundle)

    def boot(self) -> None:
        """The container with systemd and the user manager of carl running."""
        docker("run", "-d", "--name", self.name, "--hostname", self.name, "--label", LABEL, "--privileged",
               "--cgroupns=private", "--tmpfs", "/run", "--tmpfs", "/run/lock", "--add-host", "carl-api:host-gateway",
               IMAGE)
        wait(f"systemd in {self.name}", lambda: self.root("systemctl", "is-system-running", check=False).stdout.strip()
             in ("running", "degraded"), 60, 0.5)
        wait(f"the user manager in {self.name}", lambda: self.sh("systemctl --user show-environment",
                                                                  check=False).returncode == 0, 30, 0.5)

    def root(self, *cmd: str, check: bool = True) -> subprocess.CompletedProcess:
        return docker("exec", self.name, *cmd, check=check)

    def sh(self, script: str, env: Tuple[str, ...] = (), check: bool = True, timeout: float = 120,
           cwd: str = "/home/carl") -> subprocess.CompletedProcess:
        """A command as the user carl, with the variables a login gives (systemctl --user needs them)."""
        extra = [a for kv in env for a in ("-e", kv)]
        return docker("exec", "-u", "carl", "-w", cwd, *USER_ENV, *extra, self.name, "bash", "-c", script,
                      check=check, timeout=timeout)

    def install(self, *env: str) -> str:
        p = self.sh("./install.sh", env=env, check=False, timeout=300, cwd=self.bundle)
        if p.returncode != 0:
            raise AssertionError(f"install.sh in {self.name} failed ({p.returncode}):\n{p.stdout}\n{p.stderr}")
        return p.stdout

    def read(self, path: str) -> str:
        p = self.root("cat", path, check=False)
        return p.stdout if p.returncode == 0 else ""

    def sync_state(self) -> Json:
        try:
            return json.loads(self.read("/home/carl/.config/carl/client-sync.json") or "{}")
        except ValueError:
            return {}

    def unit_state(self, verb: str) -> str:
        return self.sh(f"systemctl --user {verb} carl-sync.service", check=False).stdout.strip()


class SyncServiceTest(unittest.TestCase):
    """The steps run in order (test_1 ... test_5) on shared containers and one API."""
    api: Optional[CacheApi] = None
    tmp = ""
    port = 0
    bind = ""
    key = ""
    stage = ""
    containers: List[Container] = []
    timings: Dict[str, float] = {}

    @classmethod
    def setUpClass(cls) -> None:
        why = docker_ok()
        if why:
            raise unittest.SkipTest(why)
        docker("build", "-q", "-t", IMAGE, HERE, timeout=900)
        cls.tmp = tempfile.mkdtemp(prefix="carl-sync-docker-")
        cls.key = secrets.token_hex(24)                   # a test key: never the server's
        cls.bind = cls.api_address()
        cls.port = free_port()
        clientsync.publish(cls.tmp, models("test-a"))
        cls.start_api()
        cls.stage = cls.stage_client()
        cls.containers = []
        cls.timings = {}

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.api:
            cls.api.stop()
        ids = docker("ps", "-aq", "--filter", f"label={LABEL}", check=False).stdout.split()
        if ids:
            docker("rm", "-f", *ids, check=False)
        if cls.tmp:
            shutil.rmtree(cls.tmp, ignore_errors=True)
        if cls.timings:
            print("\n  timings: " + ", ".join(f"{k} {v:.1f} s" for k, v in cls.timings.items()), file=sys.stderr)

    @staticmethod
    def api_address() -> str:
        """Where the API listens so that the containers reach it (as carl-api, the host gateway): Docker
        Desktop forwards to the Mac's loopback; on Linux, the docker bridge's own address."""
        info = docker("info", "--format", "{{.OperatingSystem}}").stdout
        if "Docker Desktop" in info:
            return "127.0.0.1"
        return docker("network", "inspect", "bridge", "--format", "{{(index .IPAM.Config 0).Gateway}}").stdout.strip()

    @classmethod
    def start_api(cls) -> None:
        api = CacheApi(cls.bind, cls.port, lambda: cls.key, cls.tmp, CacheConfig, carl_dir=cls.tmp)
        for _ in range(20):
            err = api.start()
            if err is None:
                cls.api = api
                return
            time.sleep(0.25)
        raise AssertionError(err)

    @classmethod
    def stage_client(cls) -> str:
        """The client folder as a user copies it, with a remote.json and api-key for the test API."""
        stage = os.path.join(cls.tmp, "client")
        shutil.copytree(os.path.join(REPO, "client"), stage,
                        ignore=lambda d, names: [n for n in names if n in SKIP_FILES or n == "__pycache__"
                                                 or ".bak." in n or n.endswith(".tmp")])
        with open(os.path.join(stage, "remote.json"), "w", encoding="utf-8") as f:
            json.dump({"host": "carl-api", "port": free_port(), "cache_api": f"http://carl-api:{cls.port}"}, f)
        with open(os.path.join(stage, "api-key"), "w", encoding="utf-8") as f:
            f.write(cls.key + "\n")
        os.chmod(os.path.join(stage, "api-key"), 0o600)
        return stage

    # --- helpers ---------------------------------------------------------------------------------------
    def client_of(self, c: Container) -> Optional[Client]:
        assert self.api
        return next((x for x in self.api.registry.list() if x.host == c.name), None)

    def connected(self, c: Container) -> bool:
        x = self.client_of(c)
        return bool(x and x.connected >= 1 and x.mode == "service")

    def new_container(self, bundle: str) -> Container:
        c = Container(f"carl-sync-{os.getpid()}-{len(self.containers) + 1}", bundle)
        type(self).containers.append(c)
        c.start(self.stage)
        return c

    def applied(self, c: Container, version: str, ids: List[str]) -> bool:
        if c.sync_state().get("applied") != version:
            return False
        try:
            got = json.loads(c.read(c.bundle + "/installed-models.json"))
        except ValueError:
            return False
        return [m["id"] for m in got.get("models", [])] == ids

    # --- the steps -------------------------------------------------------------------------------------
    def test_1_install_starts_the_service(self) -> None:
        c = self.new_container("/home/carl/CARL client")       # a space in the path: the unit must quote it
        out = c.install()
        self.assertIn("Client sync: a background service (systemd --user carl-sync)", out)
        self.assertIn("[Service]", c.read("/home/carl/.config/systemd/user/carl-sync.service"))
        self.assertEqual(c.unit_state("is-enabled"), "enabled")
        wait("the unit active", lambda: c.unit_state("is-active") == "active", 15)
        t0 = time.time()
        wait("the registry to list the container (service, connected)", lambda: self.connected(c), 30)
        wait("the first config applied", lambda: self.applied(c, clientsync.version_of(models("test-a")), ["test-a"]), 60)
        self.timings["first sync after install"] = time.time() - t0
        st = c.sync_state()
        self.assertTrue(st.get("service"))
        self.assertTrue(st.get("connected"))
        self.assertIsNone(st.get("error"))
        oc = c.read("/home/carl/.config/opencode/opencode.json")
        self.assertIn("test-a", oc)
        self.assertEqual(len([x for x in self.api.registry.list() if x.host == c.name]), 1)   # one id per client

    def test_2_a_push_is_applied_in_seconds(self) -> None:
        c = self.containers[0]
        doc = models("test-b", "test-c")
        t0 = time.time()
        v = clientsync.publish(self.tmp, doc)
        wait("the pushed config applied", lambda: self.applied(c, v, ["test-b", "test-c"]), 30, 0.1)
        self.timings["push applied"] = time.time() - t0
        self.assertIn("test-c", c.read("/home/carl/.config/opencode/opencode.json"))
        wait("the registry to show the new version", lambda: (self.client_of(c) or Client("")).applied == v, 10)
        self.assertIn(f"client config {v}", c.read("/home/carl/.config/carl/client-sync.log"))

    def test_3_the_service_reconnects(self) -> None:
        c = self.containers[0]
        assert self.api
        self.api.stop()
        rec = Recorder(self.bind, self.port)
        t_down = time.time()
        try:
            wait("the service to see the API gone", lambda: c.sync_state().get("connected") is False, 20, 0.1)
            t_gone = time.time()
            wait("two retries", lambda: len(rec.times) >= 2, 30)
        finally:
            rec.close()
        self.assertLess(t_gone, rec.times[0])                   # it says so when the stream ends, not at a retry
        gaps = [rec.times[0] - t_down, rec.times[1] - rec.times[0]]
        self.timings["stream end seen"] = t_gone - t_down
        self.timings["first retry"], self.timings["second retry"] = gaps
        self.assertAlmostEqual(gaps[0], 5, delta=3)        # 5 s after the stream ends (<= 2 s after the stop)
        self.assertAlmostEqual(gaps[1], 10, delta=2)
        type(self).api = None
        self.start_api()
        t_up = time.time()
        wait("the service to reconnect", lambda: self.connected(c), 45)
        self.timings["reconnect after the API is back"] = time.time() - t_up
        self.assertLessEqual(time.time() - rec.times[1], 33)    # the third retry: 30 s after the second
        v = clientsync.publish(self.tmp, models("test-d"))
        wait("a push after the reconnect", lambda: self.applied(c, v, ["test-d"]), 30, 0.1)

    def test_4_no_sync_service_removes_it(self) -> None:
        c = self.containers[0]
        out = c.install("NO_SYNC_SERVICE=1")
        self.assertIn("removed   the client sync service", out)
        self.assertEqual(c.read("/home/carl/.config/systemd/user/carl-sync.service"), "")
        self.assertNotEqual(c.unit_state("is-active"), "active")
        self.assertNotEqual(c.sh("pgrep -u carl -f 'carl-sync.py watch'", check=False).returncode, 0)
        self.assertFalse(c.sync_state().get("service"))
        self.assertNotIn("carl-sync", c.sh("systemctl --user list-units --all --no-legend", check=False).stdout)
        t0 = time.time()      # the API notices at its next write: the keep-alive comment every 25 s
        wait("the registry to show it disconnected", lambda: (self.client_of(c) or Client("")).connected == 0, 60)
        self.timings["registry sees the removed service gone"] = time.time() - t0

    def test_5_three_clients_at_once(self) -> None:
        first = self.containers[0]
        more = [self.new_container("/home/carl/client") for _ in range(2)]
        errors: List[BaseException] = []

        def install(c: Container) -> None:
            try:
                c.install()
            except BaseException as e:         # noqa: BLE001  (reported below)
                errors.append(e)
        threads = [threading.Thread(target=install, args=(c,)) for c in [first, *more]]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        everyone = [first, *more]
        wait("all three in the registry, connected", lambda: all(self.connected(c) for c in everyone), 60)
        # installing again (e.g. a newer client folder) restarts the service, so it runs the new carl-sync.py
        pid = more[0].unit_state("show -p MainPID --value")
        more[0].install()
        wait("the service restarted", lambda: more[0].unit_state("show -p MainPID --value") not in ("", "0", pid), 15)
        wait("all three in the registry, connected", lambda: all(self.connected(c) for c in everyone), 60)
        self.assertEqual(sorted(x.host for x in self.api.registry.list()), sorted(c.name for c in everyone))
        t0 = time.time()
        v = clientsync.publish(self.tmp, models("test-e", "test-f"))
        wait("the push applied on all three", lambda: all(self.applied(c, v, ["test-e", "test-f"]) for c in everyone),
             60, 0.2)
        self.timings["push applied on 3 clients"] = time.time() - t0
        wait("the registry to show the version for all", lambda: all((self.client_of(c) or Client("")).applied == v
                                                                       for c in everyone), 10)


class PackageInstallTest(unittest.TestCase):
    """Phase 22: the client package (./carl.sh package) on a new Linux computer: unzip it as the user, run
    ./setup --yes. OpenCode and Pi are fake programs in ~/.local/bin (no download from npm), so the setup only
    checks them and writes their configs. Then the sync service runs and applies the config of the dashboard."""
    api: Optional[CacheApi] = None
    tmp = ""

    @classmethod
    def setUpClass(cls) -> None:
        why = docker_ok()
        if why:
            raise unittest.SkipTest(why)
        docker("build", "-q", "-t", IMAGE, HERE, timeout=900)
        cls.tmp = tempfile.mkdtemp(prefix="carl-pkg-docker-")
        cls.key = secrets.token_hex(24)
        cls.bind = SyncServiceTest.api_address()
        cls.port = free_port()
        cls.version = clientsync.publish(cls.tmp, models("pkg-a", "pkg-b"))
        api = CacheApi(cls.bind, cls.port, lambda: cls.key, cls.tmp, CacheConfig, carl_dir=cls.tmp)
        err = api.start()
        if err is not None:
            raise AssertionError(err)
        cls.api = api

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.api:
            cls.api.stop()
        ids = docker("ps", "-aq", "--filter", f"label={LABEL}", check=False).stdout.split()
        if ids:
            docker("rm", "-f", *ids, check=False)
        if cls.tmp:
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_install_from_the_zip(self) -> None:
        from carl_core.adapters.client_package import make_package
        server = os.path.join(self.tmp, "server-client")       # what a server start writes into client/
        os.makedirs(server)
        with open(os.path.join(server, "remote.json"), "w", encoding="utf-8") as f:
            json.dump({"host": "carl-api", "port": free_port(), "cache_api": f"http://carl-api:{self.port}"}, f)
        with open(os.path.join(server, "api-key"), "w", encoding="utf-8") as f:
            f.write(self.key)
        out = make_package(REPO, server, os.path.join(self.tmp, "dist"), models("pkg-a"), {})
        self.assertEqual(out.error, "", out.notes)
        c = Container(f"carl-pkg-{os.getpid()}", "/home/carl/carl-client")
        c.boot()
        docker("cp", out.path, f"{c.name}:/home/carl/package.zip")
        c.root("chown", "carl:carl", "/home/carl/package.zip")
        c.sh("unzip -q package.zip && rm package.zip")
        self.assertEqual(c.sh("stat -c %a carl-client/api-key").stdout.strip(), "600")
        self.assertEqual(c.sh("test -x carl-client/setup && echo yes").stdout.strip(), "yes")
        c.sh("mkdir -p ~/.local/bin && for b in opencode pi; do printf '#!/bin/sh\\necho 1.0.0\\n' > ~/.local/bin/$b; "
             "chmod +x ~/.local/bin/$b; done")
        p = c.sh("./setup --yes", check=False, timeout=300, cwd=c.bundle)
        text = " ".join(p.stdout.split())
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        for part in ("CARL client setup (client package", "OpenCode: version 1.0.0. CARL cannot check for a newer",
                     "Client sync: a background service (systemd --user carl-sync)", "CARL setup is done.",
                     "Smoke test: Note: no server runs at http://carl-api:", "run: opencode (or: pi)"):
            self.assertIn(part, text)
        self.assertNotIn(self.key, p.stdout + p.stderr)
        assert self.api
        wait("the registry to list the container (service, connected)",
             lambda: any(x.host == c.name and x.connected >= 1 and x.mode == "service" for x in self.api.registry.list()),
             60)
        wait("the dashboard's config applied", lambda: json.loads(c.read("/home/carl/.config/carl/client-sync.json")
                                                                   or "{}").get("applied") == self.version, 60)
        self.assertIn("pkg-b", c.read("/home/carl/.config/opencode/opencode.json"))
        self.assertEqual(c.read("/home/carl/.config/carl/api-key"), self.key)


if __name__ == "__main__":
    unittest.main()
