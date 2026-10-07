"""textstats: simple statistics on plain text."""
from textstats.stats import longest_word, mean_word_length, top_words, word_count
from textstats.tokenize import sentences, words

__all__ = ["words", "sentences", "word_count", "top_words", "mean_word_length", "longest_word"]
__version__ = "0.1.0"
