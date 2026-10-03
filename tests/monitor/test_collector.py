"""Turning server answers into a snapshot: /metrics, /slots, live rates, the log choice, and
untrusted strings."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True                                  # keep tools/ free of __pycache__
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools"))

from monitor.collector import Sample, choose_log, live_rates, parse_metrics
from monitor.model import ServerData, SlotInfo, clean, clean_json, flag, flag_int

METRICS = """# HELP llamacpp:prompt_tokens_total Number of prompt tokens processed.
# TYPE llamacpp:prompt_tokens_total counter
llamacpp:prompt_tokens_total 123456
llamacpp:prompt_seconds_total 300.5
llamacpp:spec_decode_accepted_by_position{position="0"} 300
llamacpp:spec_decode_accepted_by_position{position="1"} 200
not-a-metric
llamacpp:broken value
"""


class MetricsTest(unittest.TestCase):
    def test_values_and_positions(self) -> None:
        m, pos = parse_metrics(METRICS)
        self.assertEqual(m, {"prompt_tokens_total": 123456.0, "prompt_seconds_total": 300.5})
        self.assertEqual(pos, {0: 300.0, 1: 200.0})


class SlotsTest(unittest.TestCase):
    def test_slot_from_json(self) -> None:
        s = SlotInfo.from_json({"id": 1, "is_processing": True, "id_task": 241, "n_ctx": 98304, "n_prompt_tokens": 20000,
                                "n_prompt_tokens_cache": 8000, "n_prompt_tokens_processed": 5000,
                                "next_token": [{"n_decoded": 7}]})
        self.assertEqual(s, SlotInfo(1, True, 241, 98304, 20000, 8000, 5000, 7))

    def test_bad_fields_are_defaults(self) -> None:
        s = SlotInfo.from_json({"id": "x", "n_ctx": "big", "next_token": "no", "id_task": [1]})
        self.assertEqual((s.id, s.n_ctx, s.decoded, s.task, s.busy), (None, 0, 0, None, False))


class RatesTest(unittest.TestCase):
    def test_same_request_only(self) -> None:
        d = ServerData(t=12.0, busy=True, task=5, processed=300, decoded=0)
        self.assertEqual(live_rates(d, Sample(5, 10.0, 100, 0)), (100.0, None))
        self.assertEqual(live_rates(d, Sample(6, 10.0, 100, 0)), (None, None))
        self.assertEqual(live_rates(d, None), (None, None))
        self.assertEqual(live_rates(ServerData(t=12.0, busy=False, task=5), Sample(5, 10.0, 0, 0)), (None, None))


class LogChoiceTest(unittest.TestCase):
    def test_log_file_console_and_latest(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            log = os.path.join(home, "server.log")
            console = os.path.join(home, ".console-8080.out")
            latest = os.path.join(home, "models/logs/llama-server-latest.log")
            d = ServerData(cmd=f"llama-server --log-file {log}", pid=1, etime="00:10")
            self.assertEqual(choose_log(d, None, console, home), log)             # not written yet, no console
            self.assertEqual(choose_log(ServerData(cmd="llama-server"), None, console, home), latest)
            with open(console, "w") as f:
                f.write("x")
            self.assertEqual(choose_log(d, None, console, home), console)         # empty / missing log: console
            with open(log, "w") as f:
                f.write("0.00.000.001 I x\n")
            self.assertEqual(choose_log(d, None, console, home), log)


class UntrustedTextTest(unittest.TestCase):
    def test_clean_drops_control_characters_but_keeps_tabs_and_newlines(self) -> None:
        self.assertEqual(clean("a\x1b[2Jb\x07c\x9bd\te\nf"), "a[2Jbcd\te\nf")

    def test_clean_json_recurses(self) -> None:
        self.assertEqual(clean_json({"k\x1b": ["v\x07", 1, {"x": "\x1b]52;c;aGk=\x07"}]}),
                         {"k": ["v", 1, {"x": "]52;c;aGk="}]})

    def test_flags(self) -> None:
        cmd = "llama-server -m /m/a.gguf -c=4096 --parallel 0 -np x"
        self.assertEqual(flag(cmd, "-m", "--model"), "/m/a.gguf")
        self.assertEqual(flag(cmd, "-c"), "4096")
        self.assertIsNone(flag(cmd, "--temp"))
        self.assertEqual(flag(cmd, "--temp", default="N/A"), "N/A")
        self.assertEqual(flag_int(cmd, "--parallel", default=1), 0)      # "0" is a value, not missing
        self.assertEqual(flag_int(cmd, "-np", default=1), 1)            # not a number
        self.assertEqual(flag_int(cmd, "--ctx-size", default=7), 7)


if __name__ == "__main__":
    unittest.main()
