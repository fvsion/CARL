"""The pre-read prompt cache's pure parts (tools/monitor/prefix.py): the spec, the model name
in the system prompt, the cut at the environment block, the file name and its staleness."""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest

import mon_support  # noqa: F401  (puts tools/ on sys.path)
from monitor import prefix

SYSTEM = ("You are powered by the model named m1. The exact model ID is llamacpp/m1"
          + prefix.ENV_MARKER + "\n<env>\n  Working directory: /a\n</env>\nInstructions from: x")
SPEC = {"schema": 1, "provider": "llamacpp", "model": "m1", "system": SYSTEM,
        "tools": [{"type": "function", "function": {"name": "read", "description": "Read", "parameters": {}}}]}


class PrefixTest(unittest.TestCase):
    def test_load_spec_own_then_newest(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(prefix.load_spec(d, "m1"))
            with open(os.path.join(d, "other.json"), "w", encoding="utf-8") as f:
                json.dump(dict(SPEC, model="other"), f)
            self.assertEqual(prefix.load_spec(d, "m1")["model"], "other")      # type: ignore[index]
            time.sleep(0.01)
            with open(os.path.join(d, "m1.json"), "w", encoding="utf-8") as f:
                json.dump(SPEC, f)
            with open(os.path.join(d, "bad.json"), "w", encoding="utf-8") as f:
                f.write("{nope")
            self.assertEqual(prefix.load_spec(d, "m1")["model"], "m1")         # type: ignore[index]

    def test_the_model_name_follows_the_loaded_model(self) -> None:
        system, tools = prefix.with_model(SPEC, "m2")
        self.assertIn("named m2. The exact model ID is llamacpp/m2", system)
        self.assertEqual(len(tools), 1)
        body = prefix.template_body(system, tools, "m2")
        self.assertEqual((body["model"], body["messages"][0]["content"]), ("m2", system))
        self.assertNotIn("model", prefix.template_body(system, tools, None))

    def test_cut_at_the_environment_block(self) -> None:
        rendered = "<|im_start|>system\\n# Tools ...\\n" + SYSTEM + "<|im_end|>"
        cut = prefix.cut_text(rendered, SYSTEM)
        self.assertTrue(cut.endswith("llamacpp/m1"))
        self.assertNotIn("<env>", cut)
        no_env = "PRE " + "plain system" + " POST"
        self.assertEqual(prefix.cut_text(no_env, "plain system"), "PRE plain system")
        self.assertEqual(prefix.cut_text("unrelated", "plain system"), "")
        self.assertEqual(prefix.common_prefix([1, 2, 3, 4], [1, 2, 9]), 2)

    def test_an_idle_slot_empty_first_else_the_one_holding_least(self) -> None:
        self.assertEqual(prefix.choose_slot([(0, False, 9000), (1, False, 0)]), (1, False))
        self.assertEqual(prefix.choose_slot([(0, False, 9000), (1, False, 300)]), (1, True))    # pushed to the cache
        self.assertEqual(prefix.choose_slot([(0, True, 0), (1, False, 50)]), (1, True))
        self.assertIsNone(prefix.choose_slot([(0, True, 0)]))

    def test_file_names_change_with_what_the_state_depends_on(self) -> None:
        a = prefix.slot_file("m1", "/nofile", "q4_0", "b1", [1, 2, 3])
        self.assertTrue(a.startswith("carl-prefix-m1-") and a.endswith(".bin"))
        for other in (prefix.slot_file("m1", "/nofile", "q8_0", "b1", [1, 2, 3]),     # KV type
                      prefix.slot_file("m1", "/nofile", "q4_0", "b2", [1, 2, 3]),     # llama.cpp build
                      prefix.slot_file("m1", "/nofile", "q4_0", "b1", [1, 2, 4])):    # the prefix (a new tool)
            self.assertNotEqual(a, other)
        with tempfile.TemporaryDirectory() as d:
            for n in (a, "carl-prefix-m1-0000000000000000.bin", "carl-prefix-m10-1111111111111111.bin"):
                open(os.path.join(d, n), "w").close()
            self.assertEqual([os.path.basename(x) for x in prefix.stale_files(d, a)],
                             ["carl-prefix-m1-0000000000000000.bin"])


if __name__ == "__main__":
    unittest.main()
