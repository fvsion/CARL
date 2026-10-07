import unittest

from textstats.tokenize import sentences, words


class TokenizeTest(unittest.TestCase):
    def test_words(self) -> None:
        self.assertEqual(words("The cat's hat, 2 times!"), ["the", "cat's", "hat", "2", "times"])

    def test_sentences(self) -> None:
        self.assertEqual(sentences("Hi. How are you?  Fine!"), ["Hi.", "How are you?", "Fine!"])
