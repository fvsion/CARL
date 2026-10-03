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
            new = os.path.join(home, ".config", "llm-deploy", "api-key")
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

    def serve(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([os.path.join(REPO, "host", "serve.sh"), *args], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, env={**os.environ, "CARL_CMD": "./carl.sh"})

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
            new = os.path.join(home, ".config", "llm-deploy", "api-key")
            self.assertEqual(argv[argv.index("--api-key-file") + 1], new)
            with open(new, encoding="utf-8") as f:
                self.assertEqual(f.read(), "oldsecret123")

    def test_rejects_bad_port(self) -> None:
        p, argv, _ = self.run_serve(env={"PORT": "80;x"})
        self.assertEqual(p.returncode, 2)
        self.assertEqual(argv, [])


if __name__ == "__main__":
    unittest.main()
