"""AGENTS.md rule 1 on rules-flag: each function of notes/cli.py that now deals with the limit has a "Does:"
docstring."""
import unittest

from tests._rules import does_doc, functions, source


class RulesFlag(unittest.TestCase):
    def test_docstrings(self) -> None:
        touched = {n: f for n, f in functions("notes/cli.py").items() if "limit" in source(f, "notes/cli.py")}
        self.assertTrue(touched, "no function of notes/cli.py names the limit")
        missing = [n for n, f in touched.items() if not does_doc(f)]
        self.assertEqual(missing, [], "changed functions without a 'Does:' docstring")
