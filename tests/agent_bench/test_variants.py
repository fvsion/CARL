"""The Phase 23 variants (tools/agent-bench/variants/): each changes a harness HOME the way it says, keeps what is not
its business, and gives the same result when it runs again on its own output."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest

import _support as sp  # (puts tools/agent-bench on sys.path)
from agentbench import variant, varlib


def read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def make_home(root: str, background: bool = True, browser: bool = True) -> str:
    """A HOME with CARL's coder, rule and hand-off switches as client/configure.py writes them (_support)."""
    return sp.make_variant_home(root, background, browser)


class VariantsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = make_home(self.tmp.name)
        self.p = varlib.Paths.of(self.home)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_listed(self) -> None:
        for name in ("baseline", "v1_short_rule", "v2_turn_reminder", "v4_read_nudge", "v5_new_file_gate",
                     "v7_coder_modes", "c1_v2_v5_v7", "c2_v2_v7", "c3_v2_v5n2_v7", "project_in_system", "brief_kv",
                     "brief_json"):
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
                self.assertEqual(plugins[-1], ["file:" + oc, {}])               # CARL's own plugins stay before it
                self.assertEqual(sum(1 for x in plugins if x[0] == "file:" + oc), 1)
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


def git_show(spec: str) -> str:
    """A file of a tag (git show), or "" when git or the tag is not there."""
    try:
        p = subprocess.run(["git", "-C", varlib.REPO, "show", spec], capture_output=True, text=True, timeout=30)
    except OSError:
        return ""
    return p.stdout if p.returncode == 0 else ""


