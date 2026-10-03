"""The disk cache's budget (tools/monitor/diskcache.py): the settings with their defaults, and
which saved states go when the folder is over the limit."""
from __future__ import annotations

import os
import tempfile
import unittest

import mon_support  # noqa: F401  (puts tools/ on sys.path)
from monitor import diskcache
from monitor.diskcache import CacheFile


class DiskCacheTest(unittest.TestCase):
    def test_settings_default_to_5_gb_with_both_caches_on(self) -> None:
        self.assertEqual(diskcache.config_of({}), diskcache.CacheConfig(5, True, True))
        conf = diskcache.config_of({"cache": {"disk_gb": 20, "sessions": False}})
        self.assertEqual((conf.disk_gb, conf.prefix, conf.sessions, conf.limit), (20, True, False, 20 * 10 ** 9))

    def test_the_oldest_conversations_go_first_then_the_oldest_prompts(self) -> None:
        files = [CacheFile("carl-prefix-a-1.bin", 3, 1.0), CacheFile("carl-session-a-s0.bin", 3, 2.0),
                 CacheFile("carl-session-a-s1.bin", 3, 3.0), CacheFile("carl-prefix-b-2.bin", 3, 4.0)]
        self.assertEqual(diskcache.over_budget(files, 12), [])
        self.assertEqual(diskcache.over_budget(files, 9), ["carl-session-a-s0.bin"])
        self.assertEqual(diskcache.over_budget(files, 4), ["carl-session-a-s0.bin", "carl-session-a-s1.bin",
                                                           "carl-prefix-a-1.bin"])
        # the file just saved stays while the others can make room
        self.assertEqual(diskcache.over_budget(files, 9, keep="carl-session-a-s0.bin"), ["carl-session-a-s1.bin"])

    def test_a_file_alone_over_the_limit_goes_too(self) -> None:
        files = [CacheFile("carl-prefix-a-1.bin", 3, 1.0), CacheFile("carl-session-a-s0.bin", 9, 2.0)]
        self.assertEqual(diskcache.over_budget(files, 5, keep="carl-session-a-s0.bin"),
                         ["carl-prefix-a-1.bin", "carl-session-a-s0.bin"])

    def test_listing_counts_only_carls_saved_states(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            for n, data in (("carl-prefix-m-1.bin", b"xx"), ("carl-session-m-s0.bin", b"xxx"),
                            ("carl-sessions.json", b"{}"), ("other.bin", b"x")):
                with open(os.path.join(d, n), "wb") as f:
                    f.write(data)
            files = diskcache.listing(d)
            self.assertEqual(sorted(f.name for f in files), ["carl-prefix-m-1.bin", "carl-session-m-s0.bin"])
            self.assertEqual(diskcache.used(files), 5)
            diskcache.remove(d, ["carl-prefix-m-1.bin", "gone.bin"])
            self.assertEqual([f.name for f in diskcache.listing(d)], ["carl-session-m-s0.bin"])


if __name__ == "__main__":
    unittest.main()
