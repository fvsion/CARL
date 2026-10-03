"""Parsing macOS command output (real samples from an M-series Mac), and sh() itself."""
from __future__ import annotations

import os
import sys
import time
import unittest

sys.dont_write_bytecode = True                                  # keep tools/ free of __pycache__
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools"))

from monitor import system
from monitor.system import TcpRow

NETSTAT = """Active Internet connections (including servers)
Proto Recv-Q Send-Q  Local Address                                 Foreign Address                               (state)          rxbytes      txbytes  rhiwat  shiwat          process:pid    state  options           gencnt    flags   flags1 usecnt rtncnt fltrs
tcp4       0      0  127.0.0.1.8080         *.*                    LISTEN                 0            0  131072  131072  llama-server:63389  00100 00000006 0000000000a2f6c1 00000000 00000800      1      0 000000
tcp4       0      0  127.0.0.1.8080         127.0.0.1.54012        ESTABLISHED       194371       802811  408300  146988  llama-server:63389  00102 00000004 0000000000a30e11 00000080 04000900      2      0 000000
tcp4       0      0  192.168.25.105.60571   34.54.194.141.443      ESTABLISHED         1764         2189  131072  131600    Claude Helper:57067  00102 00000008 00000000013862a6 00000080 04000900      2      0 000000
tcp6       0      0  fe80::e484:d5ff:.64032 fe80::64f0:d9ff:.49161 ESTABLISHED         5990         3286  131072  131376         rapportd:704    00102 00000004 0000000000e2a93b 00080083 01000800      2      0 000000
tcp6       0      0  *.8000                 *.*                    LISTEN                 0            0  131072  131072         Python:4242     00100 00000006 00000000004a8640 00000001 00000800      1      0 000000
udp4       0      0  *.5353                 *.*                                    0            0  786896    9216        mDNSResponder:390   00000 00000000 0000000000000a2c 00000000 00000800      1      0 000000
"""


class NetstatTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = system.parse_netstat(NETSTAT)

    def test_rows_with_pids(self) -> None:
        self.assertEqual(self.rows[0], TcpRow("127.0.0.1", 8080, "*.*", "LISTEN", 63389))
        self.assertEqual(self.rows[2], TcpRow("192.168.25.105", 60571, "34.54.194.141.443", "ESTABLISHED", 57067))

    def test_process_names_with_spaces_and_ipv6(self) -> None:
        self.assertEqual(self.rows[3].local_port, 64032)
        self.assertEqual(self.rows[3].pid, 704)
        self.assertEqual(len(self.rows), 5)                   # the header and udp are skipped

    def test_listen_host_and_pid(self) -> None:
        self.assertEqual(system.listen_host(self.rows, 8080), "127.0.0.1")
        self.assertEqual(system.listen_host(self.rows, 8000), "127.0.0.1")      # "*": all addresses
        self.assertIsNone(system.listen_host(self.rows, 9999))
        self.assertEqual(system.listen_pid(self.rows, 8080), 63389)
        self.assertEqual(system.listen_pid(self.rows, 8000), 4242)

    def test_connections_to_the_server(self) -> None:
        self.assertEqual(system.connections(self.rows, 8080, 63389), [("127.0.0.1", "54012")])
        self.assertEqual(system.connections(self.rows, 8080, 1), [])


class ProcessTest(unittest.TestCase):
    def test_parse_ps(self) -> None:
        info = system.parse_ps("  2528   12.5 01-02:03:04 /opt/llama-server -m /m/a.gguf --port 8080\n")
        assert info is not None
        self.assertEqual((info.rss, info.cpu, info.etime), (2528 * 1024, 12.5, "01-02:03:04"))
        self.assertEqual(info.cmd, "/opt/llama-server -m /m/a.gguf --port 8080")
        self.assertIsNone(system.parse_ps(""))

    def test_parse_ps_drops_control_characters(self) -> None:
        info = system.parse_ps("1 0.0 00:01 evil\x1b]0;title\x07name\n")
        assert info is not None
        self.assertEqual(info.cmd, "evil]0;titlename")

    def test_etime_seconds_adds_a_minute(self) -> None:
        self.assertEqual(system.etime_seconds("00:10"), 70)
        self.assertEqual(system.etime_seconds("01:00:00"), 3660)
        self.assertEqual(system.etime_seconds("2-00:00:01"), 2 * 86400 + 61)
        self.assertEqual(system.etime_seconds(None), 60)


