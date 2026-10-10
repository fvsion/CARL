"""Phase 23.5 item 7: OpenCode and Pi on this Mac set up for the server's old port (8080 before 9275, or a start on the
next free port) count as set up for it, out of date: the Connect tab says so, and u sets them up again."""
from __future__ import annotations

import json
import os
import tempfile
import unittest

import mon_support  # noqa: F401  (puts tools/ on sys.path)
from monitor import fsio
from monitor.clients import drift


class MovedTest(unittest.TestCase):
    def test_moved_port(self) -> None:
        base = "http://127.0.0.1:9275"
        self.assertEqual(fsio.moved_port("http://127.0.0.1:8080/v1", base), 8080)
        self.assertEqual(fsio.moved_port("http://localhost:8080/v1", base), 8080)
        self.assertIsNone(fsio.moved_port("http://127.0.0.1:9275/v1", base))          # this server
        self.assertIsNone(fsio.moved_port("http://192.168.42.1:8080/v1", base))       # another address
        self.assertIsNone(fsio.moved_port("http://127.0.0.1:8080/other", base))
        self.assertIsNone(fsio.moved_port("not a url", base))

    def test_here_and_the_drift_line(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            folder = os.path.join(home, ".config", "opencode")
            os.makedirs(folder)
            with open(os.path.join(folder, "carl.json"), "w") as f:
                json.dump({"base_url": "http://127.0.0.1:8080/v1", "providers": {"llamacpp": "llamacpp"}}, f)
            base = "http://127.0.0.1:9275"
            self.assertEqual(fsio.installed_here(base, home), [("OpenCode", "llamacpp")])
            self.assertEqual(fsio.moved_here(base, home), {"OpenCode": 8080})
            d = drift({"OpenCode": {"m": 98304}}, ["m"], None, "", {"OpenCode": (8080, 9275)})
            self.assertEqual(len(d), 1)
            self.assertEqual(d[0].line(1), "OpenCode uses port 8080. The server runs on port 9275. Press u.")


if __name__ == "__main__":
    unittest.main()
