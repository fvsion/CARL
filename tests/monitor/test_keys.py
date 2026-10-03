"""Terminal input: SGR mouse reports and escape sequences split across reads."""
from __future__ import annotations

import os
import sys
import unittest
from typing import List

sys.dont_write_bytecode = True                                  # keep tools/ free of __pycache__
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools"))

from monitor.keys import Click, InputBuffer, split_mouse, strip_escapes


def feed_all(chunks: List[str], more: bool = True) -> List[str]:
    buf = InputBuffer()
    return [buf.feed(c, lambda: more) for c in chunks]


class SplitMouseTest(unittest.TestCase):
    def test_presses_are_clicks_releases_are_dropped(self) -> None:
        clicks, rest = split_mouse("\x1b[<0;12;4M\x1b[<0;12;4m")
        self.assertEqual(clicks, [Click(0, 12, 4)])
        self.assertEqual(rest, "")

    def test_wheel_and_keys_around_reports(self) -> None:
        clicks, rest = split_mouse("a\x1b[<64;40;10Mb\x1b[<65;1;2Mc")
        self.assertEqual(clicks, [Click(64, 40, 10), Click(65, 1, 2)])
        self.assertEqual(rest, "abc")

    def test_strip_escapes_leaves_plain_keys(self) -> None:
        self.assertEqual(strip_escapes("\x1b[Aq\x1b[5~1\x1b[F"), "q1")


class InputBufferTest(unittest.TestCase):
    def test_mouse_report_split_in_two_reads(self) -> None:
        first, second = feed_all(["\x1b[<0;12", ";4M"])
        self.assertEqual(first, "")                    # kept, not read as keys "0;12"
        self.assertEqual(second, "\x1b[<0;12;4M")

    def test_split_after_the_escape_byte(self) -> None:
        self.assertEqual(feed_all(["q\x1b", "[B"]), ["q", "\x1b[B"])

    def test_lone_escape_is_passed_on_when_nothing_follows(self) -> None:
        self.assertEqual(feed_all(["\x1b"], more=False), ["\x1b"])

    def test_unfinished_tail_is_passed_on_when_nothing_follows(self) -> None:
        self.assertEqual(feed_all(["x\x1b[<0;1"], more=False), ["x\x1b[<0;1"])

    def test_complete_input_passes_through(self) -> None:
        self.assertEqual(feed_all(["ab\x1b[A", "\x1b[<0;1;2M\x1b[<0;1;2m"]), ["ab\x1b[A", "\x1b[<0;1;2M\x1b[<0;1;2m"])

    def test_a_long_unfinished_tail_is_not_kept(self) -> None:
        junk = "\x1b[" + "1;" * 20
        self.assertEqual(feed_all([junk]), [junk])

    def test_more_coming_is_only_asked_for_an_unfinished_tail(self) -> None:
        asked: List[bool] = []
        buf = InputBuffer()
        buf.feed("abc", lambda: asked.append(True) or True)
        self.assertEqual(asked, [])


if __name__ == "__main__":
    unittest.main()
