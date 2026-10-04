"""Model cards for custom models (Phase 5): the user's card is checked with the catalogue's
rules, stored in models.json, joined into the model record (lists, sort, filters, auto fit
read it), refused for catalogue models, and edited field by field from command-line text."""
from __future__ import annotations

import unittest
from typing import Callable, Dict, List, Tuple, cast

from support import GIB, MDIR, FakeShapes, World, catalog, entry, shape
from carl_core.domain import cards
from carl_core.domain.autofit import Budget, auto_fit, candidate
from carl_core.domain.errors import ConfigError
from carl_core.domain.records import CUSTOM_CARD_KEYS, parse_custom_card, parse_local_db
from carl_core.domain.settings import Config
from carl_core.domain.types import CustomCard, ModelInfo

BIG = entry("big", "Big-Q4.gguf", 20 * GIB, rank=2)
MINE = f"{MDIR}/Mine-Q4.gguf"
NAMES = {"big", "mine-q4"}
GOOD: Dict[str, object] = {
    "label": "Mine 7B", "role": "Fast local coder", "good_for": ["agent coding", "hard code"], "why_use": "Small.",
    "trade_offs": "Weaker on long tasks.", "hardware": "16 GB Macs", "arch": "moe", "quant": "Q4_K_M", "rank": 4,
    "thinking": "on-off", "pick_instead": [{"model": "big", "when": "harder code"}]}


def world(local: object = None, gpu: int = 48 * GIB) -> World:
    return World(catalog(BIG), files={MINE: 5 * GIB}, local=local, gpu=gpu,
                 shapes=FakeShapes(local={MINE: shape(experts=8)}, remote={"Big-Q4.gguf": shape()}))


def by_name(w: World) -> Dict[str, ModelInfo]:
    return {m["name"]: m for m in w.carl.all_models(Config())}


class ValidationTest(unittest.TestCase):
    def test_a_full_card_is_accepted(self) -> None:
        self.assertEqual(parse_custom_card(dict(GOOD), NAMES, "t", "mine-q4"), GOOD)

    def test_the_catalogue_rules_and_the_card_rules(self) -> None:
        bad: List[Dict[str, object]] = [
            {"good_for": ["poetry"]}, {"good_for": ["uncensored"]}, {"rank": 0}, {"rank": True}, {"rank": "1"},
            {"role": "x" * 61}, {"why_use": 3}, {"why_use": ""}, {"pick_instead": [{"model": "nope", "when": "x"}]},
            {"pick_instead": [{"model": "big"}]}, {"pick_instead": [{"model": "mine-q4", "when": "never"}]},
            {"arch": "MoE"}, {"thinking": "low"}, {"abliterated": "yes"}, {"colour": "red"},
            {"label": "x" * 61}, {"quant": "q" * 41}, {"role": "line\nbreak"}, {"why_use": "\x1b[2Jcleared"},
            {"uncensored": "no refusals"},                                      # the text needs abliterated
            {"auto_fit": True, "arch": "moe"},                                  # no rank
            {"auto_fit": True, "rank": 3},                                      # no arch
            {"auto_fit": True, "rank": 3, "arch": "moe", "abliterated": True},  # not stock
        ]
        for card in bad:
            with self.subTest(card=card), self.assertRaises(ConfigError):
                parse_custom_card(card, NAMES, "t", "mine-q4")

    def test_abliterated_models_may_be_uncensored(self) -> None:
        parse_custom_card({"abliterated": True, "good_for": ["uncensored"], "uncensored": "few refusals"}, NAMES, "t")

    def test_auto_fit_needs_rank_arch_and_stock(self) -> None:
        parse_custom_card({"auto_fit": True, "rank": 3, "arch": "dense"}, NAMES, "t")

    def test_models_json_loads_a_card_whose_alternative_is_gone(self) -> None:
        """A pick_instead model that was deleted later must not break the model list."""
        db = parse_local_db({"schema": 1, "models": {"mine-q4": {"path": MINE, "card": {
            "pick_instead": [{"model": "deleted-model", "when": "x"}]}}}}, "models.json")
        self.assertIn("card", db["models"]["mine-q4"])
        with self.assertRaisesRegex(ConfigError, "models.json: mine-q4: card: rank"):
            parse_local_db({"schema": 1, "models": {"mine-q4": {"card": {"rank": -1}}}}, "models.json")

    def test_fields_match_the_stored_keys(self) -> None:
        self.assertEqual(sorted(cards.FIELD), sorted(CUSTOM_CARD_KEYS))


