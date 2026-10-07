# textstats

A small library for simple statistics on plain text.

```python
from textstats import words, sentences, word_count, top_words, mean_word_length, longest_word

word_count("The cat sat.")          # 3
top_words("a b a c a b", 2)         # [("a", 3), ("b", 2)]
```

## Layout

- `textstats/tokenize.py`: split text into words and sentences
- `textstats/stats.py`: counts and averages
- `tests/`: the tests

## Tests

    python3 -m pytest -q        # or: python3 -m unittest discover -s tests

Python 3.9 or newer, the standard library only.
