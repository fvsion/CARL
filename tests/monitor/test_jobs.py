"""Background jobs: the settings restart and its rollback, Auto-tune and download progress,
Hugging Face lookups. The launchers are small stand-in scripts in a temporary folder:
no real server, model, GPU or network is used."""
from __future__ import annotations

import json
import os
import signal
import socket
import stat
import tempfile
import time
import unittest
from typing import List, Optional

from mon_support import FakeStore
from monitor.api import Endpoint
from monitor.collector import Collector
from monitor.jobs import CLEAN_ENV, Paths, ServerJobs, server_env
from monitor.model import JSONDict, ServerData
from monitor.settings import Schema, SettingsService, net_choices
from monitor.state import Download, TuneRun, UIState
from monitor.store import ModelList

HEALTH_SERVER = """#!/bin/sh
exec python3 -c '
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
HTTPServer(("127.0.0.1", int(os.environ["PORT"])), H).serve_forever()
'
"""
FAILS = """#!/bin/sh
echo "starting on $PORT (MONITOR=$MONITOR, CTX=${CTX:-unset})"
echo "error: the model did not load"
exit 3
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


class FileStore(FakeStore):
    """FakeStore that also writes config.json, as carl.save_config does."""

    def save_config(self, cfg: JSONDict) -> None:
        super().save_config(cfg)
        with open(self.config_file, "w") as f:
            json.dump(cfg, f)


class FakeProc:
    def __init__(self, codes: List[Optional[int]]) -> None:
        self.codes = codes
        self.pid = 999999
        self.returncode: Optional[int] = None

    def poll(self) -> Optional[int]:
        self.returncode = self.codes.pop(0) if len(self.codes) > 1 else self.codes[0]
        return self.returncode


class JobsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        os.makedirs(os.path.join(root, "host"))
        os.makedirs(os.path.join(root, "conf"))
        self.port = free_port()
        self.store = FileStore()
        self.store.config_file = os.path.join(root, "conf", "config.json")
        self.ui = UIState()
        self.models = ModelList(self.store, lambda msg: None)
        self.svc = SettingsService(self.models, Schema(net_choices([])), "127.0.0.1", lambda: self.store.limit)
        self.collector = Collector(Endpoint("127.0.0.1", self.port, ""), True, os.path.join(root, "key"), None, None,
                                   None, root, 16384)
        self.paths = Paths(repo=root, logs=os.path.join(root, "logs"), config_file=self.store.config_file)
        self.jobs = ServerJobs(self.ui, self.collector, self.svc, self.paths, "127.0.0.1")
        self.pending = dict(self.svc.schema.defaults(), adv="hidden", model="big")

    def tearDown(self) -> None:
        pid = self.collector.server_pid
        if pid:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.tmp.cleanup()

    def launcher(self, text: str) -> None:
        path = os.path.join(self.paths.repo, "host", "serve-llama.sh")
        with open(path, "w") as f:
            f.write(text)
        os.chmod(path, 0o755)

    def test_failed_start_puts_config_back_private(self) -> None:
        self.launcher(FAILS)
        with open(self.store.config_file, "w") as f:
            f.write('{"schema": 1, "mine": true}\n')
        os.chmod(self.store.config_file, 0o644)
        os.environ["CTX"] = "123"                          # the environment does not leak into the start
        try:
            self.jobs._restart(self.pending, ServerData())
        finally:
            del os.environ["CTX"]
        msg = self.ui.toast_msg[0]
        self.assertIn("The new settings failed", msg)
        self.assertIn("No server is running now", msg)
        self.assertIn("error: the model did not load", " ".join(self.ui.start_error))   # the launcher's error line
        with open(self.paths.console(self.port)) as f:
            self.assertIn("CTX=unset", f.read())
        with open(self.store.config_file) as f:
            self.assertEqual(f.read(), '{"schema": 1, "mine": true}\n')
        self.assertEqual(stat.S_IMODE(os.stat(self.store.config_file).st_mode), 0o600)
        self.assertEqual(len(self.store.saved), 1)          # it was saved before the start
        self.assertIsNone(self.ui.restart)

    def test_successful_start_follows_the_new_server(self) -> None:
        self.launcher(HEALTH_SERVER)
        self.ui.pending = dict(self.pending)
        self.jobs._restart(self.pending, ServerData())
        self.assertTrue(self.ui.toast_msg[0].startswith("The server restarted with the new settings"))
        self.assertIsNone(self.ui.pending)
        self.assertIsNotNone(self.collector.server_pid)
        self.assertNotIn("backend", self.store.saved[-1])
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.store.config_file), "last-backend")))

    def test_server_env(self) -> None:
        env = server_env({"CTX": "1", "HOME": "/h", "SETTINGS_FILE": "x"}, 8095, {"MODEL": "/m/a.gguf"})
        self.assertEqual(env, {"HOME": "/h", "MONITOR": "0", "PORT": "8095", "MODEL": "/m/a.gguf"})
        env = server_env({}, 1, {"SETTINGS_FILE": "none", "CTX": "2"})
        self.assertEqual((env["SETTINGS_FILE"], env["CTX"]), ("none", "2"))
        self.assertIn("SPEC_N", CLEAN_ENV)

    def test_download_progress_and_end(self) -> None:
        part = os.path.join(self.tmp.name, "a.gguf")
        log = os.path.join(self.tmp.name, "dl.out")
        with open(part, "wb") as f:
            f.write(b"x" * 100)
        with open(log, "w") as f:
            f.write("downloading\n")
        self.ui.dl = Download(name="a", path=part, total=1000, proc=FakeProc([None, None, 0]), log=log)
        self.jobs.poll()
        with open(part, "ab") as f:
            f.write(b"x" * 400)
        self.jobs.poll()
        dl = self.ui.dl
        self.assertEqual((dl.have, dl.done), (500, False))
        self.assertGreater(dl.rate, 0)
        with open(log, "a") as f:
            f.write("verified\n\n")
        self.jobs.poll()
        self.assertTrue(dl.done)
        self.assertEqual(dl.tail, ["verified"])
        self.assertTrue(self.ui.toast_msg[0].startswith("a is downloaded and checked"))
        self.assertIn("u in the Connect tab", self.ui.toast_msg[0])                 # the client lists need updating

    def test_tune_end_is_announced(self) -> None:
        log = os.path.join(self.tmp.name, "tune.out")
        with open(log, "w") as f:
            f.write("STEP 4/4 the result\nDONE kv q4_0, ngram n=2\n")
        self.ui.tune = TuneRun(model="big", proc=FakeProc([0]), log=log, restart=False)
        self.jobs.poll()
        self.assertTrue(self.ui.tune.done)
        self.assertEqual(self.ui.toast_msg[0], "Auto-tune is done. kv q4_0, ngram n=2")

    def test_tune_that_stopped_the_server_starts_it_again(self) -> None:
        self.launcher(FAILS)
        log = os.path.join(self.tmp.name, "tune.out")
        with open(log, "w") as f:
            f.write("DONE ok\n")
        os.makedirs(self.paths.logs)
        self.ui.tune = TuneRun(model="big", proc=FakeProc([0]), log=log, restart=True)
        self.jobs.poll()
        deadline = time.time() + 10
        while "did not start again" not in self.ui.toast_msg[0] and time.time() < deadline:
            time.sleep(0.1)
        self.assertIn("The server did not start again", self.ui.toast_msg[0])

    def test_hugging_face_lookup(self) -> None:
        self.jobs._hf_lookup("Qwen/Qwen3-0.6B-GGUF")
        assert self.ui.picker is not None
        self.assertEqual(self.ui.picker.items[0][0], "a.gguf")
        self.ui.picker = None
        self.jobs._hf_lookup("a/b?x=1")                   # parse_hf accepts it; the API path must not
        self.assertIsNone(self.ui.picker)
        self.assertIn("not a Hugging Face repo", self.ui.hf.status if self.ui.hf else "")

    def test_hugging_face_names_with_control_characters_are_not_offered(self) -> None:
        self.store.hf_files = lambda repo: [("evil\x1b]0;x\x07.gguf", 1, None), ("ok.gguf", 2, None)]  # type: ignore[method-assign]
        self.jobs._hf_lookup("o/r")
        assert self.ui.picker is not None
        self.assertEqual([i[0] for i in self.ui.picker.items], ["ok.gguf"])


if __name__ == "__main__":
    unittest.main()
