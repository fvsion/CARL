"""client/configure.py against a throw-away home: fresh install, re-run, user-owned
entries, the coder switch, argument validation."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from typing import Any

from _paths import CLIENT

SCRIPT = os.path.join(CLIENT, "configure.py")


class ConfigureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_configure(self, *extra: str, host: str = "192.168.42.1", ctx: str = "98304") -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, SCRIPT, "--bundle", CLIENT, "--home", self.home, "--host", host,
                               "--port", "8000", "--llama-port", "8080", "--ctx", ctx, *extra],
                              capture_output=True, text=True)

    def path(self, rel: str) -> str:
        return os.path.join(self.home, rel)

    def read_json(self, rel: str) -> Any:
        with open(self.path(rel), encoding="utf-8") as f:
            return json.load(f)

    def write_json(self, rel: str, data: object) -> None:
        os.makedirs(os.path.dirname(self.path(rel)), exist_ok=True)
        with open(self.path(rel), "w", encoding="utf-8") as f:
            json.dump(data, f)

    def test_fresh_install(self) -> None:
        p = self.run_configure("--coder", "1", ctx="65536")
        self.assertEqual(p.returncode, 0, p.stderr)
        oc = self.read_json(".config/opencode/opencode.json")
        prov = oc["provider"]["llamacpp"]
        self.assertEqual(prov["options"]["baseURL"], "http://192.168.42.1:8080/v1")
        self.assertEqual(prov["options"]["apiKey"], "{file:" + self.home + "/.config/mtplx/api-key}")
        self.assertTrue(all(m["limit"]["context"] == 65536 for m in prov["models"].values()))
        self.assertEqual(oc["model"], "llamacpp/qwen3.6-35b-a3b")
        self.assertIn("coder", oc["agent"])
        pi = self.read_json(".pi/agent/models.json")
        self.assertIn("llamacpp", pi["providers"])
        self.assertTrue(os.path.exists(self.path(".pi/agent/agents/coder.md")))
        for rel in (".config/opencode/opencode.json", ".config/opencode/llm-deploy.json", ".pi/agent/models.json",
                    ".pi/agent/settings.json"):
            self.assertEqual(stat.S_IMODE(os.stat(self.path(rel)).st_mode), 0o600, rel)

    def test_rerun_changes_nothing(self) -> None:
        self.assertEqual(self.run_configure().returncode, 0)
        p = self.run_configure()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("added", p.stdout)
        self.assertNotIn("updated", p.stdout)
        baks = [f for _, _, fs in os.walk(self.home) for f in fs if ".bak." in f]
        self.assertEqual(baks, [])

    def test_user_owned_entries_are_kept(self) -> None:
        self.write_json(".config/opencode/opencode.json",
                        {"provider": {"llamacpp": {"name": "mine"}}, "model": "other/model", "agent": {"coder": {"prompt": "mine"}}})
        p = self.run_configure()
        self.assertEqual(p.returncode, 0, p.stderr)
        oc = self.read_json(".config/opencode/opencode.json")
        self.assertEqual(oc["provider"]["llamacpp"], {"name": "mine"})
        self.assertIn("llm-deploy", oc["provider"])
        self.assertEqual(oc["model"], "other/model")
        self.assertEqual(oc["agent"]["coder"], {"prompt": "mine"})
        self.assertIn("llm-deploy-coder", oc["agent"])
        self.assertTrue(os.path.exists(self.path(".config/opencode/opencode.json.before-carl")))

    def test_coder_off_removes_ours(self) -> None:
        self.run_configure("--coder", "1")
        p = self.run_configure("--coder", "0")
        self.assertEqual(p.returncode, 0, p.stderr)
        oc = self.read_json(".config/opencode/opencode.json")
        self.assertNotIn("coder", oc.get("agent", {}))
        self.assertFalse(os.path.exists(self.path(".pi/agent/agents/coder.md")))

    def test_bad_json_changes_nothing(self) -> None:
        os.makedirs(self.path(".config/opencode"))
        with open(self.path(".config/opencode/opencode.json"), "w", encoding="utf-8") as f:
            f.write("{ // comment\n}")
        p = self.run_configure()
        self.assertEqual(p.returncode, 1)
        self.assertIn("not plain JSON", p.stderr)
        self.assertFalse(os.path.exists(self.path(".pi")))

    def test_rejects_host_that_would_break_the_json(self) -> None:
        p = self.run_configure(host='evil","x":"')
        self.assertEqual(p.returncode, 2)
        self.assertIn("not a host name", p.stderr)
        self.assertFalse(os.path.exists(self.path(".config")))


if __name__ == "__main__":
    unittest.main()
