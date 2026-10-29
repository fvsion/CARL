"""Adapters where the behaviour depends on the technology: private atomic JSON writes (in a
temporary folder), and the netstat / ps / KEY=value parsers (on sample output)."""
from __future__ import annotations

import os
import stat
import tempfile
import unittest

import support  # noqa: F401  (import path)
from carl_core.adapters.json_files import JsonFile, read_env_file, read_json
from carl_core.adapters.system import parse_listening_ports, parse_processes
from carl_core.domain.errors import ConfigError

NETSTAT = """Active Internet connections (including servers)
Proto Recv-Q Send-Q  Local Address          Foreign Address        (state)      rhiwat  shiwat    pid   epid
tcp4       0      0  127.0.0.1.8080         *.*                    LISTEN       131072  131072  4242      0
tcp4       0      0  192.168.1.5.52000      17.1.1.1.443           ESTABLISHED  131072  131072   100      0
tcp6       0      0  *.8000                 *.*                    LISTEN       131072  131072   777      0
tcp4       0      0  *.*                    *.*                    CLOSED       131072  131072     1      0
"""


class JsonFileTest(unittest.TestCase):
    def test_atomic_private_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            doc = JsonFile(os.path.join(d, "conf", "config.json"))
            self.assertIsNone(doc.load())
            doc.save({"schema": 1, "name": "ü"})
            self.assertEqual(doc.load(), {"schema": 1, "name": "ü"})
            self.assertEqual(stat.S_IMODE(os.stat(doc.path).st_mode), 0o600)
            self.assertEqual(os.listdir(os.path.dirname(doc.path)), ["config.json"])   # no temp file left

    def test_damaged_json(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.json")
            with open(path, "w") as f:
                f.write("{nope")
            with self.assertRaisesRegex(ConfigError, "x.json"):
                read_json(path)

    def test_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "llama.env")
            with open(path, "w") as f:
                f.write("# old settings\nNET=vm\nEXTRA=a=b\n\njunk\n")
            self.assertEqual(read_env_file(path), {"NET": "vm", "EXTRA": "a=b"})
            self.assertEqual(read_env_file(os.path.join(d, "missing")), {})


class ParsersTest(unittest.TestCase):
    def test_listening_ports(self) -> None:
        self.assertEqual(parse_listening_ports(NETSTAT), {8080, 8000})
        self.assertEqual(parse_listening_ports(""), set())

    def test_processes(self) -> None:
        procs = parse_processes("  101 9437184 /opt/homebrew/bin/llama-server\n"
                                "  7 120 /System/Applications/LM Studio\nbad\n")
        self.assertEqual([(p.pid, p.rss_kib, p.command) for p in procs],
                         [(101, 9437184, "/opt/homebrew/bin/llama-server"), (7, 120, "/System/Applications/LM Studio")])




class BatchedBenchTest(unittest.TestCase):
    def test_parse_the_table(self) -> None:
        from carl_core.adapters.llama_server import parse_batched
        text = """|    PP |     TG |    B |   N_KV |   T_PP s | S_PP t/s |   T_TG s | S_TG t/s |      T s |    S t/s |
|-------|--------|------|--------|----------|----------|----------|----------|----------|----------|
|  1024 |    128 |    1 |   1152 |    2.131 |   480.59 |    5.021 |    25.49 |    7.152 |   161.08 |
|  1024 |    128 |    8 |   9216 |   28.402 |   288.43 |   22.740 |    45.03 |   51.141 |   180.21 |
"""
        self.assertEqual(parse_batched(text), {1: (25.49, 480.59), 8: (45.03, 288.43)})
        self.assertEqual(parse_batched("no table"), {})

    def test_the_bench_runs_with_fit_off(self) -> None:
        """llama.cpp 0.6 fits by default: Auto-tune's llama-batched-bench passes --fit off, as every CARL start."""
        from unittest import mock
        from carl_core.adapters import llama_server as ls
        seen = []

        def run(argv, **kw):                        # type: ignore[no-untyped-def]
            seen.append(argv)
            return mock.Mock(stdout="")
        ctl = ls.LlamaServerControl("/nope/serve-llama.sh", "/m/a.gguf", 8093, "k", "/tmp/x.log", lambda: set())
        with mock.patch.object(ls.subprocess, "run", run):
            ctl.parallel([1, 2], 512, 96, "q4_0")
        argv = seen[0]
        self.assertEqual(argv[0], "llama-batched-bench")
        self.assertEqual(argv[argv.index("--fit") + 1], "off")
        self.assertNotIn("-lv", argv)


class TuneServerStartTest(unittest.TestCase):
    def test_the_tune_server_starts_through_the_launcher_on_its_port(self) -> None:
        """Auto-tune's server is CARL's launcher (it passes --port and --fit off; tests/scripts/test_cli_text.py)
        with PORT set to the tune's port, local only, without the user's settings; no -lv: Auto-tune reads no
        buffer sizes from the log."""
        from carl_core.adapters import llama_server as ls
        with tempfile.TemporaryDirectory() as d:
            rec = os.path.join(d, "rec")
            launcher = os.path.join(d, "serve-llama.sh")
            with open(launcher, "w", encoding="utf-8") as f:
                f.write(f'#!/usr/bin/env bash\nprintf "%s\\n" "PORT=$PORT" "SETTINGS_FILE=$SETTINGS_FILE" "$@" > {rec!r}\n'
                        'echo "error: a fake launcher"; exit 1\n')
            os.chmod(launcher, 0o755)
            ctl = ls.LlamaServerControl(launcher, "/m/a.gguf", 8093, "k", os.path.join(d, "logs", "tune.log"),
                                        lambda: set())
            with self.assertRaises(ConfigError):
                ctl.start("ngram-mod", 2, 32768)
            with open(rec, encoding="utf-8") as f:
                got = f.read().splitlines()
        self.assertEqual(got[:2], ["PORT=8093", "SETTINGS_FILE=none"])
        self.assertEqual(got[2:], ["--model", "/m/a.gguf", "--local", "--ctx", "32768", "--kv", "q4"])


if __name__ == "__main__":
    unittest.main()
