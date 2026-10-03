"""host/common.sh helpers and host/serve-llama.sh's argument building, run with
bash. serve-llama.sh is started with a fake llama-server on PATH that records its
arguments (no model is loaded)."""
from __future__ import annotations

import json
import os
import shutil
import socket
import stat
import subprocess
import tempfile
import textwrap
import unittest

from _paths import HOST, REPO

COMMON = os.path.join(HOST, "common.sh")


def bash(script: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run SCRIPT under set -euo pipefail with host/common.sh sourced."""
    full = f"set -euo pipefail\nsource {COMMON!r}\n{script}"
    return subprocess.run(["bash", "-c", full], capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


class CommonTests(unittest.TestCase):
    def test_ensure_api_key_creates_private_key_and_returns(self) -> None:
        # Regression: under pipefail, tr's SIGPIPE used to end the script right
        # after creating the key (a first start stopped without a message).
        with tempfile.TemporaryDirectory() as d:
            key = os.path.join(d, "keys", "api-key")
            p = bash(f"ensure_api_key {key!r}; echo after")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("after", p.stdout)
            with open(key, encoding="utf-8") as f:
                secret = f.read()
            self.assertRegex(secret, r"^[A-Za-z0-9]{40}$")
            self.assertNotIn(secret, p.stdout + p.stderr)
            self.assertEqual(stat.S_IMODE(os.stat(key).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(key)).st_mode), 0o700)

    def test_ensure_api_key_copies_the_pre_1_2_key_once(self) -> None:
        """The default key file is new in 1.2.0: the first start copies the old MTPLX-era
        key (same value: configured clients keep working), private, and leaves the old file."""
        with tempfile.TemporaryDirectory() as home:
            old = os.path.join(home, ".mtplx", "api-key")
            os.makedirs(os.path.dirname(old))
            with open(old, "w", encoding="utf-8") as f:
                f.write("oldsecret123\n")
            p = bash('ensure_api_key "$CARL_KEY_FILE"; echo "$CARL_KEY_FILE"', env={"HOME": home})
            self.assertEqual(p.returncode, 0, p.stderr)
            new = os.path.join(home, ".config", "carl", "api-key")
            self.assertEqual(p.stdout.strip(), new)
            with open(new, encoding="utf-8") as f:
                self.assertEqual(f.read().strip(), "oldsecret123")
            self.assertIn("moved the API key", p.stderr)
            self.assertNotIn("oldsecret123", p.stdout + p.stderr)
            self.assertEqual(stat.S_IMODE(os.stat(new).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(new)).st_mode), 0o700)
            self.assertTrue(os.path.exists(old))
            with open(new, "w", encoding="utf-8") as f:                 # once there, the new key wins
                f.write("newsecret456")
            p = bash('ensure_api_key "$CARL_KEY_FILE"', env={"HOME": home})
            self.assertEqual((p.returncode, p.stderr), (0, ""))
            with open(new, encoding="utf-8") as f:
                self.assertEqual(f.read(), "newsecret456")

    def test_ensure_api_key_takes_the_newest_earlier_key(self) -> None:
        """Both settings folders exist (the old one is not moved then) and only the old one
        holds a key: that key is copied, not the older MTPLX one."""
        with tempfile.TemporaryDirectory() as home:
            for rel, text in ((".mtplx/api-key", "mtplxkey"), (".config/llm-deploy/api-key", "renamedkey")):
                os.makedirs(os.path.join(home, os.path.dirname(rel)), exist_ok=True)
                with open(os.path.join(home, rel), "w", encoding="utf-8") as f:
                    f.write(text)
            os.makedirs(os.path.join(home, ".config", "carl"))
            p = bash('ensure_api_key "$CARL_KEY_FILE"', env={"HOME": home})
            self.assertEqual(p.returncode, 0, p.stderr)
            with open(os.path.join(home, ".config", "carl", "api-key"), encoding="utf-8") as f:
                self.assertEqual(f.read(), "renamedkey")
            self.assertIn("copied from ~/.config/llm-deploy/api-key", p.stderr)
            self.assertTrue(os.path.isfile(os.path.join(home, ".config", "llm-deploy", "api-key")))

    def old_conf(self, home: str) -> str:
        """A settings folder under its name from before the rename, as a 1.2.0 install left it."""
        old = os.path.join(home, ".config", "llm-deploy")
        os.makedirs(old)
        for name, text in (("config.json", '{"schema": 1}'), ("models.json", "{}"), ("api-key", "oldsecret123"),
                           ("llama.env", "CTX=65536\n")):
            with open(os.path.join(old, name), "w", encoding="utf-8") as f:
                f.write(text)
            os.chmod(os.path.join(old, name), 0o644)
        os.chmod(old, 0o755)
        return old

    def test_migrate_conf_dir_moves_once_and_links_the_old_path(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            old = self.old_conf(home)
            new = os.path.join(home, ".config", "carl")
            p = bash("migrate_conf_dir; migrate_conf_dir", env={"HOME": home, "CARL_CONF_DIR": ""})
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(p.stderr.count("moved ~/.config/llm-deploy to ~/.config/carl"), 1)   # once
            self.assertEqual(sorted(os.listdir(new)), ["api-key", "config.json", "llama.env", "models.json"])
            self.assertEqual(stat.S_IMODE(os.stat(new).st_mode), 0o700)
            for name in os.listdir(new):
                self.assertEqual(stat.S_IMODE(os.stat(os.path.join(new, name)).st_mode), 0o600, name)
            self.assertTrue(os.path.islink(old))
            self.assertEqual(os.readlink(old), "carl")
            with open(os.path.join(old, "api-key"), encoding="utf-8") as f:          # old configs still read it
                self.assertEqual(f.read(), "oldsecret123")
            self.assertNotIn("oldsecret123", p.stdout + p.stderr)

    def test_migrate_conf_dir_leaves_both_folders_alone(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            old = self.old_conf(home)
            new = os.path.join(home, ".config", "carl")
            os.makedirs(new)
            p = bash("migrate_conf_dir", env={"HOME": home, "CARL_CONF_DIR": ""})
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("both ~/.config/carl and ~/.config/llm-deploy exist: using ~/.config/carl", p.stderr)
            self.assertEqual(os.listdir(new), [])
            self.assertFalse(os.path.islink(old))
            self.assertEqual(len(os.listdir(old)), 4)

    def test_migrate_conf_dir_does_nothing_without_an_old_folder_or_with_conf_dir(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            p = bash("migrate_conf_dir", env={"HOME": home, "CARL_CONF_DIR": ""})
            self.assertEqual((p.returncode, p.stderr), (0, ""))
            self.assertEqual(os.listdir(home), [])
            old = self.old_conf(home)
            p = bash("migrate_conf_dir", env={"HOME": home, "CARL_CONF_DIR": os.path.join(home, "mine")})
            self.assertEqual((p.returncode, p.stderr), (0, ""))
            self.assertFalse(os.path.islink(old))
            self.assertFalse(os.path.exists(os.path.join(home, ".config", "carl")))

    def test_ensure_api_key_other_file_is_not_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            old = os.path.join(home, ".mtplx", "api-key")
            os.makedirs(os.path.dirname(old))
            with open(old, "w", encoding="utf-8") as f:
                f.write("oldsecret123")
            other = os.path.join(home, "mine", "key")
            p = bash(f"ensure_api_key {other!r}", env={"HOME": home})
            self.assertEqual(p.returncode, 0, p.stderr)
            with open(other, encoding="utf-8") as f:
                self.assertRegex(f.read(), r"^[A-Za-z0-9]{40}$")

    def test_require_int(self) -> None:
        self.assertEqual(bash("require_int CTX 98304 4096 262144").returncode, 0)
        for bad in ("1+1", "08", "", "-5", "4095"):
            p = bash(f"require_int CTX {bad!r} 4096")
            self.assertEqual(p.returncode, 2, bad)

    def test_require_int_never_evaluates(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            marker = os.path.join(d, "pwned")
            p = bash(f"require_int X 'a[$(touch {marker})]'")
            self.assertEqual(p.returncode, 2)
            self.assertFalse(os.path.exists(marker))

    def test_port_pid_ignores_junk(self) -> None:
        p = bash("port_pid 'x|y'; port_pid 99999; port_host ''; echo done")
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "done"))

    def test_apply_settings(self) -> None:
        p = bash(textwrap.dedent("""\
            CTX=1234; MODEL=env
            apply_settings 'MODEL|CTX|KV|EXTRA_ARGS' 'MODEL' <<< $'MODEL=/m.gguf\\nCTX=98304\\nKV=q8_0\\nPATH=/evil\\nEXTRA_ARGS=--x $(id)'
            printf '%s|%s|%s|%s|%s\\n' "$MODEL" "$CTX" "$KV" "$EXTRA_ARGS" "${SETTINGS_USED[*]}"
            command -v bash >/dev/null && echo path-ok
            """))
        self.assertEqual(p.returncode, 0, p.stderr)
        lines = p.stdout.splitlines()
        self.assertEqual(lines[0], "/m.gguf|1234|q8_0|--x $(id)|MODEL=/m.gguf KV=q8_0 EXTRA_ARGS=--x $(id)")
        self.assertEqual(lines[1], "path-ok")
        self.assertIn("ignoring unknown setting 'PATH'", p.stderr)

    def test_resolve_host_refuses_wildcards(self) -> None:
        for h in ("0.0.0.0", "::", "*"):
            p = bash("resolve_host local", {"HOST": h})
            self.assertEqual(p.returncode, 1, h)
            self.assertIn("refusing", p.stderr)

    def test_resolve_host_local(self) -> None:
        p = bash('HOST=""; unset HOST; resolve_host local; echo "$HOST"')
        self.assertEqual(p.stdout.strip(), "127.0.0.1")


class ServeDispatch(unittest.TestCase):
    """host/serve.sh commands that answer without starting anything."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()          # a throw-away home: commands may move its settings folder
        self.home = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def serve(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = {k: v for k, v in os.environ.items() if k not in ("CARL_CONF_DIR", "API_KEY_FILE")}
        return subprocess.run([os.path.join(REPO, "host", "serve.sh"), *args], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, env={**env, "CARL_CMD": "./carl.sh", "HOME": self.home})

    def test_config_moves_the_old_settings_folder_first(self) -> None:
        old = os.path.join(self.home, ".config", "llm-deploy")
        os.makedirs(old)
        with open(os.path.join(old, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"schema": 1, "llama": {"net": "local"}}, f)
        p = self.serve("config", "get", "llama.net")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout), "local")
        self.assertIn("moved ~/.config/llm-deploy to ~/.config/carl", p.stderr)
        self.assertEqual(self.serve("config", "path").stdout.strip(),
                         os.path.join(self.home, ".config", "carl", "config.json"))

    def test_help_and_unknown_commands_move_nothing(self) -> None:
        old = os.path.join(self.home, ".config", "llm-deploy")
        os.makedirs(old)
        for args in (("-h",), ("help", "env"), ("nope",), ("grant",)):
            self.serve(*args)
        self.assertFalse(os.path.islink(old))
        self.assertFalse(os.path.exists(os.path.join(self.home, ".config", "carl")))

    def test_removed_mtplx_presets_point_to_llama(self) -> None:
        for cmd in ("grant", "pocket"):
            p = self.serve(cmd, "--local")
            self.assertEqual(p.returncode, 2, cmd)
            self.assertIn("was removed in CARL 1.2.0", p.stderr)
            self.assertIn("./carl.sh llama", p.stderr)

    def test_help_and_unknown_command(self) -> None:
        p = self.serve("-h")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotRegex(p.stdout, r"(?i)mtplx|grant|pocket|:8000")
        p = self.serve("nope")
        self.assertEqual(p.returncode, 2)
        self.assertIn("unknown command 'nope'", p.stderr)


