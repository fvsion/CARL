"""client/configure.py against a throw-away home: fresh install, re-run, user-owned
entries, the coder switch, argument validation, and taking the MTPLX pieces of a
pre-1.2.0 install out (ours only)."""
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
                               "--llama-port", "8080", "--ctx", ctx, *extra],
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
        self.assertEqual(prov["options"]["apiKey"], "{file:" + self.home + "/.config/llm-deploy/api-key}")
        self.assertEqual(set(oc["provider"]), {"llamacpp"})
        self.assertNotIn("plugin", oc)
        self.assertTrue(all(m["limit"]["context"] == 65536 for m in prov["models"].values()))
        self.assertEqual(oc["model"], "llamacpp/qwen3.6-35b-a3b")
        self.assertIn("coder", oc["agent"])
        pi = self.read_json(".pi/agent/models.json")
        self.assertEqual(set(pi["providers"]), {"llamacpp"})
        self.assertEqual(pi["providers"]["llamacpp"]["apiKey"], "!cat ~/.config/llm-deploy/api-key")
        self.assertFalse(os.path.exists(self.path(".pi/agent/extensions/mtplx-request-policy.ts")))
        self.assertFalse(os.path.exists(self.path(".config/opencode/plugins/mtplx-session-headers")))
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
        self.assertIn("carl-coder", oc["agent"])                           # ours, next to the user's own coder
        with open(self.path(".config/opencode/llm-deploy/delegation.md"), encoding="utf-8") as f:
            self.assertIn("`carl-coder`", f.read())
        self.assertTrue(os.path.exists(self.path(".config/opencode/opencode.json.before-carl")))

    def test_coder_off_removes_ours(self) -> None:
        self.run_configure("--coder", "1")
        p = self.run_configure("--coder", "0")
        self.assertEqual(p.returncode, 0, p.stderr)
        oc = self.read_json(".config/opencode/opencode.json")
        self.assertNotIn("coder", oc.get("agent", {}))
        self.assertFalse(os.path.exists(self.path(".pi/agent/agents/coder.md")))

    # ---------------------------------------------------------------- pre-1.2.0 installs
    OLD_KEY_OC = "{file:%s/.config/mtplx/api-key}"
    OLD_KEY_PI = "!cat ~/.config/mtplx/api-key"

    def write_text(self, rel: str, text: str) -> None:
        os.makedirs(os.path.dirname(self.path(rel)), exist_ok=True)
        with open(self.path(rel), "w", encoding="utf-8") as f:
            f.write(text)

    def old_install(self, mtplx_id: str = "mtplx", state: bool = True, extra_oc: dict | None = None) -> None:
        """What CARL 1.1 (470c316) left in a home: both providers on the old key path, the
        MTPLX plugin (folder + plugin entry), the Pi extension, and the state files."""
        plug = self.path(".config/opencode/plugins/mtplx-session-headers")
        llama_oc = {"npm": "@ai-sdk/openai-compatible", "name": "llama.cpp (Mac host)",
                    "options": {"baseURL": "http://192.168.42.1:8080/v1", "apiKey": self.OLD_KEY_OC % self.home},
                    "models": {}}
        mx_oc = {"npm": "@ai-sdk/openai-compatible", "name": "MTPLX (Mac host, ≤48K)",
                 "options": {"baseURL": "http://192.168.42.1:8000/v1", "apiKey": self.OLD_KEY_OC % self.home,
                             "headers": {"x-mtplx-client": "opencode"}}, "models": {}}
        self.write_json(".config/opencode/opencode.json", {
            "$schema": "https://opencode.ai/config.json", "provider": {"llamacpp": llama_oc, mtplx_id: mx_oc, **(extra_oc or {})},
            "model": "llamacpp/qwen3.6-35b-a3b", "plugin": [plug, "/home/u/my-plugin"]})
        self.write_text(".config/opencode/plugins/mtplx-session-headers/index.js",
                        "export const MTPLXSessionHeaders = async () => ({});\nexport default MTPLXSessionHeaders;\n")
        self.write_json(".pi/agent/models.json", {"providers": {
            "llamacpp": {"baseUrl": "http://192.168.42.1:8080/v1", "apiKey": self.OLD_KEY_PI, "models": []},
            mtplx_id: {"baseUrl": "http://192.168.42.1:8000/v1", "apiKey": self.OLD_KEY_PI, "models": [],
                       "headers": {"x-mtplx-client": "pi"}}}})
        self.write_text(".pi/agent/extensions/mtplx-request-policy.ts", "// Pi <-> MTPLX request bridge (adapted)\n")
        if state:
            ids = {"llamacpp": "llamacpp", "mtplx": mtplx_id}
            self.write_json(".config/opencode/llm-deploy.json", {"providers": ids, "model": "llamacpp/qwen3.6-35b-a3b"})
            self.write_json(".pi/agent/llm-deploy.json", {"providers": ids})

    def test_old_mtplx_install_is_cleaned_up(self) -> None:
        self.old_install()
        p = self.run_configure()
        self.assertEqual(p.returncode, 0, p.stderr)
        oc = self.read_json(".config/opencode/opencode.json")
        self.assertEqual(set(oc["provider"]), {"llamacpp"})
        self.assertEqual(oc["provider"]["llamacpp"]["options"]["apiKey"],
                         "{file:" + self.home + "/.config/llm-deploy/api-key}")       # rewritten to the new key path
        self.assertEqual(oc["plugin"], ["/home/u/my-plugin"])                          # the user's plugin stays
        self.assertFalse(os.path.exists(self.path(".config/opencode/plugins/mtplx-session-headers")))
        self.assertEqual(self.read_json(".config/opencode/llm-deploy.json")["providers"], {"llamacpp": "llamacpp"})
        pi = self.read_json(".pi/agent/models.json")
        self.assertEqual(set(pi["providers"]), {"llamacpp"})
        self.assertEqual(pi["providers"]["llamacpp"]["apiKey"], "!cat ~/.config/llm-deploy/api-key")
        self.assertFalse(os.path.exists(self.path(".pi/agent/extensions/mtplx-request-policy.ts")))
        for rel in (".config/opencode/opencode.json", ".pi/agent/models.json"):
            self.assertTrue(os.path.exists(self.path(rel + ".before-carl")), rel)     # the usual backups
            self.assertTrue(any(f.startswith(os.path.basename(rel) + ".bak.")
                                for f in os.listdir(os.path.dirname(self.path(rel)))), rel)
        self.assertIn("OpenCode provider 'mtplx' (MTPLX support was removed", p.stdout)
        self.assertIn("Pi extensions/mtplx-request-policy.ts", p.stdout)
        again = self.run_configure()                                                  # nothing left to remove
        self.assertNotIn("removed", again.stdout)

    def test_old_install_without_state_file_is_recognised_by_signature(self) -> None:
        self.old_install(mtplx_id="llm-deploy-mtplx", state=False,
                         extra_oc={"mtplx": {"name": "my own mtplx", "options": {"apiKey": "{env:MY_KEY}"}}})
        p = self.run_configure()
        self.assertEqual(p.returncode, 0, p.stderr)
        oc = self.read_json(".config/opencode/opencode.json")
        self.assertNotIn("llm-deploy-mtplx", oc["provider"])
        self.assertEqual(oc["provider"]["mtplx"], {"name": "my own mtplx", "options": {"apiKey": "{env:MY_KEY}"}})
        self.assertNotIn("llm-deploy-mtplx", self.read_json(".pi/agent/models.json")["providers"])

    def test_user_owned_mtplx_pieces_are_kept(self) -> None:
        """A provider, plugin folder or Pi extension of the user's own with the old names stays."""
        mine_oc = {"name": "my MTPLX", "options": {"baseURL": "http://10.0.0.5:8000/v1", "apiKey": "{env:MY_KEY}"}}
        mine_pi = {"baseUrl": "http://10.0.0.5:8000/v1", "apiKey": "MY_KEY", "models": []}
        self.write_json(".config/opencode/opencode.json", {"provider": {"mtplx": mine_oc},
                                                           "model": "mtplx/my-model"})
        self.write_text(".config/opencode/plugins/mtplx-session-headers/index.js", "export default async () => ({});\n")
        self.write_json(".pi/agent/models.json", {"providers": {"mtplx": mine_pi}})
        self.write_json(".pi/agent/settings.json", {"defaultProvider": "mtplx", "defaultModel": "my-model"})
        self.write_text(".pi/agent/extensions/mtplx-request-policy.ts", "// my own policy\n")
        # an earlier CARL recorded its MTPLX provider under another id: only that one is ours
        self.write_json(".config/opencode/llm-deploy.json", {"providers": {"llamacpp": "llamacpp", "mtplx": "llm-deploy-mtplx"}})
        p = self.run_configure()
        self.assertEqual(p.returncode, 0, p.stderr)
        oc = self.read_json(".config/opencode/opencode.json")
        self.assertEqual(oc["provider"]["mtplx"], mine_oc)
        self.assertEqual(oc["model"], "mtplx/my-model")
        self.assertIn("that provider no longer exists", p.stdout)
        self.assertTrue(os.path.exists(self.path(".config/opencode/plugins/mtplx-session-headers/index.js")))
        self.assertEqual(self.read_json(".pi/agent/models.json")["providers"]["mtplx"], mine_pi)
        self.assertEqual(self.read_json(".pi/agent/settings.json")["defaultProvider"], "mtplx")
        with open(self.path(".pi/agent/extensions/mtplx-request-policy.ts"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "// my own policy\n")
        self.assertIn("kept      Pi extensions/mtplx-request-policy.ts (yours)", p.stdout)

    # ---------------------------------------------------------------- the coder's name
    CARL_PROMPT = "{file:/h/.config/opencode/llm-deploy/coder.md}"     # what makes an OpenCode agent ours
    PI_OURS = "---\nname: coder\n---\nYou are **coder**, a specialist software engineer.\n"

    def test_fresh_install_names_it_coder_everywhere(self) -> None:
        self.assertEqual(self.run_configure("--coder", "1").returncode, 0)
        self.assertIn("coder", self.read_json(".config/opencode/opencode.json")["agent"])
        with open(self.path(".pi/agent/agents/coder.md"), encoding="utf-8") as f:
            self.assertIn("name: coder\n", f.read())
        with open(self.path(".pi/agent/APPEND_SYSTEM.md"), encoding="utf-8") as f:
            self.assertIn('agent "coder"', f.read())
        self.assertFalse(os.path.exists(self.path(".pi/agent/agents/carl-coder.md")))

    def test_users_own_coder_gives_carl_coder_in_pi(self) -> None:
        self.write_text(".pi/agent/agents/coder.md", "---\nname: coder\n---\nmy own agent\n")
        p = self.run_configure("--coder", "1")
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(self.path(".pi/agent/agents/coder.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "---\nname: coder\n---\nmy own agent\n")
        with open(self.path(".pi/agent/agents/carl-coder.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("name: carl-coder\n", text)
        self.assertIn("You are **carl-coder**", text)
        with open(self.path(".pi/agent/APPEND_SYSTEM.md"), encoding="utf-8") as f:
            self.assertIn('agent "carl-coder"', f.read())
        self.assertIn("ours is 'carl-coder'", p.stdout)

    def test_old_llm_deploy_coder_is_moved(self) -> None:
        """Before 1.2.0 the fallback name was llm-deploy-coder: carl-coder while the user's
        own coder is there, coder once it is gone. Backups of what goes."""
        mine = {"prompt": "mine"}
        self.write_json(".config/opencode/opencode.json", {"agent": {"coder": mine,
                                                                     "llm-deploy-coder": {"prompt": self.CARL_PROMPT}}})
        self.write_text(".pi/agent/agents/coder.md", "my own agent\n")
        self.write_text(".pi/agent/agents/llm-deploy-coder.md", self.PI_OURS.replace("coder", "llm-deploy-coder"))
        p = self.run_configure("--coder", "1")
        self.assertEqual(p.returncode, 0, p.stderr)
        agents = self.read_json(".config/opencode/opencode.json")["agent"]
        self.assertEqual(set(agents), {"coder", "carl-coder"})
        self.assertEqual(agents["coder"], mine)
        self.assertFalse(os.path.exists(self.path(".pi/agent/agents/llm-deploy-coder.md")))
        self.assertTrue(any(f.startswith("llm-deploy-coder.md.bak.") for f in os.listdir(self.path(".pi/agent/agents"))))
        self.assertTrue(os.path.exists(self.path(".pi/agent/agents/carl-coder.md")))
        self.assertIn("OpenCode agent 'llm-deploy-coder' (ours is now 'carl-coder')", p.stdout)
        # the user removes their own coder: ours takes the plain name on the next run
        oc = self.read_json(".config/opencode/opencode.json")
        del oc["agent"]["coder"]
        self.write_json(".config/opencode/opencode.json", oc)
        os.remove(self.path(".pi/agent/agents/coder.md"))
        p = self.run_configure("--coder", "1")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(set(self.read_json(".config/opencode/opencode.json")["agent"]), {"coder"})
        self.assertFalse(os.path.exists(self.path(".pi/agent/agents/carl-coder.md")))
        self.assertTrue(os.path.exists(self.path(".pi/agent/agents/coder.md")))

    def test_users_own_carl_coder_too_skips_ours(self) -> None:
        theirs = {"coder": {"prompt": "mine"}, "carl-coder": {"prompt": "also mine"}}
        self.write_json(".config/opencode/opencode.json", {"agent": dict(theirs)})
        p = self.run_configure("--coder", "1")
        self.assertEqual(p.returncode, 0, p.stderr)
        oc = self.read_json(".config/opencode/opencode.json")
        self.assertEqual(oc["agent"], theirs)
        self.assertNotIn("instructions", oc)
        self.assertIn("'carl-coder' (yours): CARL's coder is not installed", p.stdout)

    def test_coder_off_removes_ours_under_every_name_only(self) -> None:
        mine = {"prompt": "mine"}
        self.write_json(".config/opencode/opencode.json", {"agent": {"coder": mine, "carl-coder": {"prompt": self.CARL_PROMPT},
                                                                     "llm-deploy-coder": {"prompt": self.CARL_PROMPT}}})
        self.write_text(".pi/agent/agents/coder.md", "my own agent\n")
        self.write_text(".pi/agent/agents/carl-coder.md", self.PI_OURS)
        p = self.run_configure("--coder", "0")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.read_json(".config/opencode/opencode.json")["agent"], {"coder": mine})
        self.assertTrue(os.path.exists(self.path(".pi/agent/agents/coder.md")))
        self.assertFalse(os.path.exists(self.path(".pi/agent/agents/carl-coder.md")))

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
