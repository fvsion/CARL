"""Text helpers: column-exact cutting and padding, wrapping, numbers, cards and their click spans."""
from __future__ import annotations

import os
import sys
import unittest

sys.dont_write_bytecode = True                                  # keep tools/ free of __pycache__
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools"))

from monitor.fmt import (ANSI, B, DIM, GRN, R, RED, YEL, Ln, bar, button_rows, buttons, ctx_label, cwrap, draw_card,
                         fit, footer_keys, heading, indent, lv, side_by_side, vlen, wrap, wwrap)


class FitTest(unittest.TestCase):
    def test_pads_plain_text_to_width(self) -> None:
        self.assertEqual(fit("abc", 6), "abc" + R + "   ")

    def test_cuts_with_an_ellipsis(self) -> None:
        self.assertEqual(fit("abcdefgh", 5), "abcd…" + R)

    def test_exact_width_is_not_cut(self) -> None:
        self.assertEqual(fit("abcde", 5), "abcde" + R)

    def test_colour_codes_take_no_columns(self) -> None:
        s = f"{RED}ab{R}cd"
        out = fit(s, 4)
        self.assertEqual(out, s + R)
        self.assertEqual(vlen(out), 4)

    def test_wide_characters_count_two_columns(self) -> None:
        self.assertEqual(vlen("😎a"), 3)
        out = fit("😎😎😎", 5)                       # two emoji fit (4 columns); the third does not: padded
        self.assertEqual(vlen(out), 5)
        self.assertTrue(out.startswith("😎😎"))

    def test_wide_character_that_does_not_fit_is_dropped(self) -> None:
        self.assertEqual(vlen(fit("a😎", 2)), 2)


class WrapTest(unittest.TestCase):
    def test_wrap_cuts_plain_text_every_w_characters(self) -> None:
        self.assertEqual(wrap(f"{RED}abcdefg{R}", 3), ["abc", "def", "g"])

    def test_wrap_breaks_at_spaces_and_after_slashes(self) -> None:
        self.assertEqual(wrap("E failed: Gemma4Assistant requires ctx_other", 30),
                         ["E failed: Gemma4Assistant", "requires ctx_other"])
        self.assertEqual(wrap("/Users/x/models/gguf/gemma-4-E4B-it-Q4_0.gguf", 24),
                         ["/Users/x/models/gguf/", "gemma-4-E4B-it-Q4_0.gguf"])

    def test_wrap_of_empty_text_is_one_empty_line(self) -> None:
        self.assertEqual(wrap("", 5), [""])

    def test_wwrap_wraps_words_at_least_ten_columns(self) -> None:
        self.assertEqual(wwrap("one two three four", 4), ["one two", "three four"])
        self.assertEqual(wwrap(None, 20), [""])


class ColourWrapTest(unittest.TestCase):
    TEXT = f"Press {B}Enter{R} to pick a model: {RED}auto is auto fit's pick for this Mac{R} and more words follow here"

    def test_every_line_fits_and_no_word_is_lost(self) -> None:
        for w in (10, 17, 25, 40, 200):
            lines = cwrap(self.TEXT, w, "  ")
            with self.subTest(w=w):
                self.assertTrue(all(vlen(x) <= w for x in lines))
                self.assertEqual(" ".join(ANSI.sub("", x).strip() for x in lines), ANSI.sub("", self.TEXT))
                self.assertFalse(any("…" in x for x in lines))

    def test_colour_continues_on_the_next_line(self) -> None:
        lines = cwrap(f"{RED}one two three four{R} five", 10)
        self.assertEqual(lines[0], f"{RED}one two{R}")
        self.assertTrue(lines[1].startswith(RED))              # still red after the break
        self.assertTrue(all(x.endswith(R) for x in lines[:2]))
        self.assertEqual(ANSI.sub("", lines[-1]), "five")

    def test_indent_space_runs_newlines_and_long_words(self) -> None:
        lines = cwrap(lv("fit", "fits: a model needs 15.3G for 2 x 96K"), 24, " " * 10)
        self.assertTrue(ANSI.sub("", lines[0]).startswith("fit       fits:"))     # the padded label survives
        self.assertTrue(all(ANSI.sub("", x).startswith(" " * 10) for x in lines[1:]))
        self.assertEqual(cwrap("a\n  b", 10), ["a", "  b"])
        self.assertEqual(cwrap("x" * 25, 10), ["x" * 10, "x" * 10, "x" * 5])
        self.assertEqual(cwrap(None, 10), [""])

    def test_heading_and_button_rows(self) -> None:
        self.assertEqual(vlen(heading("Status", 30)), 30)
        rows = button_rows("", [("Start server (a)", "s"), ("Auto fit (A)", "f"), ("Revert (r)", "r")], 40)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(vlen(x.text.rstrip()) <= 40 for x in rows))
        self.assertEqual([a for x in rows for _, _, a in x.spans], ["s", "f", "r"])
        self.assertEqual(rows[1].spans[0][:2], (0, len("[ Revert (r) ]")))
        self.assertEqual(len(button_rows("", [("a", "a"), ("b", "b")], 80)), 1)


