"""The shared units (tools/carl_core/domain/units.py; reference/glossary.md, section 9)."""
from __future__ import annotations

import unittest

import support  # noqa: F401  (puts tools/ on sys.path)
from carl_core.domain import units as u


class UnitsTest(unittest.TestCase):
    def test_files_are_1000_based(self) -> None:
        self.assertEqual(u.file_size(14_600_000_000), "14.6 GB")
        self.assertEqual(u.file_size(279_956_160), "280 MB")
        self.assertEqual(u.file_size(None), "–")

    def test_memory_is_1024_based(self) -> None:
        self.assertEqual(u.memory(14_600_000_000), "13.6 GiB")
        self.assertEqual(u.memory(512 * 1024 ** 2), "512 MiB")
        self.assertEqual(u.memory_pair(15.3 * 1024 ** 3, 25 * 1024 ** 3), "15.3 of 25.0 GiB")

    def test_tokens_k_is_1024(self) -> None:
        self.assertEqual(u.tokens(98304), "96K")
        self.assertEqual(u.tokens(53965), "52.7K")
        self.assertEqual(u.tokens(950), "950")

    def test_speed_time_percent(self) -> None:
        self.assertEqual(u.speed(534.2), "534 tok/s")
        self.assertEqual(u.speed(43.14), "43.1 tok/s")
        self.assertEqual(u.duration(1.23), "1.2 s")
        self.assertEqual(u.duration(42), "42 s")
        self.assertEqual(u.duration(185), "3 min")
        self.assertEqual(u.duration(4320), "1 h 12 min")
        self.assertEqual(u.duration(7200), "2 h")
        self.assertEqual(u.percent(0.79), "79%")


if __name__ == "__main__":
    unittest.main()
