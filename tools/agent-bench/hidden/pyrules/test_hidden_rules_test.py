"""AGENTS.md rule 2 on rules-test: the new tests of load_notes are named test_rule_..."""
import unittest

from tests._rules import new_tests_about


class RulesTest(unittest.TestCase):
    def test_names(self) -> None:
        new = new_tests_about("load_notes")
        self.assertTrue(new, "no new test of load_notes")
        self.assertEqual([n for n in new if not n.startswith("test_rule_")], [], "new tests not named test_rule_...")