class NumbersTest(unittest.TestCase):
    def test_footer_keeps_its_tail(self) -> None:
        keys = [("↑↓", "setting"), ("← →", "change"), ("Enter", "choose a model"), ("a", "apply")]
        tail = [("D", "detail"), ("?", "all keys"), ("q", "quit")]
        for w in (30, 60, 200):
            line = ANSI.sub("", footer_keys(keys, tail, w))
            self.assertTrue(line.endswith("D detail   ? all keys   q quit"), line)    # keys: three spaces apart
            self.assertTrue(len(line) <= w or line == "D detail   ? all keys   q quit")
        self.assertIn("a apply", ANSI.sub("", footer_keys(keys, tail, 200)))

    def test_ctx_label(self) -> None:
        self.assertEqual(ctx_label(98304), "96K")
        self.assertEqual(ctx_label("65536"), "64K")
        self.assertEqual(ctx_label(52722), "51.5K")                  # K = 1024 (glossary): never 52.7K
        self.assertEqual(ctx_label("auto"), "auto")

    def test_bar_colour_by_fill(self) -> None:
        self.assertTrue(bar(0.5, 10).startswith(GRN + "█" * 5))
        self.assertTrue(bar(0.8, 10).startswith(YEL))
        self.assertTrue(bar(2.0, 10).startswith(RED + "█" * 10))     # clamped
        self.assertIn(DIM + "░" * 10, bar(None, 10))


class CardTest(unittest.TestCase):
    def test_card_rows_are_exactly_w_wide(self) -> None:
        rows = draw_card("x", "TITLE", "summary", ["line", Ln("click me", act="go")], 30, 1)
        self.assertEqual([vlen(t) for t, _ in rows], [30, 30, 30, 30])

    def test_header_is_clickable_and_whole_line_actions_span_the_inside(self) -> None:
        rows = draw_card("x", "TITLE", "", ["a", Ln("b", act="go")], 30, 1)
        self.assertEqual(rows[0][1], [(0, 30, "level:x")])
        self.assertEqual(rows[2][1], [(2, 28, "go")])

    def test_collapsed_card_is_one_line(self) -> None:
        rows = draw_card("x", "T", "summary", ["a", "b"], 30, 0)
        self.assertEqual(len(rows), 1)
        self.assertIn("▸ T", ANSI.sub("", rows[0][0]))
        self.assertNotIn("●", ANSI.sub("", draw_card("x", "T", "", ["a"], 30, 1)[0][0]))     # no detail dots

    def test_buttons_spans_follow_the_prefix(self) -> None:
        ln = buttons("ab", [("Yes", "y"), ("No", "n")])
        self.assertEqual(ln.spans, [(2, 9, "y"), (11, 17, "n")])
        rows = draw_card("x", "T", "", [ln], 40, 1)
        self.assertEqual(rows[1][1], [(4, 11, "y"), (13, 19, "n")])     # +2: the border and a space

    def test_indent_and_side_by_side_shift_spans(self) -> None:
        self.assertEqual(indent([("t", [(0, 2, "a")])], 3), [("   t", [(3, 5, "a")])])
        rows = side_by_side([("L", [(0, 1, "l")])], [("R", [(0, 1, "r")]), ("R2", [])], 5)
        self.assertEqual(rows[0][1], [(1, 2, "l"), (7, 8, "r")])
        self.assertEqual(rows[1][0], " " + " " * 5 + R + " R2")     # a missing left row is padded


if __name__ == "__main__":
    unittest.main()
