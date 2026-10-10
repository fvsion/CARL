"""client/carl-sync.py set: the /carl panel's switches. It changes ~/.config/carl/client-install.env (the other
lines stay; mode 0600), runs the installer next to it (here a fake that records its environment) without a new
config from the dashboard, refuses unknown keys and values (exit code 2, nothing changed), says which client must
restart, and waits for a sync that holds the lock. A copied client folder with its own HOME."""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest

CLIENT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "client")

# the fake installer: its environment (one KEY=value per line) and a count of its runs; it fails when HOME/fail is there;
# the server's slot count it "read" from HOME/slots goes to CARL_SLOTS_OUT, as install.sh writes it
FAKE_INSTALL = """env > "$HOME/installer.env"
echo run >> "$HOME/installs"
if [ -f "$HOME/slots" ] && [ -n "$CARL_SLOTS_OUT" ]; then cp "$HOME/slots" "$CARL_SLOTS_OUT"; fi
[ -f "$HOME/fail" ] && exit 3
exit 0
"""


class SetTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.home = os.path.join(self.tmp, "home")
        self.bundle = os.path.join(self.tmp, "client")
        self.conf = os.path.join(self.home, ".config", "carl")
        self.env_path = os.path.join(self.conf, "client-install.env")
        os.makedirs(self.bundle)
        os.makedirs(self.conf)
        shutil.copy(os.path.join(CLIENT, "carl-sync.py"), self.bundle)
        with open(os.path.join(self.bundle, "install.sh"), "w") as f:
            f.write(FAKE_INSTALL)
        # a server address that answers nothing: set must not ask the dashboard for a config
        with open(os.path.join(self.bundle, "remote.json"), "w") as f:
            json.dump({"host": "127.0.0.1", "port": 9, "cache_api": "http://127.0.0.1:9"}, f)
        with open(os.path.join(self.bundle, "installed-models.json"), "w") as f:
            f.write('{"schema": 1, "models": [{"id": "m"}]}')
        self.write_env("CLIENTS=both\nWEB_SEARCH=exa\nNO_SIDEBAR=1\nLLAMA_CTX=96k\nDELEGATION_GATE=3\n# a comment\n")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp)

    def write_env(self, text: str) -> None:
        with open(self.env_path, "w") as f:
            f.write(text)

    def env_lines(self) -> list:
        with open(self.env_path) as f:
            return f.read().splitlines()

    def run_set(self, *args: str, extra: dict | None = None) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k not in ("NO_PROFILE", "CARL_SYNC")}
        return subprocess.run([sys.executable, os.path.join(self.bundle, "carl-sync.py"), "set", *args],
                              capture_output=True, text=True, timeout=30, env={**env, "HOME": self.home, **(extra or {})})

    def installer_env(self) -> dict:
        with open(os.path.join(self.home, "installer.env")) as f:
            return dict(line.split("=", 1) for line in f.read().splitlines() if "=" in line)

    def runs(self) -> int:
        try:
            with open(os.path.join(self.home, "installs")) as f:
                return f.read().split().count("run")
        except OSError:
            return 0

    def test_set_changes_the_env_file_and_keeps_the_other_lines(self) -> None:
        p = self.run_set("NO_SIDEBAR=on", "NO_CODER=1", "WEB_SEARCH=off")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.env_lines(), ["CLIENTS=both", "LLAMA_CTX=96k", "DELEGATION_GATE=3", "# a comment",
                                            "NO_CODER=1", "WEB_SEARCH=off"])
        self.assertEqual(stat.S_IMODE(os.stat(self.env_path).st_mode), 0o600)
        r = json.loads(p.stdout)
        self.assertTrue(r["ok"])
        self.assertIsNone(r["error"])
        self.assertEqual(r["changed"], {"NO_SIDEBAR": {"from": "1", "to": "on"}, "NO_CODER": {"from": "on", "to": "1"},
                                        "WEB_SEARCH": {"from": "exa", "to": "off"}})
        self.assertEqual(r["restart"], ["opencode", "pi"])
        self.assertTrue(r["new_terminal"])                       # web search: OpenCode reads it from the shell
        self.assertEqual(self.runs(), 1)

    def test_the_installer_gets_the_switches_and_carl_sync_but_not_no_profile(self) -> None:
        # a NO_SIDEBAR=1 in the environment must not undo "on"; DELEGATION_GATE is the dashboard's setting now
        p = self.run_set("NO_SIDEBAR=on", "NO_CACHE=1", extra={"NO_SIDEBAR": "1"})
        self.assertEqual(p.returncode, 0, p.stderr)
        env = self.installer_env()
        self.assertEqual(env.get("CARL_SYNC"), "1")
        self.assertNotIn("NO_PROFILE", env)
        self.assertNotIn("NO_SIDEBAR", env)
        self.assertNotIn("DELEGATION_GATE", env)
        self.assertEqual((env.get("NO_CACHE"), env.get("WEB_SEARCH"), env.get("LLAMA_CTX"), env.get("CLIENTS")),
                         ("1", "exa", "96k", "both"))
        self.assertEqual(env.get("HOME"), self.home)

    def test_set_uses_the_models_here_and_asks_the_dashboard_nothing(self) -> None:
        before = os.stat(os.path.join(self.bundle, "installed-models.json")).st_mtime_ns
        p = self.run_set("NO_BROWSER=1")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(os.stat(os.path.join(self.bundle, "installed-models.json")).st_mtime_ns, before)
        self.assertFalse(os.path.exists(os.path.join(self.conf, "client-sync.json")))   # no check, no error noted
        with open(os.path.join(self.conf, "client-sync.log")) as f:
            self.assertIn(": set NO_BROWSER=1", f.readline())

    def test_unknown_keys_and_values_are_refused_and_nothing_changes(self) -> None:
        before = self.env_lines()
        for args in ([], ["FOO=1"], ["NO_CODER=0"], ["NO_CODER=off"], ["WEB_SEARCH=bing"], ["WEB_SEARCH=on"],
                     ["DELEGATION_GATE=2"], ["CODER=0"], ["NO_CODER"], ["NO_CACHE=1", "NO_PROFILE=1"]):
            p = self.run_set(*args)
            self.assertEqual(p.returncode, 2, args)
            self.assertTrue(p.stderr.startswith("error: "), p.stderr)
            self.assertEqual(p.stdout, "")
        self.assertIn("WEB_SEARCH takes exa or parallel or off, not 'bing'.", self.run_set("WEB_SEARCH=bing").stderr)
        self.assertIn("set does not change 'DELEGATION_GATE'", self.run_set("DELEGATION_GATE=2").stderr)
        self.assertEqual(self.env_lines(), before)
        self.assertEqual(self.runs(), 0)

    def test_restart_names_only_the_clients_that_have_the_part(self) -> None:
        r = json.loads(self.run_set("NO_LSP=1").stdout)
        self.assertEqual((r["restart"], r["new_terminal"]), (["opencode"], True))
        r = json.loads(self.run_set("NO_SIDEBAR=1").stdout)
        self.assertEqual((r["restart"], r["new_terminal"], r["changed"]), (["opencode"], False, {}))  # it was 1
        self.write_env("CLIENTS=pi\n")
        self.assertEqual(json.loads(self.run_set("NO_SWITCHER=1").stdout)["restart"], [])
        self.assertEqual(json.loads(self.run_set("NO_CACHE=1").stdout)["restart"], ["pi"])

    def test_coder_thinking_is_kept_per_model(self) -> None:
        """/carl's Coder thinking (Phase 23.4.4): CODER_THINKING=MODEL:VALUE changes that model's value in one line of
        the env file (the other models keep theirs); the installer gets the whole line; only OpenCode restarts (Pi
        reads it each time it starts the coder)."""
        p = self.run_set("CODER_THINKING=qwen3.6-35b-a3b-iq3:off")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("CODER_THINKING=qwen3.6-35b-a3b-iq3:off", self.env_lines())
        r = json.loads(p.stdout)
        self.assertEqual(r["changed"], {"CODER_THINKING": {"from": "", "to": "qwen3.6-35b-a3b-iq3:off"}})
        self.assertEqual((r["restart"], r["new_terminal"]), (["opencode"], False))
        self.assertEqual(self.installer_env()["CODER_THINKING"], "qwen3.6-35b-a3b-iq3:off")
        self.run_set("CODER_THINKING=gemma-4-e4b:on")
        self.assertIn("CODER_THINKING=gemma-4-e4b:on,qwen3.6-35b-a3b-iq3:off", self.env_lines())
        self.run_set("CODER_THINKING=qwen3.6-35b-a3b-iq3:main", "CODER_THINKING=gemma-4-e4b:xhigh")
        self.assertIn("CODER_THINKING=gemma-4-e4b:xhigh,qwen3.6-35b-a3b-iq3:main", self.env_lines())
        self.assertEqual(sum(ln.startswith("CODER_THINKING=") for ln in self.env_lines()), 1)
        self.assertIn("CLIENTS=both", self.env_lines())                    # the other lines stay
        self.write_env("CLIENTS=pi\n")
        self.assertEqual(json.loads(self.run_set("CODER_THINKING=m:low").stdout)["restart"], [])
        before = self.env_lines()
        for bad in ("CODER_THINKING=m", "CODER_THINKING=m:high", "CODER_THINKING=:off", "CODER_THINKING=a b:off",
                    "CODER_THINKING=m:off,n:on"):
            p = self.run_set(bad)
            self.assertEqual(p.returncode, 2, bad)
            self.assertIn("CODER_THINKING takes MODEL:VALUE", p.stderr)
        self.assertEqual(self.env_lines(), before)

    def test_coder_thinking_default_removes_the_models_value(self) -> None:
        """/carl's "dashboard default": CODER_THINKING=MODEL:default takes that model's entry out (the dashboard's value
        applies again); the other models keep theirs; with none left, the line goes and the installer gets no value."""
        self.run_set("CODER_THINKING=a:off", "CODER_THINKING=b:xhigh")
        self.assertIn("CODER_THINKING=a:off,b:xhigh", self.env_lines())
        p = self.run_set("CODER_THINKING=a:default")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("CODER_THINKING=b:xhigh", self.env_lines())
        r = json.loads(p.stdout)
        self.assertEqual(r["changed"], {"CODER_THINKING": {"from": "a:off,b:xhigh", "to": "b:xhigh"}})
        self.assertEqual(self.installer_env()["CODER_THINKING"], "b:xhigh")
        self.run_set("CODER_THINKING=c:default")                            # a model with no value: nothing changes
        self.assertIn("CODER_THINKING=b:xhigh", self.env_lines())
        p = self.run_set("CODER_THINKING=b:default")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse(any(ln.startswith("CODER_THINKING") for ln in self.env_lines()), self.env_lines())
        self.assertIn("CLIENTS=both", self.env_lines())
        self.assertNotIn("CODER_THINKING", self.installer_env())
        self.assertEqual(json.loads(p.stdout)["changed"], {"CODER_THINKING": {"from": "b:xhigh", "to": ""}})

    def test_coder_model_an_external_model_or_main(self) -> None:
        """/carl's Coder model (Phase 23.4.5): CODER_MODEL=PROVIDER/MODEL (ids with / and :, as openrouter's) is kept in
        client-install.env and given to the installer; main (the default) takes the line out. Pi reads it each time it
        starts the coder: only OpenCode restarts."""
        ext = "openrouter/qwen/qwen3-coder:free"
        p = self.run_set(f"CODER_MODEL={ext}")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn(f"CODER_MODEL={ext}", self.env_lines())
        r = json.loads(p.stdout)
        self.assertEqual(r["changed"], {"CODER_MODEL": {"from": "main", "to": ext}})
        self.assertEqual(r["restart"], ["opencode"])
        self.assertEqual(self.installer_env()["CODER_MODEL"], ext)
        # back to main, with CODER=1 (/carl keeps the coder on): Pi restarts only for what changed
        r = json.loads(self.run_set("CODER_MODEL=main", "CODER=1").stdout)
        self.assertFalse(any(ln.startswith("CODER_MODEL") for ln in self.env_lines()), self.env_lines())
        self.assertNotIn("CODER_MODEL", self.installer_env())
        self.assertEqual(r["changed"]["CODER_MODEL"], {"from": ext, "to": "main"})
        self.assertEqual(r["restart"], ["opencode", "pi"])                   # CODER changed too (on -> 1)
        r = json.loads(self.run_set("CODER_MODEL=zen/free-coder-1", "CODER=1").stdout)
        self.assertEqual(r["restart"], ["opencode"])                         # CODER was 1 already
        for bad in ("CODER_MODEL=", "CODER_MODEL=nomodel", "CODER_MODEL=a b/c", "CODER_MODEL=a/b,c",
                    "CODER_MODEL=/x", "CODER_MODEL=a/\"x\"", "CODER_MODEL_THINKING=a b", "CODER_MODEL_THINKING="):
            p = self.run_set(bad)
            self.assertEqual(p.returncode, 2, bad)
        self.assertIn("CODER_MODEL takes PROVIDER/MODEL or main", self.run_set("CODER_MODEL=x").stderr)
        self.assertIn("CODER_MODEL=zen/free-coder-1", self.env_lines())

    def test_coder_model_thinking_goes_with_a_new_model(self) -> None:
        """CODER_MODEL_THINKING: the variant (OpenCode) or level (Pi) of the external model; default takes it out; a
        new CODER_MODEL takes it out too (the new model starts at its own default: model default)."""
        self.run_set("CODER_MODEL=openrouter/a")
        r = json.loads(self.run_set("CODER_MODEL_THINKING=high").stdout)
        self.assertIn("CODER_MODEL_THINKING=high", self.env_lines())
        self.assertEqual((r["changed"], r["restart"]), ({"CODER_MODEL_THINKING": {"from": "default", "to": "high"}},
                                                        ["opencode"]))
        self.run_set("CODER_MODEL=openrouter/a")                             # the same model: it stays
        self.assertIn("CODER_MODEL_THINKING=high", self.env_lines())
        r = json.loads(self.run_set("CODER_MODEL=openrouter/b").stdout)
        self.assertFalse(any(ln.startswith("CODER_MODEL_THINKING") for ln in self.env_lines()), self.env_lines())
        self.assertEqual(r["changed"]["CODER_MODEL_THINKING"], {"from": "high", "to": "default"})
        self.run_set("CODER_MODEL_THINKING=max")
        self.run_set("CODER_MODEL_THINKING=default")
        self.assertFalse(any(ln.startswith("CODER_MODEL_THINKING") for ln in self.env_lines()), self.env_lines())

    def test_coder_tests_live_in_both_clients(self) -> None:
        """/carl's Tests (Phase 23.4.6): CODER_TESTS=after or off is kept; before (the default) takes the line out; both
        clients read it at each coder task, so nothing restarts; another key with it still restarts as before."""
        r = json.loads(self.run_set("CODER_TESTS=after").stdout)
        self.assertIn("CODER_TESTS=after", self.env_lines())
        self.assertEqual(self.installer_env()["CODER_TESTS"], "after")
        self.assertEqual((r["changed"], r["restart"], r["new_terminal"]),
                         ({"CODER_TESTS": {"from": "before", "to": "after"}}, [], False))
        r = json.loads(self.run_set("CODER_TESTS=before").stdout)
        self.assertFalse(any(ln.startswith("CODER_TESTS") for ln in self.env_lines()), self.env_lines())
        self.assertNotIn("CODER_TESTS", self.installer_env())
        self.assertEqual((r["changed"], r["restart"]), ({"CODER_TESTS": {"from": "after", "to": "before"}}, []))
        r = json.loads(self.run_set("CODER_TESTS=off", "NO_REMINDER=1").stdout)
        self.assertEqual(r["restart"], ["opencode", "pi"])
        self.assertIn("CODER_TESTS takes before or after or off, not 'never'.", self.run_set("CODER_TESTS=never").stderr)

    def test_coder_request_check_live_in_both_clients(self) -> None:
        """/carl's Request Check (the 23.4.3 addendum): CODER_REQUEST_CHECK=on or off is kept; reminder (the default) takes
        the line out; both clients read it at each coder task, so nothing restarts."""
        r = json.loads(self.run_set("CODER_REQUEST_CHECK=on").stdout)
        self.assertIn("CODER_REQUEST_CHECK=on", self.env_lines())
        self.assertEqual((r["changed"], r["restart"]), ({"CODER_REQUEST_CHECK": {"from": "reminder", "to": "on"}}, []))
        r = json.loads(self.run_set("CODER_REQUEST_CHECK=reminder").stdout)
        self.assertFalse(any(ln.startswith("CODER_REQUEST_CHECK") for ln in self.env_lines()), self.env_lines())
        self.assertEqual(r["restart"], [])
        self.assertIn("CODER_REQUEST_CHECK takes reminder or on or off, not 'always'.",
                      self.run_set("CODER_REQUEST_CHECK=always").stderr)

    def test_the_coder_loop_and_the_free_form_brief_live(self) -> None:
        """The 23.4.3 addendum: CODER_RUN_GATE, CODER_FIX_ROUNDS and CODER_FREE_FORM; their defaults (on, 1, model) take
        the line out; both clients read them at each coder task, so nothing restarts."""
        r = json.loads(self.run_set("CODER_RUN_GATE=off", "CODER_FIX_ROUNDS=2", "CODER_FREE_FORM=on").stdout)
        for line in ("CODER_RUN_GATE=off", "CODER_FIX_ROUNDS=2", "CODER_FREE_FORM=on"):
            self.assertIn(line, self.env_lines())
        self.assertEqual(r["restart"], [])
        self.run_set("CODER_RUN_GATE=on", "CODER_FIX_ROUNDS=1", "CODER_FREE_FORM=model")
        self.assertFalse(any(ln.startswith(("CODER_RUN_GATE", "CODER_FIX_ROUNDS", "CODER_FREE_FORM")) for ln in self.env_lines()))
        self.assertIn("CODER_FIX_ROUNDS takes 0 or 1 or 2 or 3, not '5'.", self.run_set("CODER_FIX_ROUNDS=5").stderr)

    def test_a_failed_installer_is_reported(self) -> None:
        open(os.path.join(self.home, "fail"), "w").close()
        p = self.run_set("NO_REMINDER=1")
        self.assertEqual(p.returncode, 1)
        r = json.loads(p.stdout)
        self.assertFalse(r["ok"])
        self.assertIn("exit code 3", r["error"])
        self.assertEqual(r["restart"], [])
        self.assertIn("NO_REMINDER=1", self.env_lines())         # the choice stays: the next setup or sync applies it

    def test_without_an_env_file_set_makes_one(self) -> None:
        os.remove(self.env_path)
        p = self.run_set("NO_MODEL_CHECK=1", "NO_BACKGROUND_SUBAGENTS=1")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.env_lines(), ["NO_MODEL_CHECK=1", "NO_BACKGROUND_SUBAGENTS=1"])
        self.assertEqual(stat.S_IMODE(os.stat(self.env_path).st_mode), 0o600)

    def test_the_result_has_the_slot_count_the_installer_read(self) -> None:
        # /carl warns when it turns the coder on with 1 slot (Phase 23.4.3)
        r = json.loads(self.run_set("NO_CODER=on", "CODER=1").stdout)
        self.assertIsNone(r["slots"])                            # no server answer: not known
        for text, want in (("1\n", 1), ("2\n", 2), ("x\n", None)):
            with open(os.path.join(self.home, "slots"), "w") as f:
                f.write(text)
            r = json.loads(self.run_set("NO_CODER=on", "CODER=1").stdout)
            self.assertEqual(r["slots"], want)
        self.assertEqual([n for n in os.listdir(self.conf) if n.startswith(".set-slots")], [])   # the file goes

    def test_set_waits_for_a_sync_that_holds_the_lock(self) -> None:
        with open(os.path.join(self.conf, "client-sync.lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            p = subprocess.Popen([sys.executable, os.path.join(self.bundle, "carl-sync.py"), "set", "NO_CODER=1"],
                                 env={**os.environ, "HOME": self.home}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            time.sleep(1)
            self.assertIsNone(p.poll())
            self.assertEqual(self.runs(), 0)
            self.assertNotIn("NO_CODER=1", self.env_lines())   # not even the file changes while a sync runs
        out, _ = p.communicate(timeout=30)
        self.assertEqual(p.returncode, 0)
        self.assertTrue(json.loads(out)["ok"])
        self.assertEqual(self.runs(), 1)


if __name__ == "__main__":
    unittest.main()
