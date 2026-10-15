"""tools/monitor/rules.py: the rules list's split, the pattern list and the list itself (the standard library only; the
NLP tiers are measured by tools/compaction-bench/rules/rules_eval.py, not here)."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools", "monitor"))
import rules as rl  # noqa: E402


class SplitTest(unittest.TestCase):
    def test_held_parts_never_split(self) -> None:
        got = [s.text for s in rl.split("Use e.g. pathlib.Path here. Don't edit src/a.py. Keep v1.2.3 (a; b. c) as is.")]
        self.assertEqual(got, ["Use e.g. pathlib.Path here.", "Don't edit src/a.py.", "Keep v1.2.3 (a; b. c) as is."])

    def test_semicolon_and_list_items(self) -> None:
        got = [s.text for s in rl.split("Don't touch the migrations; make a new one.\n- one rule\n- another rule")]
        self.assertEqual(got, ["Don't touch the migrations; make a new one.", "one rule", "another rule"])

    def test_code_block_kept_whole(self) -> None:
        got = [s.text for s in rl.split("The console says:\n```\nError. Cannot read\n```\nI changed it.")]
        self.assertEqual(len(got), 2)
        self.assertIsNone(rl.pattern_strength(got[0]))


class PatternTest(unittest.TestCase):
    def test_strengths(self) -> None:
        cases = {
            "Never use print for errors.": "strong",
            "No new dependencies.": "strong",
            "Fix it, and keep the public header unchanged.": "strong",
            "Use pathlib for paths.": "weak",
            "Write the docstrings in Google style.": "weak",
            "Put the tests in tests/test_stats.py.": "weak",        # a rule-style verb with "in"
            "Add tests for load_notes in notes/store.py.": None,    # a literal alone is not a rule (user, 2026-10-14)
            "Add a function count_notes() to notes/store.py.": None,
            "Why does it fail?": None,
            "We fixed the parser yesterday.": None,
            "Thanks!": None,
            "It's only used in that file.": None,
        }
        for text, want in cases.items():
            self.assertEqual(rl.pattern_strength(text), want, text)


class ListTest(unittest.TestCase):
    def test_own_words_only(self) -> None:
        msgs = [{"role": "user", "content": "<system-reminder>x</system-reminder>Never use print.\n\n[CARL reminder] y"},
                {"role": "assistant", "content": "Never do that."},
                {"role": "user", "content": [{"type": "text", "text": "Thanks. Stick to the standard library."}]}]
        out = rl.run(msgs, "patterns")
        self.assertEqual([(r["turn"], r["quote"]) for r in out["rules"]],
                         [(1, "Never use print."), (2, "Stick to the standard library.")])
        self.assertTrue(out["text"].startswith(rl.HEADER))
        self.assertIn('- "Never use print." (turn 1)', out["text"])

    def test_moved_block_dropped(self) -> None:
        moved = "Instructions from: /p/AGENTS.md\nAlways use tabs."
        out = rl.run([{"role": "user", "content": moved + "\n\nNever use print."}], "patterns", moved=moved + "\n\n")
        self.assertEqual([r["quote"] for r in out["rules"]], ["Never use print."])

    def test_duplicate_keeps_newest(self) -> None:
        text, kept, left = rl.build([rl.Rule(1, "Never use print.", "strong"), rl.Rule(4, "never use print", "strong")])
        self.assertEqual([(r.turn, r.quote) for r in kept], [(4, "never use print")])
        self.assertEqual(left, 0)

    def test_cap_drops_oldest_weak_first(self) -> None:
        rules = [rl.Rule(t, f"Use the helper number {t} for every thing of that kind in this project.", "weak")
                 for t in range(1, 40)] + [rl.Rule(1, "Never commit secrets.", "strong")]
        text, kept, left = rl.build(rules, cap=200)
        self.assertGreater(left, 0)
        self.assertIn("Never commit secrets.", [r.quote for r in kept])
        self.assertEqual(kept[-1].turn, 39)                      # the newest weak ones stay
        self.assertLessEqual(len(text) / rl.CHARS_PER_TOKEN, 200 + 20)
        self.assertIn("older rules were left out.", text)

    def test_nothing(self) -> None:
        self.assertEqual(rl.run([{"role": "user", "content": "Thanks!"}], "patterns")["text"], "")

    def test_cap_keeps_the_first_message_last(self) -> None:
        rules = [rl.Rule(1, "Every function you add gets a docstring.", "weak")] + [
            rl.Rule(t, f"Use the helper number {t} for every thing of that kind in this project.", "strong")
            for t in range(2, 40)]
        text, kept, left = rl.build(rules, cap=200)
        self.assertGreater(left, 0)
        self.assertEqual(kept[0].quote, "Every function you add gets a docstring.")


class ChangedRuleTest(unittest.TestCase):
    """A rule changed or lifted later, and questions (discovery round 2, 2026-10-14)."""

    def quotes(self, *turns: str) -> list:
        msgs = []
        for t in turns:
            msgs += [{"role": "user", "content": t}, {"role": "assistant", "content": "OK."}]
        return [r["quote"] for r in rl.run(msgs, "patterns")["rules"]]

    def test_lifted_rule_is_quoted(self) -> None:
        got = self.quotes("Profile it first; don't change any code until I've seen the numbers.",
                          "OK, those numbers help. You can change code now.")
        self.assertIn("You can change code now.", got)

    def test_change_cue_without_a_matching_rule_is_not(self) -> None:
        got = self.quotes("Never commit secrets.", "Scratch the staging test, staging is down today.")
        self.assertEqual(got, ["Never commit secrets."])

    def test_question_is_never_a_rule(self) -> None:
        self.assertEqual(self.quotes("Should we never use print here?"), [])


class DiscoveryTest(unittest.TestCase):
    """The list logic of the recall discovery (2026-10-14)."""

    def quotes(self, *turns: str) -> list:
        msgs = []
        for t in turns:
            msgs += [{"role": "user", "content": t}, {"role": "assistant", "content": "OK."}]
        return [r["quote"] for r in rl.run(msgs, "patterns")["rules"]]

    def test_lead_line_is_not_a_rule(self) -> None:
        got = self.quotes("Please keep to these while you work on the importer:\n- Log through `audit`, never print.\n"
                          "- No wildcard imports.")
        self.assertEqual(got, ["Log through `audit`, never print.", "No wildcard imports."])

    def test_standing_intro_without_the_word_rules(self) -> None:
        got = self.quotes("Some things to keep in mind the whole time:\n- Small commits.\n- Branches start with the "
                          "ticket id.\n\nNow add a /health route.")
        self.assertEqual(got, ["Small commits.", "Branches start with the ticket id."])

    def test_task_list_is_not_a_rules_list(self) -> None:
        self.assertEqual(self.quotes("Steps for today:\n- bump requests;\n- run the integration tests."), [])

    def test_line_after_an_item_is_its_own_sentence(self) -> None:
        got = [s.text for s in rl.split("Changes:\n- a date filter;\n- a CSV button.\nKeep the layout as it is.")]
        self.assertEqual(got, ["Changes:", "a date filter;", "a CSV button.", "Keep the layout as it is."])

    def test_lower_case_lines_split(self) -> None:
        got = [s.text for s in rl.split("add a dark mode toggle\ndont touch the colors file, the designer owns it\n"
                                        "this sentence was wrapped in the middle of\nthe line by hand.")]
        self.assertEqual(got, ["add a dark mode toggle", "dont touch the colors file, the designer owns it",
                               "this sentence was wrapped in the middle of the line by hand."])

    def test_two_sentence_rule_quoted_whole(self) -> None:
        got = self.quotes("Every function that you add or change gets a docstring. Its first line starts with "
                          "`Does:`. Now fix the parser.")
        self.assertEqual(got, ["Every function that you add or change gets a docstring. Its first line starts with "
                               "`Does:`."])

    def test_list_of_mostly_rules(self) -> None:
        got = self.quotes("- Never commit secrets.\n- Name every new test method `test_rule_` and then what it checks."
                          "\n- Don't touch `vendor/`.")
        self.assertEqual(len(got), 3)

    def test_conventions_said_as_facts(self) -> None:
        cases = {"Every foreign key column gets an index.": "weak",
                 "New tests go in tests/unit, one file per module.": "weak",
                 "Config keys are snake_case.": "weak",
                 "Fix the code, not the tests.": "weak",
                 "Standard library only, please.": "weak",
                 "In this codebase we write Optional[X].": "strong",
                 "We're on Python 3.9 here.": "strong",
                 "Read every test file in this project and list what is untested.": None}
        for text, want in cases.items():
            self.assertEqual(rl.pattern_strength(text), want, text)


if __name__ == "__main__":
    unittest.main()