class ParseValueTest(unittest.TestCase):
    def test_text_values(self) -> None:
        f = cards.field
        self.assertEqual(cards.parse_value(f("role"), ["Fast", "coder"]), "Fast coder")
        self.assertEqual(cards.parse_value(f("good_for"), ["agent coding, hard code", "agent coding"]),
                         ["agent coding", "hard code"])
        self.assertIs(cards.parse_value(f("abliterated"), ["Yes"]), True)
        self.assertIs(cards.parse_value(f("auto_fit"), ["off"]), False)
        self.assertEqual(cards.parse_value(f("arch"), ["MoE"]), "moe")
        self.assertEqual(cards.parse_value(f("rank"), ["4"]), 4)
        self.assertEqual(cards.parse_value(f("pick_instead"), ["big=harder code", "other = on 24 GB"]),
                         [{"model": "big", "when": "harder code"}, {"model": "other", "when": "on 24 GB"}])
        self.assertEqual(cards.parse_value(f("pick_instead"), ['[{"model": "big", "when": "x"}]']),
                         [{"model": "big", "when": "x"}])

    def test_bad_text_values(self) -> None:
        for key, args in (("good_for", ["poetry"]), ("abliterated", ["maybe"]), ("arch", ["hybrid"]),
                          ("rank", ["-1"]), ("rank", ["two"]), ("pick_instead", ["big"]),
                          ("pick_instead", ["[1, 2]"]), ("pick_instead", ["[not json"]), ("role", ["  "])):
            with self.subTest(key=key, args=args), self.assertRaises(ConfigError):
                cards.parse_value(cards.field(key), args)
        with self.assertRaisesRegex(ConfigError, "unknown card field 'colour'"):
            cards.field("colour")


