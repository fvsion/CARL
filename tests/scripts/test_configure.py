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

from _paths import CLIENT, load_script

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
        cache = self.path(".config/opencode/plugins/carl-cache")
        bg = self.path(".config/opencode/plugins/carl-background")
        self.assertEqual(oc["plugin"], [["file:" + check, {"provider": "llamacpp"}],     # the model warnings
                                        ["file:" + cache, {"provider": "llamacpp",       # the prompt cache
                                                           "cacheApi": "http://192.168.42.1:8081"}],
                                        ["file:" + bg, {"provider": "llamacpp"}]])       # the coder in the background
        self.assertEqual(self.read_json(".pi/agent/carl.json")["cache_api"], "http://192.168.42.1:8081")
        with open(os.path.join(cache, "package.json"), encoding="utf-8") as f:
            self.assertIn("./server", json.load(f)["exports"])
        for d in (cache, self.path(".pi/agent/extensions/carl-cache")):            # each carries the shared core
            self.assertTrue(os.path.isfile(os.path.join(d, "carl-cache.js")), d)
            self.assertTrue(os.path.isfile(os.path.join(d, "carl-panel.js")), d)
        panel = self.path(".config/opencode/plugins/carl-panel")                   # /carl in OpenCode and in Pi
        self.assertIn("file:" + panel, self.read_json(".config/opencode/tui.json")["plugin"])
        for d in (panel, self.path(".pi/agent/extensions/carl-panel")):
            self.assertTrue(os.path.isfile(os.path.join(d, "carl-panel.js")), d)
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
        self.assertEqual((moe["options"], dense["options"]), ({"reasoningEffort": "high", "parallel_tool_calls": True},
                                                              {"reasoningEffort": "low", "parallel_tool_calls": True}))
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
        self.assertEqual(oc["plugin"][:2], ["/home/u/mine.js", [entry, {"provider": "carl"}]])  # ours is "carl" here
        p = self.run_configure("--model-check", "0", "--cache", "0", "--background", "0")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.read_json(".config/opencode/opencode.json")["plugin"], ["/home/u/mine.js"])
        self.assertFalse(os.path.exists(self.path(".config/opencode/plugins/carl-model-check")))
        self.assertIn("removed   OpenCode plugin carl-model-check", p.stdout)
        self.assertFalse(os.path.exists(self.path(".pi/agent/extensions/carl-cache")))
        self.assertIn("removed   Pi extension carl-cache", p.stdout)

    def test_the_phase_11_prefix_plugin_is_replaced(self) -> None:
        old = self.path(".config/opencode/plugins/carl-prefix-cache")
        os.makedirs(old)
        specs = self.path(".config/carl/prefix")
        os.makedirs(specs)
        self.write_json(".config/opencode/opencode.json", {"plugin": [["file:" + old, {"provider": "llamacpp"}]]})
        p = self.run_configure()
        self.assertEqual(p.returncode, 0, p.stderr)
        names = [x[0] if isinstance(x, list) else x for x in self.read_json(".config/opencode/opencode.json")["plugin"]]
        self.assertNotIn("file:" + old, names)
        self.assertIn("file:" + self.path(".config/opencode/plugins/carl-cache"), names)
        self.assertFalse(os.path.exists(old) or os.path.exists(specs))

    def test_a_pi_extension_of_that_name_that_is_not_ours_stays(self) -> None:
        mine = self.path(".pi/agent/extensions/carl-cache")
        os.makedirs(mine)
        with open(os.path.join(mine, "index.ts"), "w", encoding="utf-8") as f:
            f.write("export default function () {}\n")
        p = self.run_configure()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("kept      Pi extensions/carl-cache (yours)", p.stdout)
        self.assertFalse(os.path.exists(os.path.join(mine, "carl-cache.js")))

    def test_tools_on_by_default(self) -> None:
        p = self.run_configure()
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(self.path(".config/carl/opencode.env"), encoding="utf-8") as f:
            env = f.read()
        for line in ("export OPENCODE_ENABLE_EXA=1", "export OPENCODE_WEBSEARCH_PROVIDER=exa",
                     "export OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=1", "export OPENCODE_EXPERIMENTAL_LSP_TOOL=1"):
            self.assertIn(line, env)
        self.assertIs(self.read_json(".config/opencode/opencode.json")["lsp"], True)
        self.assertFalse(os.path.exists(self.path(".zshrc")))                 # never created
        self.assertIn("add this line to your shell profile", p.stdout)
        sett = self.read_json(".pi/agent/settings.json")
        self.assertEqual(sett["defaultTools"], ["+grep", "+find", "+ls"])
        mcp = self.read_json(".pi/agent/mcp.json")["mcpServers"]["carl-web-search"]
        self.assertEqual(mcp["url"], "https://mcp.exa.ai/mcp")

    def test_tools_off_and_yours_kept(self) -> None:
        with open(self.path(".bashrc"), "w", encoding="utf-8") as f:
            f.write("export MINE=1\n")
        self.write_json(".pi/agent/settings.json", {"defaultTools": ["read"]})
        self.write_json(".pi/agent/mcp.json", {"mcpServers": {"mine": {"url": "https://x/mcp"}}})
        self.assertEqual(self.run_configure().returncode, 0)
        with open(self.path(".bashrc"), encoding="utf-8") as f:
            self.assertIn("# >>> CARL: OpenCode tool switches >>>", f.read())
        self.assertEqual(self.read_json(".pi/agent/settings.json")["defaultTools"], ["read"])     # yours stays
        p = self.run_configure("--web-search", "off", "--lsp", "0", "--background", "0", "--browser", "0")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse(os.path.exists(self.path(".config/carl/opencode.env")))
        with open(self.path(".bashrc"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "export MINE=1\n")                    # the block is gone, yours stays
        self.assertNotIn("lsp", self.read_json(".config/opencode/opencode.json"))
        self.assertEqual(self.read_json(".pi/agent/mcp.json")["mcpServers"], {"mine": {"url": "https://x/mcp"}})

    def test_browser_agent_has_the_browser_tools_the_main_agents_do_not(self) -> None:
        self.assertEqual(self.run_configure().returncode, 0)
        oc = self.read_json(".config/opencode/opencode.json")
        server = oc["mcp"]["carl-browser"]
        self.assertEqual(server["command"][:4], ["npx", "-y", "@playwright/mcp@0.0.83", "--isolated"])
        self.assertIn("--headless", server["command"])
        self.assertIs(oc["tools"]["carl-browser_*"], False)                  # the main agents: off
        b = oc["agent"]["browser"]
        self.assertEqual((b["mode"], b["tools"]["carl-browser_*"], b["tools"]["bash"]), ("subagent", True, False))
        self.assertTrue(os.path.isfile(self.path(".config/opencode/carl/browser.md")))
        pi = self.read_json(".pi/agent/mcp.json")["mcpServers"]["carl-browser"]
        self.assertEqual((pi["command"], pi["exposure"]), ("npx", "deferred"))
        p = self.run_configure("--browser", "0")
        self.assertEqual(p.returncode, 0, p.stderr)
        oc = self.read_json(".config/opencode/opencode.json")
        self.assertNotIn("browser", oc.get("agent", {}))
        self.assertNotIn("mcp", oc)
        self.assertNotIn("carl-browser", self.read_json(".pi/agent/mcp.json")["mcpServers"])

    def test_a_users_own_browser_agent_stays(self) -> None:
        mine = {"description": "my browser", "mode": "subagent", "prompt": "mine"}
        self.write_json(".config/opencode/opencode.json", {"agent": {"browser": mine}})
        self.assertEqual(self.run_configure().returncode, 0)
        agents = self.read_json(".config/opencode/opencode.json")["agent"]
        self.assertEqual(agents["browser"], mine)
        self.assertIn("carl-browser", agents)                                  # ours, next to it

    def test_a_symlinked_profile_stays_a_link(self) -> None:
        os.makedirs(self.path("dotfiles"))
        with open(self.path("dotfiles/zshrc"), "w", encoding="utf-8") as f:
            f.write("export MINE=1\n")
        os.chmod(self.path("dotfiles/zshrc"), 0o600)
        os.symlink(self.path("dotfiles/zshrc"), self.path(".zshrc"))
        p = self.run_configure()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(os.path.islink(self.path(".zshrc")))
        with open(self.path("dotfiles/zshrc"), encoding="utf-8") as f:
            text = f.read()
        self.assertTrue(text.startswith("export MINE=1\n"))
        self.assertIn("# >>> CARL: OpenCode tool switches >>>", text)
        self.assertEqual(stat.S_IMODE(os.stat(self.path("dotfiles/zshrc")).st_mode), 0o600)     # its mode stays
        self.assertIn("its link target", p.stdout)

    def test_no_profile_prints_the_line(self) -> None:
        with open(self.path(".zshrc"), "w", encoding="utf-8") as f:
            f.write("export MINE=1\n")
        p = self.run_configure("--profile", "0")
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(self.path(".zshrc"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "export MINE=1\n")
        self.assertIn("NO_PROFILE=1", p.stdout)

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
        self.assertEqual(len(oc["plugin"]), 4)                                          # + CARL's three plugins
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

    def test_background_subagents_in_the_delegation_rule(self) -> None:
        """On (the default): each client's own "run the coder in the background" paragraph, Pi's extension
        told through carl.json; --background 0: neither, no markers left."""
        self.assertEqual(self.run_configure("--coder", "1").returncode, 0)
        with open(self.path(".config/opencode/carl/delegation.md"), encoding="utf-8") as f:
            oc = f.read()
        with open(self.path(".pi/agent/APPEND_SYSTEM.md"), encoding="utf-8") as f:
            pi = f.read()
        self.assertIn("the task tool's `background: true`", oc)
        self.assertNotIn("subagent tool's `background: true`", oc)
        self.assertIn("the subagent tool's `background: true`", pi)
        self.assertNotIn("carl:background", oc + pi)
        self.assertIn('subagent_type "browser"', oc)                    # the coder's browser check: the browser agent
        self.assertIn("Load the browser tools (`tool_search`)", pi)
        self.assertNotIn("carl:browser", oc + pi)
        self.assertNotIn("carl:nobrowser", oc + pi)
        self.assertIn("message when it ends: do not wait, poll or check on it.\n\n**Browser checks", oc)
        self.assertTrue(self.read_json(".pi/agent/carl.json")["background_subagents"])
        plugins = json.dumps(self.read_json(".config/opencode/opencode.json")["plugin"])
        self.assertIn("plugins/carl-background", plugins)                 # sets background: true for the coder
        with open(self.path(".pi/agent/agents/coder.md"), encoding="utf-8") as f:
            self.assertIn("exclude-tools: subagent, tool_search\n", f.read())   # no nested subagents, no browser
        self.assertEqual(self.run_configure("--coder", "1", "--background", "0").returncode, 0)
        with open(self.path(".config/opencode/carl/delegation.md"), encoding="utf-8") as f:
            oc = f.read()
        with open(self.path(".pi/agent/APPEND_SYSTEM.md"), encoding="utf-8") as f:
            pi = f.read()
        self.assertNotIn("background: true", oc + pi)
        self.assertNotIn("carl:background", oc + pi)
        self.assertEqual(self.run_configure("--coder", "1", "--browser", "0", "--background", "0").returncode, 0)
        with open(self.path(".config/opencode/carl/delegation.md"), encoding="utf-8") as f:
            oc = f.read()
        self.assertIn("pass that list on to the user", oc)                # no browser: the user checks by hand
        self.assertNotIn('subagent_type "browser"', oc)
        self.assertFalse(self.read_json(".pi/agent/carl.json")["background_subagents"])
        self.assertNotIn("carl-background", json.dumps(self.read_json(".config/opencode/opencode.json")["plugin"]))
        self.assertFalse(os.path.exists(self.path(".config/opencode/plugins/carl-background")))

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
        p = self.run_configure("--browser", "0", "--coder", "1")
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
        p = self.run_configure("--browser", "0", "--coder", "1")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(set(self.read_json(".config/opencode/opencode.json")["agent"]), {"coder"})
        self.assertFalse(os.path.exists(self.path(".pi/agent/agents/carl-coder.md")))
        self.assertTrue(os.path.exists(self.path(".pi/agent/agents/coder.md")))

    def test_users_own_carl_coder_too_skips_ours(self) -> None:
        theirs = {"coder": {"prompt": "mine"}, "carl-coder": {"prompt": "also mine"}}
        self.write_json(".config/opencode/opencode.json", {"agent": dict(theirs)})
        p = self.run_configure("--browser", "0", "--coder", "1")
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
        p = self.run_configure("--browser", "0", "--coder", "0")
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
        p = self.run_configure("--browser", "0", "--coder", "1")
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
        again = self.run_configure("--browser", "0", "--coder", "1")                                         # moved once
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


    def test_tui_plugins_carry_the_shared_helpers_and_go_with_their_switch(self) -> None:
        self.assertEqual(self.run_configure().returncode, 0)
        plugins = self.path(".config/opencode/plugins")
        for name in ("session-switcher", "subagents-sidebar"):
            self.assertTrue(os.path.isfile(os.path.join(plugins, name, "carl-tui.js")), name)
        p = self.run_configure("--switcher", "0", "--sidebar", "0")        # NO_SWITCHER=1 NO_SIDEBAR=1
        self.assertEqual(p.returncode, 0, p.stderr)
        for name in ("session-switcher", "subagents-sidebar"):
            self.assertFalse(os.path.exists(os.path.join(plugins, name)), name)
        self.assertEqual(self.read_json(".config/opencode/tui.json")["plugin"], ["file:" + os.path.join(plugins, "carl-panel")])
        self.assertIn("removed   OpenCode session switcher", p.stdout)

    def test_a_bad_pi_config_stops_the_run_before_any_write(self) -> None:
        self.write_json(".pi/agent/settings.json", [1])
        p = self.run_configure()
        self.assertEqual(p.returncode, 1)
        self.assertIn("settings.json is not a JSON object", p.stderr)
        self.assertFalse(os.path.exists(self.path(".config")))                  # OpenCode untouched too


cfg = load_script(SCRIPT, "carl_configure")


class MergeRuleTests(unittest.TestCase):
    """The merge rules alone: plain functions on parsed configs, no disk."""

    def test_profile_block_is_appended_once_and_taken_out_again(self) -> None:
        on = cfg.profile_text("export A=1\n\n", True)
        self.assertEqual(on, f"export A=1\n\n{cfg.PROFILE_BEGIN}\n{cfg.PROFILE_LINE}\n{cfg.PROFILE_END}\n")
        self.assertEqual(cfg.profile_text(on, True), on)
        self.assertEqual(cfg.profile_text(on, False), "export A=1\n")
        self.assertEqual(cfg.profile_text("", False), "")

    def test_plugin_entry_replaces_ours_and_keeps_the_rest(self) -> None:
        rep = cfg.Report()
        conf: dict[str, Any] = {"plugin": ["mine.js", ["file:/p/x", {"old": 1}]]}
        want = ["file:/p/x", {"provider": "llamacpp"}]
        self.assertFalse(cfg.merge_plugin_entry(conf, "file:/p/x", want, True, "x (what)", rep))
        self.assertEqual(conf["plugin"], ["mine.js", want])
        self.assertEqual(rep.lines(), ["  updated   OpenCode plugin x (what)"])
        self.assertTrue(cfg.merge_plugin_entry(conf, "file:/p/x", None, False, "x", rep))
        self.assertEqual(conf["plugin"], ["mine.js"])
        self.assertFalse(cfg.merge_plugin_entry(conf, "file:/p/x", None, False, "x", rep))  # nothing left to do

    def test_append_system_keeps_the_users_text(self) -> None:
        old = "<!-- llm-deploy:delegation begin -->\nold\n<!-- llm-deploy:delegation end -->\n\nMine.\n"
        new = cfg.append_system_text(old, "rule")
        self.assertEqual(new, "Mine.\n\n<!-- carl:delegation begin -->\nrule\n<!-- carl:delegation end -->\n")
        self.assertEqual(cfg.append_system_text(new, None), "Mine.\n")
        self.assertEqual(cfg.append_system_text("", None), "")

    def test_users_web_search_server_stays(self) -> None:
        rep, st = cfg.Report(), {"web_search": {"url": "ours"}}
        servers: dict[str, Any] = {cfg.SEARCH_NAME: {"url": "mine"}}
        self.assertFalse(cfg.merge_pi_web_search(servers, st, "exa", rep))
        self.assertEqual(servers, {cfg.SEARCH_NAME: {"url": "mine"}})
        self.assertNotIn("web_search", st)

    def test_env_file_only_with_a_switch_on(self) -> None:
        self.assertIsNone(cfg.env_file_text("off", False, False))
        text = cfg.env_file_text("parallel", False, False)
        self.assertIsNotNone(text)
        self.assertIn("export OPENCODE_ENABLE_PARALLEL=1", text or "")

    def test_users_default_model_is_kept_with_a_hint(self) -> None:
        rep, conf, st = cfg.Report(), {"model": "llm-deploy/m"}, {}
        cfg.merge_oc_default_model(conf, st, "m", "carl", False, {"llm-deploy": "carl"}, rep)
        self.assertEqual(conf, {"model": "llm-deploy/m"})
        self.assertEqual(rep.entries["kept"], ["OpenCode model = llm-deploy/m (yours; that provider is now 'carl': "
                                               "pick carl/m)"])


class MemoryFiles:
    """The Files port in memory: the installer on a fake disk."""

    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.dirs: set[str] = set()
        self.writes: list[str] = []

    def _parents(self, path: str) -> None:
        d = os.path.dirname(path)
        while d and d not in self.dirs and d != "/":
            self.dirs.add(d)
            d = os.path.dirname(d)

    def read(self, path: str) -> str | None:
        return self.files.get(path)

    def exists(self, path: str) -> bool:
        return path in self.files or path in self.dirs

    def isfile(self, path: str) -> bool:
        return path in self.files

    def isdir(self, path: str) -> bool:
        return path in self.dirs

    def islink(self, path: str) -> bool:
        return False

    def realpath(self, path: str) -> str:
        return path

    def listdir(self, path: str) -> list[str]:
        return sorted({p[len(path) + 1:].split("/")[0] for p in self.files.keys() | self.dirs
                       if p.startswith(path + "/")})

    def makedirs(self, path: str) -> None:
        self.dirs.add(path)
        self._parents(path)

    def write(self, path: str, text: str) -> None:
        self.files[path] = text
        self._parents(path)
        self.writes.append(path)

    def chmod(self, path: str, mode: int) -> None:
        pass

    def copy(self, src: str, dest: str) -> None:
        self.write(dest, self.files[src])

    def copy_into(self, src: str, folder: str) -> None:
        self.write(os.path.join(folder, os.path.basename(src)), self.files[src])

    def copytree(self, src: str, dest: str) -> None:
        for p in [p for p in self.files if p.startswith(src + "/")]:
            self.write(dest + p[len(src):], self.files[p])

    def remove(self, path: str) -> None:
        del self.files[path]

    def rmdir(self, path: str) -> None:
        self.dirs.discard(path)

    def rmtree(self, path: str, quiet: bool = True) -> None:
        for p in [p for p in self.files if p.startswith(path + "/")]:
            del self.files[p]
        self.dirs -= {d for d in self.dirs if d == path or d.startswith(path + "/")}


class InstallerOnFakeFilesTests(unittest.TestCase):
    """The installer through the Files port only: a fake disk, no real file is read or written."""

    HOME = "/home/u"
    BUNDLE = "/bundle"

    def setUp(self) -> None:
        self.fs = MemoryFiles()
        for root, _, names in os.walk(CLIENT):
            for n in names:
                if n.endswith((".js", ".ts", ".json", ".md")) and n not in ("installed-models.json", "remote.json"):
                    with open(os.path.join(root, n), encoding="utf-8") as f:
                        self.fs.write(self.BUNDLE + os.path.join(root, n)[len(CLIENT):], f.read())
        self.fs.writes.clear()
        self.models = cfg.carl_models.parse_list(MODELS)

    def run_installer(self, **switches: Any) -> Any:
        opts = cfg.Options(bundle=self.BUNDLE, home=self.HOME, host="127.0.0.1", llama_port="8080", ctx=98304,
                           models=self.models, running=None, coder=True, sidebar=True, switcher=True,
                           model_check=True, **switches)
        inst = cfg.Installer(opts, "20260101-000000", self.fs, chrome=False)
        inst.check_configs()
        inst.opencode()
        inst.pi()
        return inst

    def config(self, rel: str) -> Any:
        return json.loads(self.fs.files[os.path.join(self.HOME, rel)])

    def test_fresh_install_then_a_rerun_that_changes_nothing(self) -> None:
        inst = self.run_installer()
        self.assertTrue(inst.report.entries["added"])
        oc = self.config(".config/opencode/opencode.json")
        self.assertEqual(oc["model"], "llamacpp/qwen3.6-35b-a3b")
        self.assertIn("--headless", oc["mcp"]["carl-browser"]["command"])
        self.assertNotIn("chrome", oc["mcp"]["carl-browser"]["command"])
        self.assertIn(self.HOME + "/.config/opencode/plugins/session-switcher/carl-tui.js", self.fs.files)
        self.assertEqual(self.config(".pi/agent/settings.json")["defaultTools"], ["+grep", "+find", "+ls"])
        self.fs.writes.clear()
        again = self.run_installer()
        self.assertEqual((again.report.entries["added"], again.report.entries["updated"]), ([], []))
        self.assertFalse([p for p in self.fs.writes if ".bak." in p])

    def test_all_off_takes_ours_out(self) -> None:
        self.run_installer()
        inst = self.run_installer(web_search="off", lsp=False, background=False, browser=False, cache=False)
        self.assertIn("OpenCode browser (MCP server carl-browser)", inst.report.entries["removed"])
        self.assertNotIn(self.HOME + "/.config/carl/opencode.env", self.fs.files)
        self.assertNotIn("mcp", self.config(".config/opencode/opencode.json"))

    def test_a_bad_config_is_found_before_any_write(self) -> None:
        self.fs.write(self.HOME + "/.pi/agent/mcp.json", "{ // mine\n}")
        self.fs.writes.clear()
        with self.assertRaises(cfg.ConfigError):
            self.run_installer()
        self.assertEqual(self.fs.writes, [])


class InstallScriptTests(unittest.TestCase):
    """client/install.sh in a throw-away home with no server (port closed): the key folder's
    old name is moved, and the configs it writes read the key from the new place."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = self._tmp.name
        # a copied bundle, as in a VM (no ../tools/carl.py): nothing is written into the repo
        self.bundle = os.path.join(self.home, "client")
        # (without this Mac's own server files: remote.json and api-key are written at every server start)
        shutil.copytree(CLIENT, self.bundle, ignore=shutil.ignore_patterns("installed-models.json", "__pycache__",
                                                                           "remote.json", "api-key"))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def install(self) -> subprocess.CompletedProcess[str]:
        with socket.socket() as sock:                       # a port nothing listens on
            sock.bind(("127.0.0.1", 0))
            port = str(sock.getsockname()[1])
        env = {k: v for k, v in os.environ.items() if k not in ("CARL_API_KEY", "LLAMA_CTX", "CODER", "NO_CODER")}
        # never a sync service: launchd / systemd would run it for the real user
        return subprocess.run(["bash", os.path.join(self.bundle, "install.sh"), "--host", "127.0.0.1", "--port", port],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              env={**env, "HOME": self.home, "NO_SYNC_SERVICE": "1"})

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
        self.assertNotIn("Traceback", p.stderr)                  # the server is away: a note, no stack trace
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
