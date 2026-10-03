"""client/configure.py against a throw-away home: fresh install, re-run, user-owned
entries, the coder switch, argument validation, taking the MTPLX pieces of a
pre-1.2.0 install out (ours only), and moving an install from before the rename
(llm-deploy names) to CARL's names."""
from __future__ import annotations

import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from typing import Any

from _paths import CLIENT

SCRIPT = os.path.join(CLIENT, "configure.py")
# installed-models.json as tools/carl.py client-models writes it
MODELS = {"schema": 1, "default": "qwen3.6-35b-a3b", "models": [
    {"id": "qwen3.6-35b-a3b", "label": "Qwen3.6 35B-A3B · Q4", "ctx": 98304, "thinking": "on-off"},
    {"id": "qwen3.8-27b", "label": "Qwen3.8 27B · Q4", "ctx": 131072, "thinking": "effort"}]}


class ConfigureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = self._tmp.name
        self.models: Any = MODELS

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_configure(self, *extra: str, host: str = "192.168.42.1", ctx: str = "98304") -> subprocess.CompletedProcess[str]:
        lst = os.path.join(self.home, "installed-models.json")
        with open(lst, "w", encoding="utf-8") as f:
            json.dump(self.models, f)
        return subprocess.run([sys.executable, SCRIPT, "--bundle", CLIENT, "--home", self.home, "--host", host,
                               "--llama-port", "8080", "--ctx", ctx, "--models", lst, *extra],
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
        self.assertEqual(prov["options"]["apiKey"], "{file:" + self.home + "/.config/carl/api-key}")
        self.assertEqual(set(oc["provider"]), {"llamacpp"})
        check = self.path(".config/opencode/plugins/carl-model-check")
        self.assertEqual(oc["plugin"], [["file:" + check, {"provider": "llamacpp"}]])      # the model warnings
        self.assertTrue(os.path.isfile(os.path.join(check, "check.js")))
        with open(os.path.join(check, "package.json"), encoding="utf-8") as f:     # OpenCode 1.18 loads exports["./server"]
            self.assertIn("./server", json.load(f)["exports"])
        self.assertEqual({k: m["limit"]["context"] for k, m in prov["models"].items()},
                         {"qwen3.6-35b-a3b": 98304, "qwen3.8-27b": 131072})       # each model's own window
        self.assertEqual(oc["model"], "llamacpp/qwen3.6-35b-a3b")
        self.assertIn("coder", oc["agent"])
        pi = self.read_json(".pi/agent/models.json")
        self.assertEqual(set(pi["providers"]), {"llamacpp"})
        self.assertEqual(pi["providers"]["llamacpp"]["apiKey"], "!cat ~/.config/carl/api-key")
        self.assertFalse(os.path.exists(self.path(".pi/agent/extensions/mtplx-request-policy.ts")))
        self.assertFalse(os.path.exists(self.path(".config/opencode/plugins/mtplx-session-headers")))
        self.assertTrue(os.path.exists(self.path(".pi/agent/agents/coder.md")))
        self.assertEqual(oc["instructions"], [self.path(".config/opencode/carl/delegation.md")])
        self.assertEqual(oc["agent"]["coder"]["prompt"], "{file:" + self.path(".config/opencode/carl/coder.md") + "}")
        for rel in (".config/opencode/opencode.json", ".config/opencode/carl.json", ".pi/agent/models.json",
                    ".pi/agent/settings.json", ".pi/agent/carl.json"):
            self.assertEqual(stat.S_IMODE(os.stat(self.path(rel)).st_mode), 0o600, rel)

    def test_one_entry_per_installed_model_with_its_thinking(self) -> None:
        p = self.run_configure("--running", "qwen3.8-27b", ctx="65536")
        self.assertEqual(p.returncode, 0, p.stderr)
        oc = self.read_json(".config/opencode/opencode.json")["provider"]["llamacpp"]["models"]
        moe, dense = oc["qwen3.6-35b-a3b"], oc["qwen3.8-27b"]
        self.assertEqual(moe["name"], "Qwen3.6 35B-A3B · Q4 — llama.cpp, 96K")
        self.assertEqual(dense["name"], "Qwen3.8 27B · Q4 — llama.cpp, 64K")             # the running server's window
        self.assertEqual(dense["limit"], {"context": 65536, "output": 32000})
        self.assertEqual((moe["options"], dense["options"]), ({"reasoningEffort": "high"}, {"reasoningEffort": "low"}))
        self.assertEqual({k for k, v in moe["variants"].items() if "disabled" not in v}, {"none", "high"})
        self.assertEqual({k for k, v in dense["variants"].items() if "disabled" not in v}, {"none", "low", "medium", "xhigh"})
        pi = self.read_json(".pi/agent/models.json")["providers"]["llamacpp"]["models"]
        self.assertEqual([m["id"] for m in pi], ["qwen3.6-35b-a3b", "qwen3.8-27b"])
        self.assertEqual(pi[0]["thinkingLevelMap"], {"minimal": None, "low": None, "medium": None, "xhigh": None})
        self.assertEqual(pi[1]["contextWindow"], 65536)

    def test_a_deleted_model_goes_and_our_default_follows(self) -> None:
        self.assertEqual(self.run_configure().returncode, 0)
        self.models = {"schema": 1, "default": "qwen3.8-27b", "models": [MODELS["models"][1]]}
        p = self.run_configure()
        self.assertEqual(p.returncode, 0, p.stderr)
        oc = self.read_json(".config/opencode/opencode.json")
        self.assertEqual(list(oc["provider"]["llamacpp"]["models"]), ["qwen3.8-27b"])
        self.assertEqual(oc["model"], "llamacpp/qwen3.8-27b")                    # ours before, so it follows
        self.assertEqual(self.read_json(".pi/agent/settings.json")["defaultModel"], "qwen3.8-27b")

    def test_no_model_installed_yet(self) -> None:
        self.models = {"schema": 1, "default": None, "models": []}
        p = self.run_configure()
        self.assertEqual(p.returncode, 0, p.stderr)
        oc = self.read_json(".config/opencode/opencode.json")
        self.assertEqual(oc["provider"]["llamacpp"]["models"], {})
        self.assertNotIn("model", oc)
        self.assertIn("no model is installed on the server yet", p.stdout)

    def test_model_check_plugin_follows_the_provider_and_can_be_left_out(self) -> None:
        self.write_json(".config/opencode/opencode.json", {"provider": {"llamacpp": {"npm": "mine", "models": {}}},
                                                           "plugin": ["/home/u/mine.js"]})
        self.assertEqual(self.run_configure().returncode, 0)
        entry = "file:" + self.path(".config/opencode/plugins/carl-model-check")
        oc = self.read_json(".config/opencode/opencode.json")
        self.assertEqual(oc["plugin"], ["/home/u/mine.js", [entry, {"provider": "carl"}]])  # ours is "carl" here
        p = self.run_configure("--model-check", "0")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.read_json(".config/opencode/opencode.json")["plugin"], ["/home/u/mine.js"])
        self.assertFalse(os.path.exists(self.path(".config/opencode/plugins/carl-model-check")))
        self.assertIn("removed   OpenCode plugin carl-model-check", p.stdout)

    def test_a_bad_model_list_is_refused(self) -> None:
        for bad in ({"schema": 2, "models": []}, {"schema": 1, "models": [{"id": "a b", "ctx": 4096, "thinking": "on-off"}]},
                    {"schema": 1, "models": [{"id": "a", "ctx": 4096, "thinking": "maybe"}]},
                    {"schema": 1, "models": [{"id": "a", "label": "x\x1b[2J", "ctx": 4096, "thinking": "on-off"}]}):
            with self.subTest(bad=bad):
                self.models = bad
                p = self.run_configure()
                self.assertEqual(p.returncode, 2)
                self.assertIn("--models", p.stderr)

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
        self.assertIn("carl", oc["provider"])
        self.assertEqual(oc["provider"]["carl"]["name"], "llama.cpp (Mac host) [carl]")
        self.assertEqual(oc["model"], "other/model")
        self.assertEqual(oc["agent"]["coder"], {"prompt": "mine"})
        self.assertIn("carl-coder", oc["agent"])                           # ours, next to the user's own coder
        with open(self.path(".config/opencode/carl/delegation.md"), encoding="utf-8") as f:
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
                         "{file:" + self.home + "/.config/carl/api-key}")             # rewritten to the new key path
        self.assertEqual(oc["plugin"][0], "/home/u/my-plugin")                         # the user's plugin stays
        self.assertEqual(len(oc["plugin"]), 2)                                          # + carl-model-check
        self.assertFalse(os.path.exists(self.path(".config/opencode/plugins/mtplx-session-headers")))
        self.assertEqual(self.read_json(".config/opencode/carl.json")["providers"], {"llamacpp": "llamacpp"})
        self.assertFalse(os.path.exists(self.path(".config/opencode/llm-deploy.json")))   # the old state file
        pi = self.read_json(".pi/agent/models.json")
        self.assertEqual(set(pi["providers"]), {"llamacpp"})
        self.assertEqual(pi["providers"]["llamacpp"]["apiKey"], "!cat ~/.config/carl/api-key")
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
    CARL_PROMPT = "{file:/h/.config/opencode/llm-deploy/coder.md}"     # what made an OpenCode agent ours (before the rename)
    NEW_PROMPT = "{file:/h/.config/opencode/carl/coder.md}"            # ... and since
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
        self.write_json(".config/opencode/opencode.json", {"agent": {"coder": mine, "carl-coder": {"prompt": self.NEW_PROMPT},
                                                                     "llm-deploy-coder": {"prompt": self.CARL_PROMPT}}})
        self.write_text(".pi/agent/agents/coder.md", "my own agent\n")
        self.write_text(".pi/agent/agents/carl-coder.md", self.PI_OURS)
        p = self.run_configure("--coder", "0")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.read_json(".config/opencode/opencode.json")["agent"], {"coder": mine})
        self.assertTrue(os.path.exists(self.path(".pi/agent/agents/coder.md")))
        self.assertFalse(os.path.exists(self.path(".pi/agent/agents/carl-coder.md")))

    # ---------------------------------------------------------------- the rename: llm-deploy -> carl
    OLD_KEY = "{file:%s/.config/llm-deploy/api-key}"
    PI_EXT_OLD = "// Changes for LLM-Deploy, all marked \"LLM-Deploy:\"\n// LLM-Deploy: the agents\n"
    USER_SYSTEM = "Always answer in English."

    def llm_deploy_install(self, alt: bool = False) -> None:
        """What CARL 1.2.0 before the rename left in a home (a copy of a real one, trimmed): the
        llm-deploy state files, prompt folder, key path, Pi extension marker and APPEND_SYSTEM
        block. alt: the user owns "llamacpp", so ours is "llm-deploy" and the defaults point at it."""
        oc = self.path(".config/opencode")
        ours = "llm-deploy" if alt else "llamacpp"
        prov = {"npm": "@ai-sdk/openai-compatible", "name": "llama.cpp (Mac host)" + (" [llm-deploy]" if alt else ""),
                "options": {"baseURL": "http://127.0.0.1:8080/v1", "apiKey": self.OLD_KEY % self.home}, "models": {}}
        providers: dict[str, Any] = {ours: prov}
        if alt:
            providers["llamacpp"] = {"name": "my llama", "options": {"apiKey": "{env:MY_KEY}"}}
        default = f"{ours}/qwen3.6-35b-a3b"
        self.write_json(".config/opencode/opencode.json", {
            "$schema": "https://opencode.ai/config.json", "provider": providers, "model": default, "small_model": default,
            "agent": {"coder": {"mode": "subagent", "prompt": "{file:" + oc + "/llm-deploy/coder.md}"}},
            "instructions": [oc + "/llm-deploy/delegation.md", "/home/u/my-rules.md"]})
        self.write_text(".config/opencode/llm-deploy/coder.md", "You are **coder**, a specialist software engineer.\n")
        self.write_text(".config/opencode/llm-deploy/delegation.md", "## Delegating to the coder\n")
        self.write_json(".config/opencode/llm-deploy.json", {"model": default, "small_model": default, "coder_agent": "coder",
                                                             "providers": {"llamacpp": ours}})
        pi_prov = {"baseUrl": "http://127.0.0.1:8080/v1", "apiKey": "!cat ~/.config/llm-deploy/api-key", "models": []}
        pi_providers: dict[str, Any] = {ours: pi_prov}
        if alt:
            pi_providers["llamacpp"] = {"baseUrl": "http://10.0.0.5:8080/v1", "apiKey": "MY_KEY", "models": []}
        self.write_json(".pi/agent/models.json", {"providers": pi_providers})
        settings = {"defaultProvider": ours, "defaultModel": "qwen3.6-35b-a3b", "defaultThinkingLevel": "low"}
        self.write_json(".pi/agent/settings.json", settings)
        self.write_json(".pi/agent/llm-deploy.json", {"settings": settings, "subagent_ext": True, "coder_agent": "coder",
                                                      "providers": {"llamacpp": ours}})
        self.write_text(".pi/agent/agents/coder.md", self.PI_OURS)
        self.write_text(".pi/agent/extensions/subagent/index.ts", self.PI_EXT_OLD)
        self.write_text(".pi/agent/APPEND_SYSTEM.md", "<!-- llm-deploy:delegation begin -->\nold rule\n"
                                                      "<!-- llm-deploy:delegation end -->\n\n" + self.USER_SYSTEM + "\n")

    def read_text(self, rel: str) -> str:
        with open(self.path(rel), encoding="utf-8") as f:
            return f.read()

    def test_llm_deploy_install_gets_carl_names(self) -> None:
        self.llm_deploy_install()
        p = self.run_configure("--coder", "1")
        self.assertEqual(p.returncode, 0, p.stderr)
        oc_dir = self.path(".config/opencode")
        oc = self.read_json(".config/opencode/opencode.json")
        self.assertEqual(set(oc["provider"]), {"llamacpp"})
        self.assertEqual(oc["provider"]["llamacpp"]["options"]["apiKey"], "{file:" + self.home + "/.config/carl/api-key}")
        self.assertEqual((oc["model"], oc["small_model"]), ("llamacpp/qwen3.6-35b-a3b",) * 2)
        self.assertEqual(oc["agent"]["coder"]["prompt"], "{file:" + oc_dir + "/carl/coder.md}")
        self.assertEqual(oc["instructions"], ["/home/u/my-rules.md", oc_dir + "/carl/delegation.md"])
        self.assertEqual(sorted(os.listdir(self.path(".config/opencode/carl"))), ["coder.md", "delegation.md"])
        self.assertFalse(os.path.exists(self.path(".config/opencode/llm-deploy")))
        # the state files: moved to carl.json (same content, plus this run), a copy of the old one kept
        for folder, client in ((".config/opencode", "OpenCode"), (".pi/agent", "Pi")):
            self.assertFalse(os.path.exists(self.path(folder + "/llm-deploy.json")), folder)
            self.assertTrue(any(f.startswith("llm-deploy.json.bak.") for f in os.listdir(self.path(folder))), folder)
            st = self.read_json(folder + "/carl.json")
            self.assertEqual((st["providers"], st["coder_agent"]), ({"llamacpp": "llamacpp"}, "coder"), folder)
            self.assertIn(f"{client} state file ~/{folder}/llm-deploy.json -> carl.json", p.stdout)
        pi = self.read_json(".pi/agent/models.json")
        self.assertEqual(pi["providers"]["llamacpp"]["apiKey"], "!cat ~/.config/carl/api-key")
        self.assertEqual(self.read_json(".pi/agent/settings.json")["defaultProvider"], "llamacpp")
        ext = self.read_text(".pi/agent/extensions/subagent/index.ts")       # ours: replaced by the bundled copy
        self.assertIn('all marked "CARL:"', ext)
        self.assertNotIn("LLM-Deploy", ext)
        system = self.read_text(".pi/agent/APPEND_SYSTEM.md")
        self.assertTrue(system.startswith(self.USER_SYSTEM + "\n\n<!-- carl:delegation begin -->\n"), system)
        self.assertNotIn("llm-deploy", system)
        self.assertEqual(system.count("delegation begin"), 1)
        for rel in (".config/opencode/opencode.json", ".pi/agent/models.json", ".pi/agent/APPEND_SYSTEM.md"):
            self.assertTrue(os.path.exists(self.path(rel + ".before-carl")), rel)          # the usual backups
        self.assertIn("removed   ~/.config/opencode/llm-deploy (CARL's prompts are in carl/ now)", p.stdout)
        again = self.run_configure("--coder", "1")                                         # moved once
        self.assertEqual(again.returncode, 0, again.stderr)
        for kind in ("added", "updated", "removed"):
            self.assertNotIn(kind, again.stdout)

    def test_users_own_llamacpp_moves_ours_from_llm_deploy_to_carl(self) -> None:
        """Ours was "llm-deploy" next to the user's "llamacpp": it becomes "carl", and the
        defaults CARL set follow it; the user's provider stays as it was."""
        self.llm_deploy_install(alt=True)
        theirs_oc = self.read_json(".config/opencode/opencode.json")["provider"]["llamacpp"]
        theirs_pi = self.read_json(".pi/agent/models.json")["providers"]["llamacpp"]
        p = self.run_configure("--coder", "1")
        self.assertEqual(p.returncode, 0, p.stderr)
        oc = self.read_json(".config/opencode/opencode.json")
        self.assertEqual(set(oc["provider"]), {"llamacpp", "carl"})
        self.assertEqual(oc["provider"]["llamacpp"], theirs_oc)
        self.assertEqual(oc["provider"]["carl"]["name"], "llama.cpp (Mac host) [carl]")
        self.assertEqual((oc["model"], oc["small_model"]), ("carl/qwen3.6-35b-a3b",) * 2)
        self.assertEqual(self.read_json(".config/opencode/carl.json")["providers"], {"llamacpp": "carl"})
        pi = self.read_json(".pi/agent/models.json")
        self.assertEqual(set(pi["providers"]), {"llamacpp", "carl"})
        self.assertEqual(pi["providers"]["llamacpp"], theirs_pi)
        self.assertEqual(self.read_json(".pi/agent/settings.json")["defaultProvider"], "carl")
        self.assertIn("OpenCode provider 'llm-deploy' (CARL's provider is now 'carl')", p.stdout)
        self.assertIn("Pi provider 'llm-deploy' (CARL's provider is now 'carl')", p.stdout)

    def test_users_own_default_on_the_old_id_is_kept_with_a_hint(self) -> None:
        self.llm_deploy_install(alt=True)
        oc = self.read_json(".config/opencode/opencode.json")
        oc["model"] = "llm-deploy/qwen3.8-27b"                    # picked by the user, not by CARL
        self.write_json(".config/opencode/opencode.json", oc)
        self.write_json(".pi/agent/settings.json", {"defaultProvider": "llm-deploy", "defaultModel": "qwen3.8-27b"})
        p = self.run_configure()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.read_json(".config/opencode/opencode.json")["model"], "llm-deploy/qwen3.8-27b")
        self.assertEqual(self.read_json(".pi/agent/settings.json")["defaultProvider"], "llm-deploy")
        self.assertIn("OpenCode model = llm-deploy/qwen3.8-27b (yours; that provider is now 'carl': pick carl/qwen3.8-27b)",
                      p.stdout)
        self.assertIn("Pi defaults (yours: llm-deploy/qwen3.8-27b; that provider is now 'carl'", p.stdout)

    def test_users_own_llm_deploy_pieces_are_kept(self) -> None:
        """A provider of the user's own called llm-deploy (not on our key file, not recorded),
        their own Pi subagent extension, and their files in the old prompt folder stay."""
        mine = {"name": "my server", "options": {"baseURL": "http://10.0.0.9:8080/v1", "apiKey": "{env:MY_KEY}"}}
        self.write_json(".config/opencode/opencode.json", {"provider": {"llm-deploy": mine}})
        self.write_text(".config/opencode/llm-deploy/coder.md", "old CARL prompt\n")
        self.write_text(".config/opencode/llm-deploy/notes.md", "my notes\n")
        self.write_json(".pi/agent/models.json", {"providers": {"llm-deploy": {"apiKey": "MY_KEY", "models": []}}})
        self.write_text(".pi/agent/extensions/subagent/index.ts", "// my own subagent tool\n")
        p = self.run_configure("--coder", "1")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.read_json(".config/opencode/opencode.json")["provider"]["llm-deploy"], mine)
        self.assertIn("llamacpp", self.read_json(".config/opencode/opencode.json")["provider"])
        self.assertIn("llm-deploy", self.read_json(".pi/agent/models.json")["providers"])
        self.assertEqual(os.listdir(self.path(".config/opencode/llm-deploy")), ["notes.md"])
        self.assertIn("kept      ~/.config/opencode/llm-deploy (not ours", p.stdout)
        self.assertEqual(self.read_text(".pi/agent/extensions/subagent/index.ts"), "// my own subagent tool\n")

    def test_both_state_files_carl_json_wins(self) -> None:
        """A mixed home (carl.json written, the old state file still there): carl.json is
        what counts; the old one goes (a copy stays)."""
        self.write_json(".config/opencode/opencode.json", {"provider": {"llamacpp": {"name": "my llama"}}})
        self.write_json(".config/opencode/carl.json", {"providers": {"llamacpp": "carl"}})
        self.write_json(".config/opencode/llm-deploy.json", {"providers": {"llamacpp": "llamacpp"}, "model": "x/y"})
        p = self.run_configure()
        self.assertEqual(p.returncode, 0, p.stderr)
        oc = self.read_json(".config/opencode/opencode.json")
        self.assertEqual(oc["provider"]["llamacpp"], {"name": "my llama"})
        self.assertIn("carl", oc["provider"])
        self.assertEqual(oc["model"], "carl/qwen3.6-35b-a3b")         # the old file would have said "llamacpp"
        self.assertFalse(os.path.exists(self.path(".config/opencode/llm-deploy.json")))
        self.assertTrue(any(f.startswith("llm-deploy.json.bak.") for f in os.listdir(self.path(".config/opencode"))))

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


