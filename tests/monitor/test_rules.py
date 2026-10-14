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


if __name__ == "__main__":
    unittest.main()
