"""Sorting and filtering of the model lists (tools/monitor/arrange.py)."""
from __future__ import annotations

import unittest
from typing import Dict, List, Optional, cast

import mon_support  # noqa: F401  (puts tools/ on sys.path)
from monitor.arrange import FILTERS, SORTS, arrange, keep, label
from monitor.model import ModelInfo


def model(name: str, rank: Optional[int], arch: str, size: int, status: str = "downloaded",
          tags: Optional[List[str]] = None, abliterated: bool = False) -> ModelInfo:
    m: Dict[str, object] = {"name": name, "arch": arch, "bytes": size, "status": status,
                            "good_for": tags or [], "abliterated": abliterated}
    if rank is not None:
        m["rank"] = rank
    return cast(ModelInfo, m)


MODELS = [
    model("a3b-q4", 5, "moe", 22, "missing", ["agent coding", "chat & writing"]),
    model("27b-q4", 2, "dense", 16, "missing", ["hard code", "agent coding"]),
    model("a3b-iq3", 7, "moe", 14, "downloaded", ["agent coding", "chat & writing"]),
    model("orca-iq3", 6, "dense", 12, "downloaded", ["uncensored", "hard code"], abliterated=True),
    model("custom", None, "dense", 9, "downloaded"),
]
FITS = {"a3b-iq3": 262144, "orca-iq3": 98304, "custom": 16384}


def names(sort: str, filt: str = "all") -> List[str]:
    return [m["name"] for m in arrange(MODELS, SORTS.index(sort), FILTERS.index(filt), lambda m: FITS.get(m["name"]))]


class SortTest(unittest.TestCase):
    def test_quality_is_rank_order_unranked_last(self) -> None:
        self.assertEqual(names("quality"), ["27b-q4", "a3b-q4", "orca-iq3", "a3b-iq3", "custom"])

    def test_speed_puts_moe_first_then_smaller_files(self) -> None:
        self.assertEqual(names("speed"), ["a3b-iq3", "a3b-q4", "custom", "orca-iq3", "27b-q4"])

    def test_downloaded_first_then_quality(self) -> None:
        self.assertEqual(names("downloaded first"), ["orca-iq3", "a3b-iq3", "custom", "27b-q4", "a3b-q4"])

    def test_size_and_name(self) -> None:
        self.assertEqual(names("size")[0], "custom")
        self.assertEqual(names("name"), sorted(m["name"] for m in MODELS))


class FilterTest(unittest.TestCase):
    def test_use_case_tags(self) -> None:
        self.assertEqual(set(names("quality", "hard code")), {"27b-q4", "orca-iq3"})
        self.assertEqual(names("quality", "uncensored"), ["orca-iq3"])

    def test_stock_dense_moe_downloaded(self) -> None:
        self.assertNotIn("orca-iq3", names("quality", "stock"))
        self.assertEqual(set(names("quality", "MoE")), {"a3b-q4", "a3b-iq3"})
        self.assertEqual(set(names("quality", "dense")), {"27b-q4", "orca-iq3", "custom"})
        self.assertEqual(set(names("quality", "downloaded")), {"a3b-iq3", "orca-iq3", "custom"})

    def test_fits_needs_a_known_window_of_32k(self) -> None:
        self.assertEqual(set(names("quality", "fits this Mac")), {"a3b-iq3", "orca-iq3"})
        self.assertFalse(keep("fits this Mac", MODELS[0], lambda m: None))

    def test_indexes_wrap_and_label(self) -> None:
        self.assertEqual(label(len(SORTS), len(FILTERS)), (SORTS[0], FILTERS[0]))


if __name__ == "__main__":
    unittest.main()
