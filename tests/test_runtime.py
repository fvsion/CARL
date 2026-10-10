"""The server file for this Mac's clients (Phase 23.5): carl_core/domain/runtime.py and carl.py server-file."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

import support  # noqa: F401  (puts tools/ on sys.path)
from carl_core.domain import runtime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class RuntimeTest(unittest.TestCase):
    def test_write_and_read(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = runtime.write(d, 8080, "single", {"gemma-4-e4b": runtime.model_entry("window", "q4_0", 98304, 2)})
            self.assertEqual(p, os.path.join(d, "server-8080.json"))
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
            doc = runtime.read(d, 8080)
            self.assertEqual((doc["version"], doc["port"], doc["mode"]), (1, 8080, "single"))
            self.assertEqual(doc["models"], {"gemma-4-e4b": {"swa": "window", "kv": "q4_0", "ctx": 98304, "slots": 2}})
            self.assertEqual(runtime.read(d, 9999), {})

    def test_the_launchers_dash_is_none(self) -> None:
        self.assertEqual(runtime.model_entry("-"), {"swa": "none"})
        with self.assertRaises(ValueError):
            runtime.model_entry("half")
        with self.assertRaises(ValueError):
            runtime.document(1, "both", {})

    def test_carl_py_server_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, CARL_CONF_DIR=d)
            r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "carl.py"), "server-file", "--port", "8123",
                                "--model", "m", "--swa", "-", "--kv", "q8_0", "--ctx", "1024", "--slots", "1"],
                               env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(os.path.join(d, "server-8123.json")) as f:
                self.assertEqual(json.load(f)["models"], {"m": {"swa": "none", "kv": "q8_0", "ctx": 1024, "slots": 1}})


    def test_the_apis_port_into_remote_json(self) -> None:
        from carl_core.adapters.client_package import publish_api_port
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "remote.json")
            with open(p, "w") as f:
                json.dump({"host": "127.0.0.1", "port": 9275, "cache_api": "http://127.0.0.1:9276"}, f)
            env = {"CARL_CLIENT_DIR": d}
            self.assertFalse(publish_api_port("/repo", 9275, "http://127.0.0.1:9276", env))    # the same: untouched
            self.assertFalse(publish_api_port("/repo", 9999, "http://127.0.0.1:9278", env))    # another server's file
            self.assertTrue(publish_api_port("/repo", 9275, "http://127.0.0.1:9278", env))
            with open(p) as f:
                self.assertEqual(json.load(f)["cache_api"], "http://127.0.0.1:9278")
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
