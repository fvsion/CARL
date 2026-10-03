"""Client setup in the Connect tab: the pasted provider block's id, and which clients
install.sh already pointed at this server (its state files, also under their name from
before the rename)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True                                  # keep tools/ free of __pycache__
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools"))

from monitor import fsio
from monitor.clients import Served, opencode_config, pi_config

BASE = "http://127.0.0.1:8080"


class SnippetTest(unittest.TestCase):
    def test_pasted_blocks_use_the_carl_id(self) -> None:
        s = Served("qwen", 98304)
        oc = opencode_config(s, {}, BASE, "{file:/k}")
        self.assertEqual(list(oc["provider"]), ["carl"])
        self.assertEqual(oc["provider"]["carl"]["name"], "llamacpp [carl]")
        self.assertEqual(list(pi_config(s, {}, BASE, "k")["providers"]), ["carl"])


class InstalledHereTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def state(self, rel: str, providers: dict[str, str], base: str = BASE) -> None:
        path = os.path.join(self.home, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"providers": providers, "base_url": base + "/v1"}, f)

    def test_nothing_installed(self) -> None:
        self.assertEqual(fsio.installed_here(BASE, self.home), [])

    def test_carl_state_files(self) -> None:
        self.state(".config/opencode/carl.json", {"llamacpp": "carl"})
        self.state(".pi/agent/carl.json", {"llamacpp": "llamacpp"}, base="http://192.168.42.1:8080")
        self.assertEqual(fsio.installed_here(BASE, self.home), [("OpenCode", "carl")])

    def test_old_state_file_until_the_next_install(self) -> None:
        self.state(".pi/agent/llm-deploy.json", {"llamacpp": "llamacpp"})
        self.assertEqual(fsio.installed_here(BASE, self.home), [("Pi", "llamacpp")])
        self.state(".pi/agent/carl.json", {"llamacpp": "carl"})          # carl.json wins once it exists
        self.assertEqual(fsio.installed_here(BASE, self.home), [("Pi", "carl")])

    def test_unreadable_state_file(self) -> None:
        path = os.path.join(self.home, ".config/opencode/carl.json")
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.state(".config/opencode/llm-deploy.json", {"llamacpp": "llamacpp"})
        self.assertEqual(fsio.installed_here(BASE, self.home), [])


if __name__ == "__main__":
    unittest.main()
