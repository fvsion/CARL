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


if __name__ == "__main__":
    unittest.main()
