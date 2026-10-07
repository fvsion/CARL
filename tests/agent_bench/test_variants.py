"""The Phase 23 variants (tools/agent-bench/variants/): each changes a harness HOME the way it says, keeps what is not
its business, and gives the same result when it runs again on its own output."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest

import _support  # noqa: F401  (puts tools/agent-bench on sys.path)
from agentbench import variant, varlib


def read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def make_home(root: str, background: bool = True, browser: bool = True) -> str:
    """A HOME with CARL's coder and rule as client/configure.py writes them (the parts the variants touch)."""
    cf = varlib.configure()
    p = varlib.Paths.of(root)
    src = varlib.REPO
    rule = read(os.path.join(src, "client", "agents", "delegation.md"))
    coder = read(os.path.join(src, "client", "agents", "coder.md"))
    body, desc = cf.split_agent(coder)
    os.makedirs(os.path.dirname(p.oc_rule))
    os.makedirs(p.pi_agents)
    with open(p.oc_rule, "w") as f:
        f.write(cf.oc_rule_text(cf.delegation_for(rule, "opencode", background, browser)))
    with open(p.oc_coder, "w") as f:
        f.write(body)
    with open(p.oc_json, "w") as f:
        json.dump({"instructions": [p.oc_rule, "/elsewhere/AGENTS.md"],
                   "agent": {"coder": {"description": desc, "prompt": "{file:" + p.oc_coder + "}", "mode": "subagent"}}},
                  f)
    with open(p.pi_append, "w") as f:
        f.write(cf.append_system_text("The user's own text.", cf.delegation_for(rule, "pi", background, browser).strip()))
    with open(os.path.join(p.pi_agents, "coder.md"), "w") as f:
        f.write(coder)
    return root


class VariantsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = make_home(self.tmp.name)
        self.p = varlib.Paths.of(self.home)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_listed(self) -> None:
        for name in ("baseline", "v1_short_rule", "v2_turn_reminder", "v4_read_nudge", "v5_new_file_gate",
                     "v7_coder_modes", "c1_v2_v5_v7", "c2_v2_v7", "c3_v2_v5n2_v7", "project_in_system"):
            self.assertIn(name, variant.available())
            self.assertTrue(variant.load(name).description)

    def test_v1_short_rule_last(self) -> None:
        v = variant.load("v1_short_rule")
        self.assertTrue(v.apply(self.home))
        oc, pi = read(self.p.oc_rule), read(self.p.pi_append)
        for t in (oc, pi):
            self.assertIn("Coding work goes to `coder`", t)
            self.assertIn("background: true", t)                       # the switch as installed
            self.assertNotIn("<!-- carl:", t.replace("<!-- carl:delegation", "").replace("<!-- carl:main-agents-only", ""))
        self.assertTrue(oc.startswith(varlib.configure().RULE_BEGIN))       # carl-delegation keeps it to main agents
        self.assertIn('subagent_type "browser"', oc)
        self.assertIn("tool_search", pi)
        self.assertNotIn("Delegating to the coder subagent", pi)      # the old rule is replaced, not added
        self.assertTrue(pi.startswith("The user's own text."))
        self.assertEqual(json.loads(read(self.p.oc_json))["instructions"][-1], self.p.oc_rule)
        self.assertEqual(v.apply(self.home), [])                       # again on its own output: no change

    def test_v1_keeps_switches_off(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = make_home(d, background=False, browser=False)
            variant.load("v1_short_rule").apply(home)
            oc = read(varlib.Paths.of(home).oc_rule)
            self.assertNotIn("background: true", oc)
            self.assertIn("to check by hand", oc)

    def test_v7_coder_modes(self) -> None:
        v = variant.load("v7_coder_modes")
        self.assertTrue(v.apply(self.home))
        self.assertIn("## Your mode", read(self.p.oc_coder))
        self.assertIn("Mode: test", json.loads(read(self.p.oc_json))["agent"]["coder"]["description"])
        pi_agent = read(os.path.join(self.p.pi_agents, "coder.md"))
        self.assertTrue(pi_agent.startswith("---\nname: coder"))
        self.assertIn("## Your mode", pi_agent)
        for t in (read(self.p.oc_rule), read(self.p.pi_append)):
            self.assertIn("The coder's two modes", t)
            self.assertIn("Delegating to the coder subagent", t)       # the baseline rule, plus the modes
        self.assertEqual(v.apply(self.home), [])

    def test_hooks_installed_in_both_clients(self) -> None:
        for name, mode in (("v4_read_nudge", "nudge"), ("v5_new_file_gate", "gate"), ("v2_turn_reminder", "remind")):
            with tempfile.TemporaryDirectory() as d:
                home = make_home(d)
                p = varlib.Paths.of(home)
                v = variant.load(name)
                self.assertTrue(v.apply(home))
                oc = os.path.join(os.path.dirname(p.oc_json), "plugins", varlib.HOOKS)
                pi = os.path.join(os.path.dirname(p.pi_append), "extensions", varlib.HOOKS)
                self.assertIn(f'"{mode}"', read(os.path.join(oc, "index.js")))
                self.assertIn(f'MODE: string = "{mode}"', read(os.path.join(pi, "index.ts")))
                for f in (os.path.join(oc, "delegate-hooks.js"), os.path.join(pi, "delegate-hooks.js")):
                    self.assertIn("GATE_MARK", read(f))
                plugins = json.loads(read(p.oc_json))["plugin"]
                self.assertEqual(plugins, [["file:" + oc, {}]])
                self.assertEqual(v.apply(home), [])                     # again: no change, one entry

    def test_no_hooks_left_for_the_next_variant(self) -> None:
        """CARL's setup keeps plugins it does not own: a variant without hooks takes them out (2026-10-06)."""
        oc = os.path.join(os.path.dirname(self.p.oc_json), "plugins", varlib.HOOKS)
        pi = os.path.join(os.path.dirname(self.p.pi_append), "extensions", varlib.HOOKS)
        for after in ("baseline", "v1_short_rule", "v7_coder_modes"):
            variant.load("v5_new_file_gate").apply(self.home)
            self.assertTrue(os.path.isdir(oc) and os.path.isdir(pi))
            variant.load(after).apply(self.home)
            self.assertFalse(os.path.exists(oc) or os.path.exists(pi), after)
            self.assertNotIn(varlib.HOOKS, read(self.p.oc_json), after)
        self.assertEqual(variant.load("baseline").apply(self.home), [])

    def test_combined_variant(self) -> None:
        v = variant.load("c1_v2_v5_v7")
        v.apply(self.home)
        oc = os.path.join(os.path.dirname(self.p.oc_json), "plugins", varlib.HOOKS, "index.js")
        self.assertIn('"remind,gate"', read(oc))
        self.assertIn("## Your mode", read(self.p.oc_coder))
        self.assertEqual(v.apply(self.home), [])
        variant.load("c3_v2_v5n2_v7").apply(self.home)
        self.assertIn('"remind,gate:2"', read(oc))
        with self.assertRaises(ValueError):
            varlib.install_hooks(self.home, "gate:x")

    def test_project_in_system_and_back(self) -> None:
        cfg = varlib.carl_config(self.home)
        variant.load("project_in_system").apply(self.home)
        self.assertFalse(json.loads(read(cfg))["cache"]["prefix"])
        self.assertEqual(variant.load("project_in_system").apply(self.home), [])
        variant.load("baseline").apply(self.home)                       # the next variant: CARL's default again
        self.assertFalse(os.path.exists(cfg))
        os.makedirs(os.path.dirname(cfg), exist_ok=True)
        with open(cfg, "w") as f:
            json.dump({"cache": {"disk_gb": 5}}, f)                     # a file that is not the harness's
        with self.assertRaises(RuntimeError):
            variant.load("project_in_system").apply(self.home)
        self.assertEqual(variant.load("baseline").apply(self.home), [])  # and it is never removed

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_hook_rules_js(self) -> None:
        r = subprocess.run(["node", "--test", os.path.join(os.path.dirname(__file__), "delegate-hooks.test.mjs")],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])

    def test_carl_coder_name(self) -> None:
        cfg = json.loads(read(self.p.oc_json))
        cfg["agent"] = {"coder": {"prompt": "mine"}, "carl-coder": cfg["agent"]["coder"]}
        with open(self.p.oc_json, "w") as f:
            json.dump(cfg, f)
        self.assertEqual(varlib.coder_name(self.home), "carl-coder")
        os.rename(os.path.join(self.p.pi_agents, "coder.md"), os.path.join(self.p.pi_agents, "carl-coder.md"))
        variant.load("v7_coder_modes").apply(self.home)
        self.assertIn("`carl-coder`", read(self.p.oc_rule))
        self.assertTrue(os.path.isfile(os.path.join(self.p.pi_agents, "carl-coder.md")))


if __name__ == "__main__":
    unittest.main()
