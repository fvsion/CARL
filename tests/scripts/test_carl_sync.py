"""client/carl-sync.py: a pushed config is applied with the installer (here a stub) once, a config
that is already applied isn't, auto-apply off keeps it waiting, and the event stream triggers a sync.
Runs the script in a copied client folder with its own HOME and a small fake dashboard API."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CLIENT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "client")


class FakeApi:
    def __init__(self) -> None:
        self.doc = {"version": "v1", "published": "now", "models": {"schema": 1, "models": [{"id": "m"}]}}
        api = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a: object) -> None:
                pass

            def do_GET(self) -> None:
                if self.headers.get("Authorization") != "Bearer k":
                    self.send_response(401)
                    self.end_headers()
                    return
                if self.path == "/carl/client/events":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(f"event: config\ndata: {{\"version\": \"{api.doc['version']}\"}}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(0.5)
                    return
                if self.headers.get("If-None-Match", "").strip('"') == api.doc["version"]:
                    self.send_response(304)
                    self.end_headers()
                    return
                data = json.dumps(api.doc).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


class SyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.home = os.path.join(self.tmp, "home")
        self.bundle = os.path.join(self.tmp, "client")
        os.makedirs(self.bundle)
        shutil.copy(os.path.join(CLIENT, "carl-sync.py"), self.bundle)
        self.api = FakeApi()
        with open(os.path.join(self.bundle, "remote.json"), "w") as f:
            json.dump({"host": "127.0.0.1", "port": 8080, "cache_api": self.api.url}, f)
        with open(os.path.join(self.bundle, "api-key"), "w") as f:
            f.write("k\n")
        with open(os.path.join(self.bundle, "install.sh"), "w") as f:   # the stub installer: counts its runs
            f.write('echo run >> "$HOME/installs"; echo "WEB=${WEB_SEARCH:-} SYNC=${CARL_SYNC:-}" >> "$HOME/installs"\n')
        os.makedirs(os.path.join(self.home, ".config", "carl"))
        with open(os.path.join(self.home, ".config", "carl", "client-install.env"), "w") as f:
            f.write("WEB_SEARCH=off\nEVIL=1\n")

    def tearDown(self) -> None:
        self.api.server.shutdown()
        shutil.rmtree(self.tmp)

    def sync(self, *args: str, timeout: float = 30) -> dict:
        p = subprocess.run([sys.executable, os.path.join(self.bundle, "carl-sync.py"), *args], capture_output=True,
                           text=True, timeout=timeout, env={**os.environ, "HOME": self.home})
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout) if p.stdout.strip() else {}

    def installs(self) -> list:
        try:
            with open(os.path.join(self.home, "installs")) as f:
                return f.read().split("\n")
        except OSError:
            return []

    def test_a_pushed_config_is_applied_once_with_the_last_switches(self) -> None:
        st = self.sync("once")
        self.assertEqual((st["applied"], st.get("pending")), ("v1", None))
        self.assertEqual(self.installs()[:2], ["run", "WEB=off SYNC=1"])                 # EVIL isn't passed on
        with open(os.path.join(self.bundle, "installed-models.json")) as f:
            self.assertEqual(json.load(f)["models"], [{"id": "m"}])
        self.sync("once")
        self.assertEqual(self.installs().count("run"), 1)                                 # 304: nothing again

    def test_auto_apply_off_keeps_it_waiting(self) -> None:
        self.sync("auto", "off")
        self.assertEqual(self.sync("once")["pending"], "v1")
        self.assertEqual(self.installs(), [])
        self.assertEqual(self.sync("apply")["applied"], "v1")
        self.assertEqual(self.installs().count("run"), 1)

    def test_the_service_applies_on_an_event(self) -> None:
        p = subprocess.Popen([sys.executable, os.path.join(self.bundle, "carl-sync.py"), "watch"],
                             env={**os.environ, "HOME": self.home}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                if "run" in self.installs():
                    break
                time.sleep(0.1)
        finally:
            p.kill()
            p.wait()
        self.assertIn("run", self.installs())
        self.assertTrue(self.sync("status")["service"])

    def test_a_wrong_key_says_what_to_do(self) -> None:
        with open(os.path.join(self.bundle, "api-key"), "w") as f:
            f.write("wrong\n")
        self.assertIn("refused the API key", self.sync("once")["error"])
        self.assertEqual(self.installs(), [])

    def test_a_config_without_a_version_is_not_applied(self) -> None:
        self.api.doc = {"models": {"schema": 1, "models": []}, "version": "../x y"}
        self.assertIn("not a client config", self.sync("once")["error"])
        self.assertEqual(self.installs(), [])

    def test_without_remote_json_it_says_so(self) -> None:
        os.remove(os.path.join(self.bundle, "remote.json"))
        self.assertIn("remote.json", self.sync("once")["error"])


if __name__ == "__main__":
    unittest.main()
