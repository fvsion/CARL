"""tools/carl_bench.py: parsing of real netstat / vm_stat / sysctl output."""
from __future__ import annotations

import os
import unittest

import _paths  # puts tools/ on sys.path
import carl_bench as cb

# netstat -anv -p tcp on macOS 26 (Darwin 25.6), trimmed to three rows.
NETSTAT = """Active Internet connections (including servers)
Proto Recv-Q Send-Q  Local Address          Foreign Address        (state)          rxbytes      txbytes  rhiwat  shiwat          process:pid    state  options           gencnt    flags   flags1 usecnt rtncnt fltrs
tcp4       0      0  127.0.0.1.59357        127.0.0.1.59360        ESTABLISHED         1022            0  407872  146988             node:46708  00102 00000104 000000000137f1ff 00000081 01000800      2      0 000000
tcp4       0      0  127.0.0.1.59357        *.*                    LISTEN                 0            0  131072  131072             node:46708  00100 00000106 000000000137f1fa 00000001 00000800      1      0 000000
tcp4       0      0  *.24800                *.*                    LISTEN                 0            0  131072  131072     synergy-core:62071  00100 00000006 0000000000d2d836 00000000 00000800      1      0 000000
"""

VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                   717237.
Pages active:                                 486049.
Pages wired down:                             239264.
Pages purgeable:                                6600.
"""


class ParseTests(unittest.TestCase):
    def test_is_the_repo_module(self) -> None:
        self.assertEqual(os.path.dirname(os.path.abspath(cb.__file__)), _paths.TOOLS)

    def test_listen_pid(self) -> None:
        self.assertEqual(cb.parse_listen_pid(NETSTAT, 59357), 46708)
        self.assertEqual(cb.parse_listen_pid(NETSTAT, 24800), 62071)

    def test_listen_pid_ignores_established_and_other_ports(self) -> None:
        self.assertIsNone(cb.parse_listen_pid(NETSTAT, 59360))
        self.assertIsNone(cb.parse_listen_pid(NETSTAT, 4800))     # a suffix of 24800, not the port

    def test_wired(self) -> None:
        self.assertAlmostEqual(cb.parse_wired_gib(VM_STAT), 239264 * 16384 / 2**30)

    def test_wired_missing(self) -> None:
        with self.assertRaises(ValueError):
            cb.parse_wired_gib("Pages free: 1.\n")

    def test_swap(self) -> None:
        self.assertEqual(cb.parse_swap_used("total = 4096.00M  used = 3283.19M  free = 812.81M  (encrypted)"), "3283.19M")

    def test_port_of(self) -> None:
        self.assertEqual(cb.port_of("http://192.168.42.1:8080"), 8080)
        self.assertEqual(cb.port_of("http://127.0.0.1:8000/"), 8000)
        self.assertEqual(cb.port_of("http://localhost"), 80)

    def test_validate_base(self) -> None:
        self.assertEqual(cb.validate_base("http://127.0.0.1:8080/"), "http://127.0.0.1:8080")
        with self.assertRaises(SystemExit):
            cb.validate_base("file:///etc/passwd")

    def test_chat_body_turns_thinking_off(self) -> None:
        body = cb.chat_body([{"role": "user", "content": "hi"}], 60, temperature=1.0)
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual((body["max_tokens"], body["temperature"], body["model"]), (60, 1.0, "x"))

    def test_completion_from(self) -> None:
        c = cb.completion_from({"choices": [{"message": {"content": "ok"}}],
                                "timings": {"prompt_n": 5}, "usage": {"prompt_tokens": 7}})
        self.assertEqual((c.content, c.timings.get("prompt_n"), c.usage.get("prompt_tokens")), ("ok", 5, 7))


if __name__ == "__main__":
    unittest.main()
