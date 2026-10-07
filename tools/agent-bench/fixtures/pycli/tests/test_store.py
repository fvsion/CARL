import os
import tempfile
import unittest

from notes import store
from notes.model import Note


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "notes.json")

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_no_file_means_no_notes(self) -> None:
        self.assertEqual(store.load_notes(self.path), [])

    def test_add_then_load(self) -> None:
        a = store.add_note("  first ", ["home"], self.path)
        b = store.add_note("second", [], self.path)
        self.assertEqual((a.id, b.id), (1, 2))
        self.assertEqual(store.load_notes(self.path), [Note(1, "first", ["home"]), Note(2, "second", [])])

    def test_mark_done(self) -> None:
        store.add_note("x", [], self.path)
        self.assertTrue(store.mark_done(1, self.path))
        self.assertFalse(store.mark_done(9, self.path))
        self.assertTrue(store.load_notes(self.path)[0].done)
