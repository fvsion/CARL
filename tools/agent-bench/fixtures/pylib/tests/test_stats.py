import unittest

from textstats.stats import mean_word_length, top_words, word_count


class StatsTest(unittest.TestCase):
    def test_word_count(self) -> None:
        self.assertEqual(word_count("The cat sat on the mat."), 6)

    def test_top_words(self) -> None:
        self.assertEqual(top_words("a b a c a b", 2), [("a", 3), ("b", 2)])
        self.assertEqual(top_words("b a", 5), [("a", 1), ("b", 1)])

    def test_mean_word_length(self) -> None:
        self.assertEqual(mean_word_length("The cat sat on the mat."), 2.8333333333333335)
