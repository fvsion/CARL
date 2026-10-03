"""The rolling conversation cache's pure parts (tools/monitor/sessions.py): when a slot is due
for a save, the manifest, what can be restored into which server, and what is stale."""
from __future__ import annotations

import os
import tempfile
import unittest

import mon_support  # noqa: F401  (puts tools/ on sys.path)
from monitor import sessions


class SessionsTest(unittest.TestCase):
    def test_a_slot_is_saved_once_it_has_been_idle_with_a_new_state(self) -> None:
        watch: dict = {}
        self.assertEqual(sessions.due(watch, [(0, False, 9000, 5)], 0.0, idle=60), [])        # just seen
        self.assertEqual(sessions.due(watch, [(0, False, 9000, 5)], 30.0, idle=60), [])       # not idle long enough
        self.assertEqual(sessions.due(watch, [(0, False, 9000, 5)], 61.0, idle=60), [0])
        watch[0].saved = watch[0].state                                                        # saved
        self.assertEqual(sessions.due(watch, [(0, False, 9000, 5)], 200.0, idle=60), [])
        self.assertEqual(sessions.due(watch, [(0, True, 9500, 6)], 210.0, idle=60), [])       # busy
        self.assertEqual(sessions.due(watch, [(0, False, 9600, 6)], 211.0, idle=60), [])      # changed: wait again
        self.assertEqual(sessions.due(watch, [(0, False, 9600, 6)], 272.0, idle=60), [0])
        self.assertEqual(sessions.due({}, [(1, False, 100, 1)], 0.0, idle=0), [])             # too short to keep

    def test_restore_only_into_the_server_it_came_from(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            a, b = sessions.file_name("m1", 0), sessions.file_name("m1", 1)
            for n in (a, b):
                open(os.path.join(d, n), "w").close()
            doc = sessions.record({}, a, "m1", 0, "k1", 9000, 1.0)
            doc = sessions.record(doc, b, "m1", 1, "old", 5000, 1.0)
            doc = sessions.record(doc, "gone.bin", "m1", 0, "k1", 10, 1.0)                      # file missing
            sessions.write_manifest(d, doc)
            doc = sessions.read_manifest(d)
            self.assertEqual(sessions.restorable(doc, d, "m1", "k1", [0, 1]), [(0, a, 9000)])
            self.assertEqual(sessions.restorable(doc, d, "m1", "k1", [1]), [])                # slot 0 is taken
            self.assertEqual(sessions.restorable(doc, d, "m2", "k1", [0, 1]), [])
            self.assertEqual(sessions.stale(doc, "m1", "k1"), [b])
            doc = sessions.forget(doc, d, b)
            self.assertFalse(os.path.exists(os.path.join(d, b)))
            self.assertNotIn(b, doc)
        self.assertEqual(sessions.summary([(0, a, 9000)]), "conversations restored: slot 0 (9.0K tokens)")
        self.assertIsNone(sessions.summary([]))
        self.assertNotEqual(sessions.server_key("/nofile", "q4_0", "b1"), sessions.server_key("/nofile", "q8_0", "b1"))


if __name__ == "__main__":
    unittest.main()