class StoreAndMergeTest(unittest.TestCase):
    def test_round_trip_and_merge(self) -> None:
        w = world()
        saved = w.carl.save_card("mine-q4", dict(GOOD))
        self.assertEqual(saved, GOOD)
        entry_ = cast(Dict[str, object], w.local.doc)["models"]
        self.assertEqual(entry_, {"mine-q4": {"path": MINE, "source": "file", "card": GOOD}})   # path: stays custom
        m = by_name(w)["mine-q4"]
        for k, v in GOOD.items():
            self.assertEqual(m.get(k), v, k)                  # every reader sees the card's fields
        self.assertTrue(m["custom"])
        self.assertEqual(m["status"], "downloaded")

    def test_found_by_file_name_and_cleared(self) -> None:
        w = world()
        w.carl.set_card_field("Mine-Q4.gguf", "role", ["Fast", "coder"])
        self.assertEqual(by_name(w)["mine-q4"]["role"], "Fast coder")
        w.carl.unset_card_field("mine-q4", "role")
        self.assertNotIn("card", cast(Dict[str, Dict[str, Dict[str, object]]], w.local.doc)["models"]["mine-q4"])
        self.assertNotIn("role", by_name(w)["mine-q4"])

    def test_set_field_checks_the_whole_card(self) -> None:
        w = world()
        with self.assertRaisesRegex(ConfigError, "mine-q4: card: only abliterated models may be tagged uncensored"):
            w.carl.set_card_field("mine-q4", "good_for", ["uncensored"])
        w.carl.set_card_field("mine-q4", "abliterated", ["yes"])
        w.carl.set_card_field("mine-q4", "good_for", ["uncensored"])
        with self.assertRaisesRegex(ConfigError, "unknown model 'nope'"):
            w.carl.set_card_field("nope", "role", ["x"])
        with self.assertRaisesRegex(ConfigError, "pick_instead entries need a model CARL knows"):
            w.carl.set_card_field("mine-q4", "pick_instead", ["nope=x"])

    def test_catalogue_models_are_read_only(self) -> None:
        w = world()
        calls: Tuple[Callable[[], object], ...] = (lambda: w.carl.save_card("big", {"role": "x"}),
                                                    lambda: w.carl.set_card_field("big", "role", ["x"]),
                                                    lambda: w.carl.unset_card_field("big", "role"))
        for call in calls:
            with self.assertRaisesRegex(ConfigError, "big is a catalogue model: its card is read-only"):
                call()
        self.assertEqual(w.local.saved, [])

    def test_a_card_never_reaches_a_catalogue_model(self) -> None:
        """A card written into models.json by hand for a catalogue model is not used."""
        w = world(local={"schema": 1, "models": {"big": {"card": {"role": "hand edit"}}}})
        self.assertNotEqual(by_name(w)["big"].get("role"), "hand edit")

    def test_stale_alternatives_are_left_out(self) -> None:
        w = world(local={"schema": 1, "models": {"mine-q4": {"path": MINE, "card": {
            "role": "x", "pick_instead": [{"model": "gone", "when": "a"}, {"model": "big", "when": "b"}]}}}})
        self.assertEqual(by_name(w)["mine-q4"]["pick_instead"], [{"model": "big", "when": "b"}])
        w.carl.set_card_field("mine-q4", "rank", ["3"])         # an edit is not refused for the stale entry
        stored = cast(Dict[str, Dict[str, Dict[str, Dict[str, object]]]], w.local.doc)["models"]["mine-q4"]["card"]
        self.assertEqual(stored["pick_instead"], [{"model": "big", "when": "b"}])

    def test_describe(self) -> None:
        w = world()
        ms = by_name(w)
        self.assertEqual(cards.describe(ms["mine-q4"])[0], "mine-q4: custom model, no card yet")
        w.carl.set_card_field("mine-q4", "good_for", ["agent coding,hard code"])
        lines = cards.describe(by_name(w)["mine-q4"])
        self.assertEqual(lines[0], "mine-q4: custom model, your card (models.json)")
        self.assertIn("  good_for      agent coding, hard code", lines)
        self.assertIn("  rank          -", lines)
        self.assertEqual(cards.describe(ms["big"])[0], "big: catalogue model, read-only card (host/catalog.json)")


class AutoFitOptInTest(unittest.TestCase):
    """A custom model is an auto-fit candidate only when its card opts in (a user's rank is
    not measured), and the download offer never names one."""

    def test_rank_alone_is_not_enough(self) -> None:
        w = world()
        w.carl.save_card("mine-q4", {"rank": 1, "arch": "moe"})
        m = by_name(w)["mine-q4"]
        self.assertFalse(candidate(m, shape()).eligible)
        self.assertEqual(w.carl.auto_fit(w.carl.all_models(Config())).name, "big")

    def test_opted_in_custom_model_competes_on_rank(self) -> None:
        w = world()
        w.carl.save_card("mine-q4", {"rank": 1, "arch": "moe", "auto_fit": True})
        models = w.carl.all_models(Config())
        self.assertTrue(candidate(by_name(w)["mine-q4"], shape()).eligible)
        fit = w.carl.auto_fit(models)
        self.assertEqual(fit.name, "mine-q4")                   # rank 1 beats big's 2, and it fits
        m, _, note = w.carl.resolve_launch(None, Config())
        self.assertEqual((m["name"], note), ("mine-q4", None))
        self.assertEqual(w.carl.pick_default(models), "big")    # the download offer: catalogue models only

    def test_candidate_flag(self) -> None:
        card: CustomCard = {"rank": 1, "arch": "moe"}
        m = cast(ModelInfo, {"name": "c", "custom": True, "status": "downloaded", "bytes": GIB, **card})
        self.assertFalse(candidate(m, shape()).eligible)
        self.assertTrue(candidate(cast(ModelInfo, {**m, "auto_fit": True}), shape()).eligible)
        self.assertEqual(auto_fit([candidate(cast(ModelInfo, {**m, "auto_fit": True}), shape())],
                              Budget(24 * GIB, 0, 0)).name, "c")


if __name__ == "__main__":
    unittest.main()
