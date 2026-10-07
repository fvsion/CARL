"""AGENTS.md rule 1 on rules-rename: the renamed (changed) _new_note_id has a "Does:" docstring."""
import unittest

from tests._rules import does_doc, find


class RulesRename(unittest.TestCase):
    def test_docstring(self) -> None:
        fn = find("notes/store.py", "_new_note_id")
        self.assertIsNotNone(fn, "_new_note_id is not in notes/store.py")
        self.assertTrue(does_doc(fn), "_new_note_id has no docstring that starts with 'Does:'")  # type: ignore[arg-type]