class BriefVariantsTest(unittest.TestCase):
    """Phase 23.4.3's measurement of the brief's format: brief_kv (1.12.1's texts, the check, gates and chain off),
    brief_json (the same structure as JSON), and the baseline (CARL's TOML) again after either."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = make_home(self.tmp.name)
        self.p = varlib.Paths.of(self.home)

    def options(self) -> dict:  # type: ignore[type-arg]
        entry = [x for x in json.loads(read(self.p.oc_json))["plugin"] if x[0].endswith("/carl-delegation")]
        self.assertEqual(len(entry), 1)
        return dict(entry[0][1])

    def pi_delegation(self) -> dict:  # type: ignore[type-arg]
        return dict(json.loads(read(varlib.pi_carl_json(self.home)))["delegation"])

    def assert_defaults(self) -> None:
        self.assertEqual(self.options(), {"provider": "llamacpp", "reminder": True, "cacheApi": "http://127.0.0.1:8098",
                                          "coder": "coder"})
        self.assertEqual(self.pi_delegation(), {"reminder": True})
        ts = read(varlib.pi_subagent_ts(self.home))
        self.assertIn("a TOML brief as its task", ts)
        self.assertEqual(ts, read(os.path.join(varlib.REPO, "client", "pi", "extensions", "subagent", "index.ts")))

    def test_texts(self) -> None:
        for fmt in varlib.BRIEF_FORMATS[1:]:
            for part in ("delegation", "coder"):
                t = varlib.text(f"brief_{fmt}_{part}.md")
                self.assertNotIn("TOML", t, (fmt, part))
        old = git_show("v1.12.1:client/agents/delegation.md")
        if old:                                                             # the copies are 1.12.1's own files
            self.assertEqual(varlib.text("brief_kv_delegation.md"), old)
            self.assertEqual(varlib.text("brief_kv_coder.md"), git_show("v1.12.1:client/agents/coder.md"))
            sub = git_show("v1.12.1:client/pi/extensions/subagent/index.ts")
            for line in varlib.PI_GUIDELINES["kv"]:
                self.assertIn(line, sub)
        src = read(os.path.join(varlib.REPO, "client", "pi", "extensions", "subagent", "index.ts"))
        for line in varlib.PI_GUIDELINES["toml"]:                          # CARL's own lines, as the source has them
            self.assertIn(line, src)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_json_texts_hold_a_valid_brief_and_report(self) -> None:
        from agentbench import briefs
        rule = varlib.text("brief_json_delegation.md")
        example = rule[rule.index("```json"):]
        toml_rule = read(os.path.join(varlib.REPO, "client", "agents", "delegation.md"))
        out = briefs.check_texts([example, toml_rule[toml_rule.index("```toml"):]])
        self.assertEqual([(o["format"], o["valid"]) for o in out], [("json", True), ("toml", True)])
        script = ("import * as B from %s; const [a, b] = JSON.parse(process.argv[1]);"
                  "process.stdout.write(JSON.stringify([B.parseBrief(a).brief, B.parseBrief(b).brief, "
                  "B.parseReport(process.argv[2]), B.parseReport(process.argv[3])]));"
                  % json.dumps(os.path.join(varlib.REPO, "client", "shared", "carl-brief.js")))
        coder = varlib.text("brief_json_coder.md")
        toml_coder = read(os.path.join(varlib.REPO, "client", "agents", "coder.md"))
        p = subprocess.run(["node", "--input-type=module", "-e", script, "--",
                            json.dumps([example, toml_rule[toml_rule.index("```toml"):]]),
                            coder[coder.index("```json"):], toml_coder[toml_coder.index("```toml"):]],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        json_brief, toml_brief, json_report, toml_report = json.loads(p.stdout)
        self.assertEqual(json_brief, toml_brief)                           # the example converted faithfully
        self.assertEqual(json_report, toml_report)                         # the report's example too

    def test_brief_kv(self) -> None:
        v = variant.load("brief_kv")
        changed = v.apply(self.home)
        for f in (self.p.oc_rule, self.p.oc_coder, self.p.oc_json, self.p.pi_append, varlib.pi_carl_json(self.home),
                  varlib.pi_subagent_ts(self.home)):
            self.assertIn(f, changed)
        for t in (read(self.p.oc_rule), read(self.p.pi_append)):
            self.assertIn("Mode: code | test", t)                           # 1.12.1's task form
            self.assertNotIn("TOML", t)
            self.assertNotIn("<!-- carl:background", t)                     # the markers cut as for CARL's rule
            self.assertIn("background: true", t)
        self.assertIn('subagent_type "browser"', read(self.p.oc_rule))
        self.assertIn("Start every task with the line `Mode: code`",
                      json.loads(read(self.p.oc_json))["agent"]["coder"]["description"])
        self.assertNotIn("TOML", read(os.path.join(self.p.pi_agents, "coder.md")))
        self.assertEqual(self.options(), {"provider": "llamacpp", "reminder": True, "cacheApi": "http://127.0.0.1:8098",
                                          "coder": "coder", "brief": False, "chain": False})
        self.assertEqual(self.pi_delegation(), {"reminder": True, "brief": False, "chain": False})
        ts = read(varlib.pi_subagent_ts(self.home))
        self.assertNotIn("a TOML brief as its task", ts)
        self.assertIn('and a self-contained task. Do not start writing it yourself.', ts)
        self.assertEqual(v.apply(self.home), [])                            # again: no change
        variant.load("baseline").apply(self.home)                          # the next variant: CARL's switches again
        self.assert_defaults()

    def test_brief_json(self) -> None:
        v = variant.load("brief_json")
        v.apply(self.home)
        for t in (read(self.p.oc_rule), read(self.p.pi_append)):
            self.assertIn("is a brief in JSON", t)
            self.assertIn('"requirement": [', t)
        self.assertIn("Your task is a brief in JSON.", read(self.p.oc_coder))
        self.assertIn("Its task is a JSON brief", json.loads(read(self.p.oc_json))["agent"]["coder"]["description"])
        opts = self.options()
        self.assertEqual(opts["briefFormat"], "json")
        self.assertNotIn("brief", opts)                                     # the check and the chain stay on
        self.assertNotIn("chain", opts)
        self.assertEqual(self.pi_delegation(), {"reminder": True, "brief_format": "json"})
        self.assertIn("a JSON brief as its task", read(varlib.pi_subagent_ts(self.home)))
        self.assertEqual(v.apply(self.home), [])
        variant.load("brief_kv").apply(self.home)                          # one variant after the other
        self.assertNotIn("briefFormat", self.options())
        variant.load("baseline").apply(self.home)
        self.assert_defaults()
        self.assertEqual(variant.load("baseline").apply(self.home), [])

    def test_needs_carl_delegation(self) -> None:
        cfg = json.loads(read(self.p.oc_json))
        cfg["plugin"] = [x for x in cfg["plugin"] if not x[0].endswith("/carl-delegation")]
        with open(self.p.oc_json, "w") as f:
            json.dump(cfg, f)
        with self.assertRaises(RuntimeError):
            variant.load("brief_kv").apply(self.home)
        self.assertEqual(variant.load("baseline").apply(self.home), [])     # the reset needs nothing

    def test_carl_coder_name(self) -> None:
        cfg = json.loads(read(self.p.oc_json))
        cfg["agent"] = {"coder": {"prompt": "mine"}, "carl-coder": cfg["agent"]["coder"]}
        cfg["plugin"][-1][1]["coder"] = "carl-coder"
        with open(self.p.oc_json, "w") as f:
            json.dump(cfg, f)
        os.rename(os.path.join(self.p.pi_agents, "coder.md"), os.path.join(self.p.pi_agents, "carl-coder.md"))
        variant.load("brief_json").apply(self.home)
        self.assertIn("`carl-coder`", read(self.p.oc_rule))
        self.assertIn("name: carl-coder", read(os.path.join(self.p.pi_agents, "carl-coder.md")))


if __name__ == "__main__":
    unittest.main()
