"""tools/llama-log-filter.py (Phase 23.4.4 item 12): CARL's filter between a llama-server at -lv 4 and its log. It
writes the kept lines to the log and to its own output, says where the log is (~/models/logs/.log-PORT.json), and ends
only when its input ends: Ctrl-C (SIGINT), a closed terminal (SIGHUP) and SIGTERM do not stop it. Made-up lines."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from _paths import TOOLS

FILTER = os.path.join(TOOLS, "llama-log-filter.py")
KEPT = "0.01.000.001 I slot launch_slot_: id  0 | task 3 | processing task, is_child = 0\n"
DROPPED = "0.01.000.002 I srv  update_slots: all slots are idle\n"
STATE = ("0.01.000.003 I srv        update:  - cache state: 2 prompts, 8.507 MiB (limits: 2048.000 MiB, 8192 tokens, "
         "26241 est)\n")


class LogFilterTest(unittest.TestCase):
    def test_it_keeps_its_lines_and_outlives_the_signals_until_its_input_ends(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            log = os.path.join(home, "models", "logs", "server.log")
            p = subprocess.Popen([sys.executable, FILTER, "--port", "8123", "--home", home, log],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            assert p.stdin is not None and p.stdout is not None
            p.stdin.write((KEPT + DROPPED).encode())
            p.stdin.flush()
            self.assertEqual(p.stdout.readline().decode(), KEPT)          # at once, line by line
            pointer = os.path.join(home, "models", "logs", ".log-8123.json")
            for _ in range(100):
                if os.path.exists(pointer):
                    break
                time.sleep(0.02)
            with open(pointer, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"log": log, "pid": p.pid})
            for sig in (signal.SIGINT, signal.SIGHUP, signal.SIGTERM):
                p.send_signal(sig)
            time.sleep(0.2)
            self.assertIsNone(p.poll())                                    # still there: the server's last lines
            p.stdin.write(STATE.encode())
            p.stdin.close()                                                # the server stopped
            self.assertEqual(p.stdout.read().decode(), STATE)
            self.assertEqual(p.wait(timeout=10), 0)
            with open(log, encoding="utf-8") as f:
                self.assertEqual(f.read(), KEPT + STATE)

    def test_a_log_it_cannot_write_still_passes_the_lines_on(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            blocked = os.path.join(home, "file")
            with open(blocked, "w") as f:
                f.write("x")
            p = subprocess.run([sys.executable, FILTER, "--port", "8124", "--home", home,
                                os.path.join(blocked, "server.log")],            # a folder that is a file
                               input=(KEPT + DROPPED).encode(), capture_output=True, timeout=20)
            self.assertEqual((p.returncode, p.stdout.decode()), (0, KEPT))


if __name__ == "__main__":
    unittest.main()
