"""Hidden check for the prompt large-module (copied in only after the run)."""
import unittest

from textstats import readability as r


class HiddenReadabilityTest(unittest.TestCase):
    def test_syllables(self) -> None:
        for word, n in (("cat", 1), ("banana", 3), ("make", 1), ("python", 2), ("a", 1)):
            self.assertEqual(r.syllable_count(word), n, word)

    def test_sentences(self) -> None:
        self.assertEqual(r.sentence_count("Hi. How are you? Fine!"), 3)
        self.assertEqual(r.sentence_count(""), 0)

    def test_flesch(self) -> None:
        self.assertEqual(r.flesch_reading_ease(""), 0.0)
        self.assertAlmostEqual(r.flesch_reading_ease("The cat sat on the mat."), 116.145, delta=0.011)

    def test_exported(self) -> None:
        import textstats
        for name in ("syllable_count", "sentence_count", "flesch_reading_ease"):
            self.assertTrue(hasattr(textstats, name), name)
