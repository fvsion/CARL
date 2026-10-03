"""Client setup in the Connect tab: the pasted provider block's id and its entries (one per
installed model, as install.sh writes them), which clients install.sh already pointed at
this server (its state files, also under their name from before the rename), and whether
their model lists are out of date."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True                                  # keep tools/ free of __pycache__
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools"))

from monitor import fsio
from monitor.clients import Served, drift, model_list, opencode_config, pi_config

BASE = "http://127.0.0.1:8080"


INSTALLED = {"schema": 1, "default": "moe", "models": [
    {"id": "moe", "label": "MoE · IQ3", "ctx": 98304, "thinking": "on-off"},
    {"id": "dense", "label": "Dense · Q4", "ctx": 131072, "thinking": "effort"}]}


class SnippetTest(unittest.TestCase):
    def test_pasted_blocks_use_the_carl_id(self) -> None:
        s = Served("qwen", 98304)
        ml = model_list(None, s)                                       # no list: the served model alone
        oc = opencode_config(s, {}, BASE, "{file:/k}", ml)
        self.assertEqual(list(oc["provider"]), ["carl"])
        self.assertEqual(oc["provider"]["carl"]["name"], "llamacpp [carl]")
        self.assertEqual(list(oc["provider"]["carl"]["models"]), ["qwen"])
        self.assertEqual(list(pi_config(s, {}, BASE, "k", ml)["providers"]), ["carl"])

    def test_every_installed_model_with_the_running_window(self) -> None:
        s = Served("dense", 65536)
        ml = model_list(INSTALLED, s)
        models = opencode_config(s, {}, BASE, "k", ml)["provider"]["carl"]["models"]
        self.assertEqual(list(models), ["moe", "dense"])
        self.assertEqual((models["moe"]["limit"]["context"], models["dense"]["limit"]["context"]), (98304, 65536))
        self.assertEqual([m["id"] for m in pi_config(s, {}, BASE, "k", ml)["providers"]["carl"]["models"]], ["moe", "dense"])
        self.assertEqual(list(opencode_config(s, {}, BASE, "k", model_list({"schema": 9}, s))["provider"]["carl"]["models"]),
                         ["dense"])                                    # a bad list: the served model alone


class DriftTest(unittest.TestCase):
    def test_out_of_date_lists(self) -> None:
        out = drift({"OpenCode": {"a": 98304, "gone": 98304}, "Pi": {"a": 98304, "b": 0}, "broken": None}, ["a", "b"])
        self.assertEqual([(d.client, d.added, d.removed) for d in out], [("OpenCode", ["b"], ["gone"])])
        self.assertEqual(out[0].line(2), "OpenCode lists 2 models; installed now: 2 — added b; removed gone")

    def test_the_running_window_changed(self) -> None:
        listed = {"OpenCode": {"a": 98304}, "Pi": {"a": 131072}}
        out = drift(listed, ["a"], running=("a", 131072))                  # the server now runs 128K per slot
        self.assertEqual([d.client for d in out], ["OpenCode"])
        self.assertEqual(out[0].line(1), "OpenCode window of a: 96K in the config, 128K on the server")
        self.assertEqual(drift(listed, ["a"], running=("other", 4096)), [])

    def test_listed_models_reads_our_provider(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, ".config/opencode"))
            os.makedirs(os.path.join(home, ".pi/agent"))
            with open(os.path.join(home, ".config/opencode/opencode.json"), "w", encoding="utf-8") as f:
                json.dump({"provider": {"carl": {"models": {"a": {}, "b": {}}}, "mine": {"models": {"x": {}}}}}, f)
            with open(os.path.join(home, ".pi/agent/models.json"), "w", encoding="utf-8") as f:
                json.dump({"providers": {"llamacpp": {"models": [{"id": "a"}]}}}, f)
            self.assertEqual(fsio.listed_models(home, "OpenCode", "carl"), {"a": 0, "b": 0})
            self.assertEqual(fsio.listed_models(home, "Pi", "llamacpp"), {"a": 0})
            self.assertIsNone(fsio.listed_models(home, "Pi", "nope"))


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
