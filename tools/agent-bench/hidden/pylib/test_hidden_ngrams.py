"""Hidden check for the prompt medium-module (copied in only after the run)."""
import unittest

from textstats import ngrams as ng


class HiddenNgramsTest(unittest.TestCase):
    def test_ngrams(self) -> None:
        self.assertEqual(ng.ngrams("The cat sat on the mat.", 2),
                         [("the", "cat"), ("cat", "sat"), ("sat", "on"), ("on", "the"), ("the", "mat")])
        self.assertEqual(ng.ngrams("A b, c!", 3), [("a", "b", "c")])
        self.assertEqual(ng.ngrams("one two", 1), [("one",), ("two",)])

    def test_too_few_words(self) -> None:
        self.assertEqual(ng.ngrams("one", 2), [])
        self.assertEqual(ng.ngrams("", 1), [])

    def test_bad_n(self) -> None:
        for n in (0, -1):
            with self.assertRaises(ValueError):
                ng.ngrams("a b c", n)

    def test_top_ngrams(self) -> None:
        text = "a b a b a c"                                  # (a b) 2, (b a) 2, (a c) 1
        self.assertEqual(ng.top_ngrams(text, 2, 2), [(("a", "b"), 2), (("b", "a"), 2)])
        self.assertEqual(ng.top_ngrams(text, 2), [(("a", "b"), 2), (("b", "a"), 2), (("a", "c"), 1)])
        self.assertEqual(ng.top_ngrams("x y z", 1, k=2), [(("x",), 1), (("y",), 1)])
        self.assertEqual(ng.top_ngrams("one", 2), [])