class InstallScriptTests(unittest.TestCase):
    """client/install.sh in a throw-away home with no server (port closed): the key folder's
    old name is moved, and the configs it writes read the key from the new place."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = self._tmp.name
        # a copied bundle, as in a VM (no ../tools/carl.py): nothing is written into the repo
        self.bundle = os.path.join(self.home, "client")
        shutil.copytree(CLIENT, self.bundle, ignore=shutil.ignore_patterns("installed-models.json", "__pycache__"))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def install(self) -> subprocess.CompletedProcess[str]:
        with socket.socket() as sock:                       # a port nothing listens on
            sock.bind(("127.0.0.1", 0))
            port = str(sock.getsockname()[1])
        env = {k: v for k, v in os.environ.items() if k not in ("CARL_API_KEY", "LLAMA_CTX", "CODER", "NO_CODER")}
        return subprocess.run(["bash", os.path.join(self.bundle, "install.sh"), "--host", "127.0.0.1", "--port", port],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL, env={**env, "HOME": self.home})

    def write(self, rel: str, text: str) -> None:
        os.makedirs(os.path.dirname(os.path.join(self.home, rel)), exist_ok=True)
        with open(os.path.join(self.home, rel), "w", encoding="utf-8") as f:
            f.write(text)

    def test_old_key_folder_is_moved_and_used(self) -> None:
        self.write(".config/llm-deploy/api-key", "vmsecret")
        p = self.install()
        self.assertEqual(p.returncode, 0, p.stderr)
        carl, old = os.path.join(self.home, ".config/carl"), os.path.join(self.home, ".config/llm-deploy")
        self.assertIn("Moved ~/.config/llm-deploy to ~/.config/carl", p.stdout)
        self.assertIn("Using the API key in ~/.config/carl/api-key", p.stdout)
        self.assertEqual(os.readlink(old), "carl")
        self.assertEqual(stat.S_IMODE(os.stat(carl).st_mode), 0o700)
        with open(os.path.join(carl, "api-key"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "vmsecret")
        with open(os.path.join(self.home, ".config/opencode/opencode.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["provider"]["llamacpp"]["options"]["apiKey"], "{file:" + carl + "/api-key}")
        self.assertNotIn("vmsecret", p.stdout + p.stderr)

    def test_without_a_model_list_or_server_no_entries_and_a_note(self) -> None:
        self.write(".config/carl/api-key", "k")
        p = self.install()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("no installed-models.json here", p.stdout)
        self.assertIn("models for the clients: none yet", p.stdout)
        with open(os.path.join(self.home, ".config/opencode/opencode.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["provider"]["llamacpp"]["models"], {})

    def test_a_copied_model_list_is_used(self) -> None:
        self.write(".config/carl/api-key", "k")
        with open(os.path.join(self.bundle, "installed-models.json"), "w", encoding="utf-8") as f:
            json.dump(MODELS, f)
        p = self.install()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("models for the clients: qwen3.6-35b-a3b, qwen3.8-27b", p.stdout)
        with open(os.path.join(self.home, ".pi/agent/models.json"), encoding="utf-8") as f:
            self.assertEqual([m["id"] for m in json.load(f)["providers"]["llamacpp"]["models"]],
                             ["qwen3.6-35b-a3b", "qwen3.8-27b"])

    def test_both_folders_reuse_the_old_key(self) -> None:
        self.write(".config/llm-deploy/api-key", "vmsecret")
        os.makedirs(os.path.join(self.home, ".config/carl"))
        p = self.install()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("Reusing the API key from ~/.config/llm-deploy/api-key", p.stdout)
        self.assertFalse(os.path.islink(os.path.join(self.home, ".config/llm-deploy")))
        with open(os.path.join(self.home, ".config/carl/api-key"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "vmsecret")


if __name__ == "__main__":
    unittest.main()
