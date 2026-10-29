"""./carl.sh card: show a model's card, set and unset fields of a custom model's card, refuse
catalogue models; run through host/serve.sh with a throw-away home, settings folder and
models folder (one small file in it is the custom model)."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest

from _paths import REPO


class CardCli(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        d = self._tmp.name
        self.conf, self.models = os.path.join(d, "conf"), os.path.join(d, "models")
        os.makedirs(self.models)
        with open(os.path.join(self.models, "Mine-Q4.gguf"), "wb") as f:
            f.write(b"GGUF")
        self.env = {**{k: v for k, v in os.environ.items() if k != "API_KEY_FILE"}, "HOME": d,
                    "CARL_CONF_DIR": self.conf, "MODELS_DIR": self.models, "CARL_CMD": "./carl.sh", "COLUMNS": "80"}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def carl(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([os.path.join(REPO, "carl.sh"), *args], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, env=self.env)

    def stored(self) -> object:
        with open(os.path.join(self.conf, "models.json"), encoding="utf-8") as f:
            return json.load(f)["models"]["mine-q4"].get("card")

    def test_set_show_unset(self) -> None:
        p = self.carl("card", "mine-q4")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("mine-q4 is a custom model. It has no card yet.", p.stdout)
        for args in (("role", "Fast", "local", "coder"), ("good_for", "agent coding,hard code"), ("rank", "4"),
                     ("arch", "MoE"), ("pick_instead", "qwen3.8-27b=harder code", "qwen3.6-35b-a3b=chat"),
                     ("auto_fit", "yes")):
            p = self.carl("card", "mine-q4", "set", *args)
            self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.stored(), {
            "role": "Fast local coder", "good_for": ["agent coding", "hard code"], "rank": 4, "arch": "moe",
            "pick_instead": [{"model": "qwen3.8-27b", "when": "harder code"},
                             {"model": "qwen3.6-35b-a3b", "when": "chat"}], "auto_fit": True})
        self.assertRegex(p.stdout, r"\n  good for \(good_for\) +agent coding, hard code\n")
        listing = self.carl("models").stdout
        self.assertRegex(listing, r"mine-q4 .*\n +Fast local coder \(your card\)")
        p = self.carl("card", "mine-q4", "unset", "auto_fit")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("auto_fit", self.stored() or {})

    def test_errors_exit_1(self) -> None:
        for args, msg in ((("qwen3.8-27b", "set", "role", "x"), "qwen3.8-27b is a catalogue model, so you cannot change its card"),
                          (("mine-q4", "set", "good_for", "uncensored"), "only an abliterated model can have the tag uncensored"),
                          (("mine-q4", "set", "rank", "0"), "rank must be a whole number, 1 or more"),
                          (("mine-q4", "set", "colour", "red"), "unknown card field 'colour'"),
                          (("mine-q4", "set", "role"), "usage: carl.py card NAME"),
                          (("nope",), "unknown model 'nope'")):
            p = self.carl("card", *args)
            self.assertEqual(p.returncode, 1, args)
            self.assertTrue(p.stderr.startswith("error: "), p.stderr)
            self.assertIn(msg, p.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.conf, "models.json")))   # nothing written

    def test_catalogue_card_and_help(self) -> None:
        p = self.carl("card", "qwen3.8-27b")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("qwen3.8-27b is a catalogue model. You cannot change its card", p.stdout)
        self.assertRegex(p.stdout, r"\n  quality rank +1\n")
        self.assertIn("card NAME", self.carl("-h").stdout)
        self.assertIn("FIELDS", self.carl("help", "card").stdout)
        self.assertIn("card NAME set FIELD VALUE", self.carl("card", "--help").stdout)


if __name__ == "__main__":
    unittest.main()
