# Project rules (notes)

- Every function that you add or change gets a docstring. Its first line starts with `Does:`, for example `"""Does: load the notes file."""`.
- Name every new test method `test_rule_` and then what it checks, for example `test_rule_empty_file`.
- After every code change, run the tests: `python3 -m unittest discover -s tests`.