class ServeLlamaArgs(unittest.TestCase):
    """serve-llama.sh with MONITOR=0 execs llama-server: a fake one records argv."""

    def run_serve(self, *args: str, env: dict[str, str] | None = None,
                  extra_args: list[str] | None = None) -> tuple[subprocess.CompletedProcess[str], list[str], str]:
        with tempfile.TemporaryDirectory() as d:
            bindir, conf = os.path.join(d, "bin"), os.path.join(d, "conf")
            os.makedirs(bindir)
            os.makedirs(conf)
            argv_file = os.path.join(d, "argv")
            fake = os.path.join(bindir, "llama-server")
            with open(fake, "w", encoding="utf-8") as f:
                f.write(f'#!/usr/bin/env bash\nprintf "%s\\n" "$@" > {argv_file!r}\n')
            os.chmod(fake, 0o755)
            model = os.path.join(d, "fake.gguf")
            with open(model, "wb") as f:
                f.write(b"GGUF")
            with open(os.path.join(conf, "config.json"), "w", encoding="utf-8") as f:
                json.dump({"schema": 1, "llama": {"extra_args": extra_args or []}}, f)
            key = os.path.join(d, "key", "api-key")
            run_env = {k: v for k, v in os.environ.items() if k not in ("HOST", "NET", "EXTRA_ARGS", "CTX", "SLOTS")}
            run_env.update(PATH=bindir + ":" + os.environ["PATH"], SKIP_DEPS="1", MONITOR="0", KEEP_AWAKE="0",
                           LOG_FILE="none", THINK_TOGGLE="0", FIT_CHECK="0", ALLOW_SECOND_MODEL="1",
                           PORT=str(free_port()), API_KEY_FILE=key, CARL_CONF_DIR=conf, MODEL=model)
            run_env.update(env or {})
            p = subprocess.run([os.path.join(REPO, "host", "serve-llama.sh"), "--local", *args],
                               capture_output=True, text=True, env=run_env, stdin=subprocess.DEVNULL)
            argv: list[str] = []
            if os.path.exists(argv_file):
                with open(argv_file, encoding="utf-8") as f:
                    argv = f.read().splitlines()
            key_mode = oct(stat.S_IMODE(os.stat(key).st_mode)) if os.path.exists(key) else ""
            return p, argv, key_mode

    def test_extra_args_split_into_words_before_command_line_extras(self) -> None:
        p, argv, key_mode = self.run_serve("--ctx", "16k", "--foo", "a b",
                                           extra_args=["--threads", "4", "--flag-x"])
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(key_mode, "0o600")                      # first start creates the key and goes on
        self.assertEqual(argv[-5:], ["--threads", "4", "--flag-x", "--foo", "a b"])
        self.assertIn("127.0.0.1", argv[argv.index("--host") + 1])
        slots = int(argv[argv.index("--parallel") + 1])
        self.assertEqual(argv[argv.index("-c") + 1], str(slots * 16384))   # slots x the 16k window

    def test_extra_args_from_environment_are_not_globbed(self) -> None:
        p, argv, _ = self.run_serve(env={"EXTRA_ARGS": "--x * ?"})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(argv[-3:], ["--x", "*", "?"])

    def test_rejects_non_numeric_settings(self) -> None:
        p, argv, _ = self.run_serve(env={"UB": "512+1"})
        self.assertEqual(p.returncode, 2)
        self.assertIn("UB must be a whole number", p.stderr)
        self.assertEqual(argv, [])

    def test_default_key_file_is_migrated_and_used(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            old = os.path.join(home, ".mtplx", "api-key")
            os.makedirs(os.path.dirname(old))
            with open(old, "w", encoding="utf-8") as f:
                f.write("oldsecret123")
            cache = os.path.expanduser("~/models/.metal-limit")   # the GPU limit probe takes seconds
            if os.path.exists(cache):
                os.makedirs(os.path.join(home, "models"))
                shutil.copy(cache, os.path.join(home, "models", ".metal-limit"))
            p, argv, _ = self.run_serve(env={"API_KEY_FILE": "", "HOME": home})
            self.assertEqual(p.returncode, 0, p.stderr)
            new = os.path.join(home, ".config", "carl", "api-key")
            self.assertEqual(argv[argv.index("--api-key-file") + 1], new)
            with open(new, encoding="utf-8") as f:
                self.assertEqual(f.read(), "oldsecret123")

    def test_old_settings_folder_is_moved_before_the_start(self) -> None:
        """A home from before the rename: its config.json and key are used from ~/.config/carl."""
        with tempfile.TemporaryDirectory() as home:
            old = os.path.join(home, ".config", "llm-deploy")
            os.makedirs(old)
            with open(os.path.join(old, "config.json"), "w", encoding="utf-8") as f:
                json.dump({"schema": 1, "llama": {"extra_args": ["--from-old-config"]}}, f)
            with open(os.path.join(old, "api-key"), "w", encoding="utf-8") as f:
                f.write("renamedsecret")
            cache = os.path.expanduser("~/models/.metal-limit")   # the GPU limit probe takes seconds
            if os.path.exists(cache):
                os.makedirs(os.path.join(home, "models"))
                shutil.copy(cache, os.path.join(home, "models", ".metal-limit"))
            p, argv, _ = self.run_serve(env={"API_KEY_FILE": "", "CARL_CONF_DIR": "", "HOME": home})
            self.assertEqual(p.returncode, 0, p.stderr)
            new = os.path.join(home, ".config", "carl")
            self.assertIn("--from-old-config", argv)
            self.assertEqual(argv[argv.index("--api-key-file") + 1], os.path.join(new, "api-key"))
            with open(os.path.join(new, "api-key"), encoding="utf-8") as f:
                self.assertEqual(f.read(), "renamedsecret")
            self.assertTrue(os.path.islink(old))

    def test_rejects_bad_port(self) -> None:
        p, argv, _ = self.run_serve(env={"PORT": "80;x"})
        self.assertEqual(p.returncode, 2)
        self.assertEqual(argv, [])


if __name__ == "__main__":
    unittest.main()
