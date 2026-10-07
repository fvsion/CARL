"""AGENTS.md rule 1 on rules-func: the new count_notes has a docstring that starts with "Does:"."""
import unittest

from tests._rules import does_doc, find


class RulesFunc(unittest.TestCase):
    def test_docstring(self) -> None:
        fn = find("notes/store.py", "count_notes")
        self.assertIsNotNone(fn, "count_notes is not in notes/store.py")
        self.assertTrue(does_doc(fn), "count_notes has no docstring that starts with 'Does:'")  # type: ignore[arg-type]
