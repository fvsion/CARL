"""The llama.cpp version check (tools/carl_core/domain/llamacpp.py): the parse of `llama-server --version`, the
comparison with the tested version, the start line and the HEALTH card's rows. Made-up version strings only."""
from __future__ import annotations

import unittest

import support  # noqa: F401  (puts tools/ on sys.path)
from carl_core.domain import llamacpp as lc

V060 = "version: 0.6.0 (build 11429, commit d81235049)\nbuilt with AppleClang 21.0.0.21000334 for Darwin arm64\n"
TESTED = lc.LlamaVersion((0, 6, 0), 11429)


class ParseTest(unittest.TestCase):
    def test_the_0x_format(self) -> None:
        self.assertEqual(lc.parse_version(V060), lc.LlamaVersion((0, 6, 0), 11429))
        self.assertEqual(lc.parse_version("load_backend: x\nversion: 0.4.1 (build 9001, commit abc)"),
                         lc.LlamaVersion((0, 4, 1), 9001))
        self.assertEqual(lc.parse_version("version: 1.2 (commit abc)"), lc.LlamaVersion((1, 2), None))

    def test_the_old_format_is_a_build_number(self) -> None:
        self.assertEqual(lc.parse_version("version: 6789 (bc091a4d)"), lc.LlamaVersion((), 6789))
        self.assertEqual(lc.parse_version("version: b5124"), lc.LlamaVersion((), 5124))

    def test_nothing_to_read(self) -> None:
        for text in ("", "?", "built with clang", "version: unknown (x)", "Version 0.6.0"):
            with self.subTest(text=text):
                self.assertIsNone(lc.parse_version(text))

    def test_text(self) -> None:
        self.assertEqual(TESTED.text(), "0.6.0 (build 11429)")
        self.assertEqual(TESTED.text(inner=True), "0.6.0, build 11429")
        self.assertEqual(lc.LlamaVersion((), 6789).text(), "build 6789")
        self.assertEqual(lc.LlamaVersion((0, 7)).text(), "0.7")


class CompareTest(unittest.TestCase):
    def test_by_build_when_both_have_one(self) -> None:
        self.assertEqual(lc.compare(lc.LlamaVersion((0, 6, 0), 11400), TESTED), -1)
        self.assertEqual(lc.compare(lc.LlamaVersion((0, 6, 0), 11429), TESTED), 0)
        self.assertEqual(lc.compare(lc.LlamaVersion((0, 6, 0), 11500), TESTED), 1)      # a newer build of 0.6.0
        self.assertEqual(lc.compare(lc.LlamaVersion((), 6789), TESTED), -1)              # the old format
        self.assertEqual(lc.compare(lc.LlamaVersion((9, 0), 100), TESTED), -1)           # the build decides

    def test_else_by_the_dotted_version(self) -> None:
        self.assertEqual(lc.compare(lc.LlamaVersion((0, 5, 9)), TESTED), -1)
        self.assertEqual(lc.compare(lc.LlamaVersion((0, 6)), TESTED), 0)                  # 0.6 = 0.6.0
        self.assertEqual(lc.compare(lc.LlamaVersion((0, 10, 0)), TESTED), 1)              # numbers, not text
        self.assertIsNone(lc.compare(lc.LlamaVersion((), 7000), lc.LlamaVersion((0, 6))))

    def test_the_tested_version_is_the_installed_one_here(self) -> None:
        """The one tested version: 0.6.0, build 11429 (both test Macs since 2026-10-09)."""
        self.assertEqual(lc.TESTED, TESTED)
        self.assertEqual(lc.check_version(V060).state, "same")


class CheckTest(unittest.TestCase):
    def test_states(self) -> None:
        self.assertEqual(lc.check_version("version: 0.4.1 (build 9001, commit a)").state, "older")
        self.assertEqual(lc.check_version("version: 0.7.0 (build 12001, commit a)").state, "newer")
        self.assertEqual(lc.check_version("?").state, "unknown")
        self.assertEqual(lc.check_version("").state, "unknown")

    def test_the_start_line_only_when_older(self) -> None:
        older = lc.check_version("version: 0.4.1 (build 9001, commit a)")
        self.assertEqual(lc.start_line(older), "llama.cpp 0.4.1 (build 9001) is older than the version that CARL is "
                                               "tested with (0.6.0, build 11429): update it with brew upgrade llama.cpp.")
        for text in (V060, "version: 0.7.0 (build 12001, commit a)", "?"):
            self.assertEqual(lc.start_line(lc.check_version(text)), "")

    def test_health_rows(self) -> None:
        older = lc.check_version("version: 6789 (bc091a4d)")
        for full in (False, True):
            rows = lc.health_rows(older, full)
            self.assertEqual(rows, (("version", "llama.cpp build 6789 is older than the tested version, 0.6.0 (build 11429).",
                                     True),
                                    ("to update", "Run brew upgrade llama.cpp, then restart the server.", False)))
        newer = lc.check_version("version: 0.7.0 (build 12001, commit a)")
        self.assertEqual(lc.health_rows(newer, False), ())                       # a quiet note at full detail only
        self.assertEqual(lc.health_rows(newer, True),
                         (("version", "llama.cpp 0.7.0 (build 12001) is newer than the tested version, 0.6.0 (build 11429).",
                           False),))
        self.assertEqual(lc.health_rows(lc.check_version(V060), True),
                         (("version", "llama.cpp 0.6.0 (build 11429), the tested version.", False),))
        self.assertEqual(lc.health_rows(lc.check_version("?"), True), ())
        for _, text, _ in lc.health_rows(older, True) + lc.health_rows(newer, True):
            self.assertNotIn(" · ", text)
            self.assertTrue(text.endswith("."))


if __name__ == "__main__":
    unittest.main()
