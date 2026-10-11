"""What the disk cache did (Phase 23.5.1.1, the counters): the events, the table and the SLOTS card's Cache column."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode = True                                  # keep tools/ free of __pycache__
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools"))

from monitor import cachestats as cs
from monitor.cacheapi import CacheState, handle
from monitor.diskcache import CacheConfig
from monitor.logbook import LogBook

NOW = 1_800_000_000.0


def write_events(carl_dir: str, events, t: float = NOW) -> str:
    d = cs.events_dir(carl_dir)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, cs.day_name(t))
    with open(path, "a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    return path


class EventsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp()

    def test_clean_keeps_only_known_fields(self) -> None:
        e = cs.clean({"e": "place", "t": NOW - 5, "slot": 1, "from": "session", "tokens": 9000, "history": True,
                      "prompt": "secret text", "session": "s" * 300}, NOW)
        assert e
        self.assertNotIn("prompt", e)
        self.assertEqual((e["slot"], e["from"], e["tokens"], e["history"]), (1, "session", 9000, True))
        self.assertEqual(len(str(e["session"])), cs.MAX_TEXT)
        self.assertIsNone(cs.clean({"e": "place", "from": "elsewhere"}, NOW))           # not a known source
        self.assertIsNone(cs.clean({"e": "drop"}, NOW))
        self.assertIsNone(cs.clean("x", NOW))
        self.assertEqual(cs.clean({"e": "retire", "t": NOW - 10 * 86400}, NOW)["t"], NOW)   # a far-off clock: now

    def test_a_remote_clients_events_are_appended_with_its_address_and_the_files_size(self) -> None:
        slots = os.path.join(self.dir, "slots")
        os.makedirs(slots)
        with open(os.path.join(slots, "carl-session+m+k+s1.bin"), "wb") as f:
            f.write(b"x" * 1234)
        n = cs.append(self.dir, [{"e": "save", "what": "session", "file": "carl-session+m+k+s1.bin", "tokens": 9},
                                 {"e": "nonsense"}], "10.0.0.9", slots, NOW)
        self.assertEqual(n, 1)
        with open(os.path.join(cs.events_dir(self.dir), cs.day_name(NOW)), encoding="utf-8") as f:
            doc = json.loads(f.readline())
        self.assertEqual((doc["host"], doc["bytes"], doc["tokens"]), ("10.0.0.9", 1234, 9))

    def test_the_api_takes_events_with_the_key(self) -> None:
        state = CacheState(os.path.join(self.dir, "slots"))
        os.makedirs(state.folder)
        code, doc = handle(state, CacheConfig, "POST", "/carl/cache/events", {},
                           {"events": [{"e": "retire", "file": "carl-prefix+m+a+1.bin"}]}, carl_dir=self.dir,
                           host="10.0.0.9")
        self.assertEqual((code, doc), (200, {"kept": 1}))
        self.assertEqual(handle(state, CacheConfig, "POST", "/carl/cache/events", {}, {"events": "x"},
                                carl_dir=self.dir)[0], 400)

    def test_the_log_reads_only_what_is_new_and_waits_for_a_whole_line(self) -> None:
        path = write_events(self.dir, [{"e": "retire", "t": NOW - 60}])
        log = cs.EventLog(self.dir)
        self.assertEqual(len(log.poll(NOW)), 1)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"e": "retire", "t": NOW - 30}) + "\n" + '{"e": "ret')     # the last one half written
        self.assertEqual(len(log.poll(NOW)), 2)
        with open(path, "a", encoding="utf-8") as f:
            f.write('ire", "t": ' + str(NOW - 20) + "}\n")
        self.assertEqual(len(log.poll(NOW)), 3)
        self.assertEqual(len(log.poll(NOW + cs.KEEP_S + 61)), 0)                         # two days later: gone

    def test_day_files_older_than_two_days_go(self) -> None:
        write_events(self.dir, [{"e": "retire"}], NOW - 4 * 86400)
        write_events(self.dir, [{"e": "retire"}], NOW)
        self.assertEqual(cs.prune(self.dir, NOW), 1)
        self.assertEqual(os.listdir(cs.events_dir(self.dir)), [cs.day_name(NOW)])


class TableTest(unittest.TestCase):
    EVENTS = [
        {"e": "place", "t": NOW - 600, "slot": 0, "session": "s1", "agent": "build", "from": "session", "tokens": 38_000,
         "history": True},
        {"e": "place", "t": NOW - 500, "slot": 1, "session": "s2", "agent": "coder", "from": "prompt", "tokens": 9_900,
         "sub": True},
        {"e": "place", "t": NOW - 400, "slot": 0, "session": "s3", "agent": "build", "from": "read", "history": True},
        {"e": "place", "t": NOW - 300, "slot": 0, "session": "s3", "agent": "build", "from": "slot", "history": True},
        {"e": "save", "t": NOW - 200, "what": "session", "session": "s3", "tokens": 51_000, "bytes": 600_000_000},
        {"e": "save", "t": NOW - 7200, "what": "prompt", "agent": "build", "bytes": 100_000_000},     # before the hour
        {"e": "retire", "t": NOW - 100, "file": "carl-prefix+m+build+1.bin"},
    ]
    # (end, slot, prompt tokens, read, seconds): the request after each placement; s3's read 78K at 500 tok/s
    TALLY = [(NOW - 598, 0, 38_100, 100, 0.2), (NOW - 498, 1, 10_000, 100, 0.2), (NOW - 390, 0, 78_000, 78_000, 156.0),
             (NOW - 299, 0, 78_200, 200, 0.4)]

    def test_the_rows(self) -> None:
        rows = {r.what: r for r in cs.table(self.EVENTS, self.TALLY, NOW - 3600)}
        self.assertEqual((rows["Restored sessions"].count, rows["Restored sessions"].tokens), ("1", "38K"))
        self.assertEqual((rows["Restored prompts"].count, rows["Restored prompts"].tokens), ("1", "9.9K"))
        self.assertEqual((rows["Read again"].count, rows["Read again"].tokens, rows["Read again"].time),
                         ("1", "78K", "took 2 min"))
        self.assertEqual((rows["Saved sessions"].count, rows["Saved sessions"].written), ("1", "600 MB"))
        self.assertEqual(rows["Saved prompts"].count, "0")                        # it was saved before the hour
        self.assertEqual(rows["Retired files"].count, "1")
        # reused 37,900 + 9,900 + 0 + 78,000 = 125,800 of 204,300; still in the slot: that less CARL's 47,900
        self.assertEqual(rows["Still in their slot"].tokens, "78K")
        self.assertEqual(rows["All requests"].tokens, "126K of 204K reused (62%)")
        self.assertTrue(rows["Restored sessions"].time.startswith("saved "))

    def test_no_requests_yet(self) -> None:
        rows = {r.what: r for r in cs.table([], [], NOW - 3600)}
        self.assertEqual(rows["All requests"].tokens, "no request yet")
        self.assertEqual(rows["Restored sessions"].count, "0")

    def test_the_windows(self) -> None:
        self.assertEqual(cs.window_start("last hour", NOW, None), NOW - 3600)
        self.assertEqual(cs.window_start("since the start", NOW, NOW - 99), NOW - 99)
        midnight = cs.window_start("today", NOW, None)
        self.assertEqual(time.localtime(midnight)[3:6], (0, 0, 0))

    def test_the_slot_column(self) -> None:
        names = {"s3": "Fix the date test"}
        self.assertEqual(cs.slot_column(self.EVENTS, 0, "", names, NOW), ("its slot", "51K, 3 min ago",
                                                                          "Fix the date test"))
        self.assertEqual(cs.slot_column(self.EVENTS, 1, "", names, NOW), ("coder prompt, 9.9K", "no (a subagent)", "s2"))
        self.assertIsNone(cs.slot_column(self.EVENTS, 2, "", names, NOW))
        ev = [{"e": "place", "t": NOW, "slot": 0, "session": "ses_abcdefghijklmnop", "from": "read", "model": "m"}]
        self.assertEqual(cs.slot_column(ev, 0, "m", {}, NOW), ("read (a new session)", "not yet", "ses_abcdefgh…"))
        self.assertIsNone(cs.slot_column(ev, 0, "other-model", {}, NOW))


class TallyTest(unittest.TestCase):
    def test_answered_requests_are_tallied_not_carls_own(self) -> None:
        book = LogBook()
        book.start = NOW
        for line in ["0.10.000.000 I slot launch_slot_: id  0 | task 2 | processing task, is_child = 0",
                     "0.10.500.000 I slot print_timing: id  0 | task 2 | prompt eval time =    1000.00 ms /   500 tokens"
                     " (    2.00 ms per token,   500.00 tokens per second)",
                     "0.10.600.000 I slot print_timing: id  0 | task 2 |        eval time =    1669.89 ms /    80 tokens"
                     " (   21.14 ms per token,    47.31 tokens per second)",
                     "0.10.700.000 I slot      release: id  0 | task 2 | stop processing: n_tokens = 9080, truncated = 0",
                     # CARL's own (a prompt read into a slot, n_predict 0): not tallied
                     "0.11.000.000 I slot launch_slot_: id  1 | task 3 | processing task, is_child = 0",
                     "0.11.500.000 I slot print_timing: id  1 | task 3 | prompt eval time =    9000.00 ms /  4500 tokens"
                     " (    2.00 ms per token,   500.00 tokens per second)",
                     "0.11.600.000 I slot      release: id  1 | task 3 | stop processing: n_tokens = 4500, truncated = 0"]:
            book.add(line)
        (t, slot, prompt, new, secs), = book.tally()
        self.assertEqual((slot, prompt, new, secs), (0, 9000, 500, 1.0))
        self.assertAlmostEqual(t, NOW + 10.7, places=3)          # the log's clock: 0.10.700 is 10.7 s
        book.start = None
        self.assertEqual(book.tally(), [])


if __name__ == "__main__":
    unittest.main()
