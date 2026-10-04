"""The disk cache's budget (tools/monitor/diskcache.py): the settings with their defaults, and
which saved states go when the folder is over the limit."""
from __future__ import annotations

import json
import os
import tempfile
import unittest

import mon_support  # noqa: F401  (puts tools/ on sys.path)
from monitor import diskcache
from monitor.diskcache import CacheFile


class DiskCacheTest(unittest.TestCase):
    def test_settings_default_to_10_gb_both_caches_on_auto_saves(self) -> None:
        self.assertEqual(diskcache.config_of({}), diskcache.CacheConfig(10, True, True, "auto", "auto", 120))
        conf = diskcache.config_of({"cache": {"disk_gb": 20, "sessions": False, "save": "stop", "swa": "window"}})
        self.assertEqual((conf.disk_gb, conf.prefix, conf.sessions, conf.limit, conf.save, conf.swa),
                         (20, True, False, 20 * 10 ** 9, "stop", "window"))
        self.assertEqual(diskcache.config_of({"cache": {"save": "never"}}).save, "auto")

    def test_slot_records(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            docs = {".resident+m+0.json": {"model": "m", "slot": 0, "file": "carl-session+m+k+a.bin", "task": 4},
                    ".resident+m+1.json": {"model": "m", "slot": 1, "file": "../escape.bin", "task": 4},
                    ".resident+other+0.json": {"model": "other", "slot": 0, "file": "carl-session+o+k+b.bin", "task": 1}}
            for n, doc in docs.items():
                with open(os.path.join(d, n), "w") as f:
                    json.dump(doc, f)
            with open(os.path.join(d, ".resident+m+2.json"), "w") as f:
                f.write("not json")
            rs = diskcache.residents(d, "m")
            self.assertEqual([(r.slot, r.file, r.task) for r in rs], [(0, "carl-session+m+k+a.bin", 4)])
            diskcache.drop_resident(rs[0])
            self.assertEqual(diskcache.residents(d, "m"), [])

    def test_the_oldest_conversations_go_first_then_the_oldest_prompts(self) -> None:
        files = [CacheFile("carl-prefix+a+1.bin", 3, 1.0), CacheFile("carl-session+a+0.bin", 3, 2.0),
                 CacheFile("carl-session+a+1.bin", 3, 3.0), CacheFile("carl-prefix+b+2.bin", 3, 4.0)]
        self.assertEqual(diskcache.over_budget(files, 12), [])
        self.assertEqual(diskcache.over_budget(files, 9), ["carl-session+a+0.bin"])
        self.assertEqual(diskcache.over_budget(files, 4), ["carl-session+a+0.bin", "carl-session+a+1.bin",
                                                           "carl-prefix+a+1.bin"])
        # the file just saved stays while the others can make room
        self.assertEqual(diskcache.over_budget(files, 9, keep="carl-session+a+0.bin"), ["carl-session+a+1.bin"])

    def test_a_file_alone_over_the_limit_goes_too(self) -> None:
        files = [CacheFile("carl-prefix+a+1.bin", 3, 1.0), CacheFile("carl-session+a+0.bin", 9, 2.0)]
        self.assertEqual(diskcache.over_budget(files, 5, keep="carl-session+a+0.bin"),
                         ["carl-prefix+a+1.bin", "carl-session+a+0.bin"])

    def test_listing_counts_only_carls_saved_states(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            for n, data in (("carl-prefix+m+a+1.bin", b"xx"), ("carl-session+m+k+s.bin", b"xxx"),
                            ("carl-sessions.json", b"{}"), ("other.bin", b"x")):
                with open(os.path.join(d, n), "wb") as f:
                    f.write(data)
            files = diskcache.listing(d)
            self.assertEqual(sorted(f.name for f in files), ["carl-prefix+m+a+1.bin", "carl-session+m+k+s.bin"])
            self.assertEqual(diskcache.used(files), 5)
            diskcache.remove(d, ["carl-prefix+m+a+1.bin", "gone.bin"])
            self.assertEqual([f.name for f in diskcache.listing(d)], ["carl-session+m+k+s.bin"])


class NamesTest(unittest.TestCase):
    def test_file_names_read_as_model_and_agent_or_session(self) -> None:
        self.assertEqual(diskcache.describe("carl-prefix+qwen3.6-35b-a3b-iq3+pi-coder+0123456789ab.bin"),
                         "qwen3.6-35b-a3b-iq3 · pi-coder")
        self.assertEqual(diskcache.describe("carl-session+qwen3.8-9b+0123456789+ses_abcDEF.bin"), "qwen3.8-9b · ses_abcDEF")
        self.assertEqual(diskcache.describe("odd.bin"), "odd.bin")

    def test_phase_11_files_are_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            for n in ("carl-sessions.json", "carl-session-m-s0.bin", "carl-prefix-m-0123456789abcdef.bin",
                      "carl-prefix+m+build+0123456789ab.bin", "carl-session+m+0123456789+ses_1.bin"):
                open(os.path.join(d, n), "w").close()
            self.assertEqual(sorted(diskcache.legacy(d)), ["carl-prefix-m-0123456789abcdef.bin",
                                                           "carl-session-m-s0.bin", "carl-sessions.json"])


if __name__ == "__main__":
    unittest.main()
