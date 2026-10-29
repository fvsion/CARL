"""The dashboard calls tools/carl.py through an untyped module handle (store.CarlStore._carl), so neither
mypy nor a search for callers sees those uses: every name it uses must exist in carl.py (a dead-code pass once
removed resolve_launch, and the dashboard then showed the wrong model for "auto")."""
from __future__ import annotations

import glob
import os
import re
import unittest

import mon_support  # noqa: F401  (puts tools/ on sys.path)
import carl

MONITOR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools", "monitor")


class CarlLinkTest(unittest.TestCase):
    def test_every_name_the_dashboard_uses_exists(self) -> None:
        used = set()
        for path in glob.glob(os.path.join(MONITOR, "**", "*.py"), recursive=True):
            with open(path, encoding="utf-8") as f:
                used |= set(re.findall(r"\b_carl\.(\w+)", f.read()))
        self.assertIn("resolve_launch", used)
        self.assertEqual(sorted(n for n in used if not hasattr(carl, n)), [])


if __name__ == "__main__":
    unittest.main()