class MemoryPowerTest(unittest.TestCase):
    def test_vm_stat(self) -> None:
        text = ("Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free:      3885.\n"
                "Pages active:    819102.\nPages wired down:   200000.\nPages occupied by compressor: 1000.\n")
        vm = system.parse_vm_stat(text, 16384)
        self.assertEqual(vm["Pages free"], 3885 * 16384)
        self.assertEqual(vm["Pages occupied by compressor"], 1000 * 16384)
        self.assertNotIn("Mach Virtual Memory Statistics", vm)

    def test_swap_gpu_power_pressure(self) -> None:
        self.assertEqual(system.parse_swap("total = 4096.00M  used = 3307.19M  free = 788.81M  (encrypted)"),
                         (3307.19 * 2**20, 4096.0 * 2**20))
        self.assertEqual(system.parse_swap(""), (0, 0))
        self.assertEqual(system.parse_gpu('"Device Utilization %"=15 x "In use system memory"=974585856'), (15, 974585856))
        self.assertEqual(system.parse_gpu(""), (None, None))
        self.assertEqual(system.parse_power("Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t100%; charged"), "AC 100%")
        self.assertEqual(system.parse_power("Now drawing from 'Battery Power'\n 54%; discharging"), "battery 54%")
        self.assertEqual(system.pressure_name("1"), "normal")
        self.assertEqual(system.pressure_name("4"), "CRITICAL")
        self.assertEqual(system.pressure_name(""), "?")

    def test_thermal(self) -> None:
        self.assertEqual(system.parse_thermal("Note: No thermal warning level has been recorded\n"), "nominal")
        self.assertEqual(system.parse_thermal("CPU_Speed_Limit = 80\n"), "CPU_Speed_Limit = 80")

    def test_sleep_events_since_start(self) -> None:
        log = ("2026-10-03 09:00:00 +0200 Sleep               \tEntering Sleep state due to 'Idle Sleep'\n"
               "2026-10-03 11:00:00 +0200 Wake                \tWake from Deep Idle [CDNVA] : due to UserActivity\n"
               "2026-10-03 11:00:05 +0200 Assertions          \tPID 1 Created\n")
        since = time.mktime(time.strptime("2026-10-03 10:00:00", "%Y-%m-%d %H:%M:%S"))
        events = system.parse_sleep_events(log, since)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][:2], ("11:00:00", "Wake"))

    def test_ifconfig_addresses(self) -> None:
        text = ("lo0: flags\n\tinet 127.0.0.1 netmask 0xff000000\n\tinet 192.168.25.105 netmask 0xffffff00\n"
                "\tinet 192.168.42.1 netmask 0xffffff00\n\tinet 192.168.25.105 netmask 0xffffff00\n")
        self.assertEqual(system.parse_ifconfig(text, "192.168.42.1"), ["192.168.25.105"])


class ShTest(unittest.TestCase):
    """sh() runs real commands (adapter test)."""

    def test_output_and_errors(self) -> None:
        self.assertEqual(system.sh(["/bin/echo", "hi"]), "hi\n")
        self.assertEqual(system.sh(["/nonexistent/command"]), "")

    def test_bytes_that_are_not_utf8_are_replaced(self) -> None:
        # pmset prints device names in MacRoman: "Brennon\xd5s Magic Keyboard"
        self.assertEqual(system.sh(["/usr/bin/printf", "a\\325b"]), "a�b")

    def test_timeout_does_not_wait(self) -> None:
        t0 = time.time()
        self.assertEqual(system.sh(["/bin/sleep", "5"], timeout=0.2), "")
        self.assertLess(time.time() - t0, 2)


if __name__ == "__main__":
    unittest.main()
