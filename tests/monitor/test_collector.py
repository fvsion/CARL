"""Turning server answers into a snapshot: /metrics, /slots, live rates, the log choice, and
untrusted strings."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True                                  # keep tools/ free of __pycache__
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools"))

import json
from typing import Dict, List

from monitor.api import Endpoint
from monitor.collector import Collector, Sample, choose_log, live_rates, log_pointer, parse_metrics
from monitor.model import RouterInfo, ServerData, SlotInfo, clean, clean_json, flag, flag_int

METRICS = """# HELP llamacpp:prompt_tokens_total Number of prompt tokens processed.
# TYPE llamacpp:prompt_tokens_total counter
llamacpp:prompt_tokens_total 123456
llamacpp:prompt_seconds_total 300.5
llamacpp:spec_decode_accepted_by_position{position="0"} 300
llamacpp:spec_decode_accepted_by_position{position="1"} 200
not-a-metric
llamacpp:broken value
"""


class FakeEndpoint(Endpoint):
    """A server that answers from a dict (path -> body); every path asked is recorded."""

    def __init__(self, answers: Dict[str, str]) -> None:
        super().__init__("127.0.0.1", 8080, "k")
        self.answers = answers
        self.asked: List[str] = []

    def get(self, path: str, timeout: float = 2) -> str:
        self.asked.append(path)
        if path not in self.answers:
            raise OSError(f"no answer for {path}")
        return self.answers[path]


ROUTER_MODELS = {"data": [
    {"id": "a", "status": {"value": "unloaded", "args": ["llama-server", "--port", "0"]}},
    {"id": "b c", "status": {"value": "loaded", "args": ["llama-server", "--port", "51808", "--ctx-size", "8192"]}}]}


class RouterTest(unittest.TestCase):
    def test_router_snapshot_asks_only_about_the_loaded_model_without_autoload(self) -> None:
        q = "?model=b%20c&autoload=false"
        ep = FakeEndpoint({"/health": "{}", "/props": json.dumps({"role": "router"}), "/models": json.dumps(ROUTER_MODELS),
                           "/props" + q: json.dumps({"model_alias": "b c", "total_slots": 1}),
                           "/slots" + q: json.dumps([{"id": 0, "n_ctx": 8192}]), "/metrics" + q: "llamacpp:x 1\n"})
        c = Collector(ep, True, "/nokey", None, None, None, "/tmp", 16384)
        d = ServerData()
        c._http(d)
        assert d.router is not None
        self.assertEqual([(m.id, m.status) for m in d.router.models], [("a", "unloaded"), ("b c", "loaded")])
        self.assertEqual((d.alias, d.n_ctx, d.slots, d.metrics), ("b c", 8192, True, {"x": 1.0}))
        model_paths = [p for p in ep.asked if "model=" in p]
        self.assertTrue(model_paths and all(p.endswith("&autoload=false") for p in model_paths))

    def test_no_model_loaded(self) -> None:
        idle = {"data": [{"id": "a", "status": {"value": "unloaded"}}]}
        ep = FakeEndpoint({"/health": "{}", "/props": json.dumps({"role": "router"}), "/models": json.dumps(idle)})
        d = ServerData()
        Collector(ep, True, "/nokey", None, None, None, "/tmp", 16384)._http(d)
        self.assertEqual((d.alias, d.slots), ("", False))
        self.assertIsNone(d.router.current if d.router else "no router")
        self.assertFalse(any("model=" in p for p in ep.asked))

    def test_single_server(self) -> None:
        ep = FakeEndpoint({"/health": "{}", "/props": json.dumps({"model_alias": "m"}), "/slots": "[]",
                           "/metrics": ""})
        d = ServerData()
        Collector(ep, True, "/nokey", None, None, None, "/tmp", 16384)._http(d)
        self.assertIsNone(d.router)
        self.assertEqual(d.alias, "m")
        self.assertNotIn("/models", ep.asked)

    def test_router_info_and_long_flags(self) -> None:
        self.assertIsNone(RouterInfo.from_json({"data": "x"}).current)
        self.assertEqual(flag("llama-server --temperature 0.6 --ubatch-size 512", "--temp"), "0.6")   # a router child
        self.assertEqual(flag("llama-server --ubatch-size 512", "-ub"), "512")


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
            self.assertEqual(choose_log(d.cmd, None, console, home), log)             # not written yet, no console
            self.assertEqual(choose_log("llama-server", None, console, home), latest)
            with open(console, "w") as f:
                f.write("x")
            self.assertEqual(choose_log(d.cmd, None, console, home), console)         # empty / missing log: console
            with open(log, "w") as f:
                f.write("0.00.000.001 I x\n")
            self.assertEqual(choose_log(d.cmd, None, console, home), log)


    def test_the_log_of_a_server_at_lv4_comes_from_the_filters_pointer(self) -> None:
        """Phase 23.4.4 item 12: the server has no --log-file; CARL's log filter wrote ~/models/logs/.log-PORT.json."""
        with tempfile.TemporaryDirectory() as home:
            log = os.path.join(home, "models", "logs", "llama-server-1.log")
            os.makedirs(os.path.dirname(log))
            with open(log, "w") as f:
                f.write("0.00.000.001 I x\n")
            with open(os.path.join(home, "models", "logs", ".log-8080.json"), "w") as f:
                json.dump({"log": log, "pid": os.getpid()}, f)
            self.assertEqual(log_pointer(home, 8080), {"log": log, "pid": os.getpid()})
            self.assertEqual(log_pointer(home, 8081), {})
            console = os.path.join(home, ".console-8080.out")
            self.assertEqual(choose_log("llama-server -lv 4", None, console, home, log), log)


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
