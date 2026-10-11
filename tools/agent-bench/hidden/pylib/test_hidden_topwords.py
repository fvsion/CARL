"""Hidden check for the prompt medium-change (copied in only after the run)."""
import unittest

from textstats.stats import top_words


class HiddenTopWordsTest(unittest.TestCase):
    def test_old_calls_unchanged(self) -> None:
        self.assertEqual(top_words("a b a c a b", 2), [("a", 3), ("b", 2)])
        self.assertEqual(top_words("b a", 5), [("a", 1), ("b", 1)])
        self.assertEqual(len(top_words("a b c d e f g h i j k l")), 10)

    def test_stopwords_ignore_case(self) -> None:
        text = "The cat and the hat and the bat"
        self.assertEqual(top_words(text, stopwords=["The", "AND"]), [("bat", 1), ("cat", 1), ("hat", 1)])
        self.assertEqual(top_words(text, 1, stopwords={"the"}), [("and", 2)])

    def test_min_length(self) -> None:
        self.assertEqual(top_words("a bb a ccc bb a", min_length=2), [("bb", 2), ("ccc", 1)])
        self.assertEqual(top_words("a bb a ccc bb a", min_length=3), [("ccc", 1)])

    def test_both(self) -> None:
        self.assertEqual(top_words("I see the sea and the sea sees me", stopwords=("sea",), min_length=3),
                         [("the", 2), ("and", 1), ("see", 1), ("sees", 1)])

    def test_n_none_and_zero(self) -> None:
        text = "a b c d e f g h i j k l a"
        everything = top_words(text, None)
        self.assertEqual(len(everything), 12)
        self.assertEqual(everything[0], ("a", 2))
        self.assertEqual(top_words(text, 0), [])

    def test_negative_n(self) -> None:
        with self.assertRaises(ValueError):
            top_words("a b", -1)
