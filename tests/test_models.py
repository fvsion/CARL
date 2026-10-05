"""Models, settings precedence, context zones (with the 96K floor) and model selection."""
from __future__ import annotations

import unittest
from typing import Dict, List, cast

from support import GIB, HOME, MDIR, FakeFolder, catalog, entry, shape
from carl_core.domain import models as dm
from carl_core.domain.errors import ConfigError
from carl_core.domain.records import parse_catalog, parse_local_db
from carl_core.domain.settings import Config
from carl_core.domain.types import CatalogEntry, LocalDb, ModelInfo

BIG = entry("big", "Big-Q4.gguf", 20 * GIB)
SMALL = entry("small", "Small-IQ3.gguf", 10 * GIB, zones={"good": 65536, "slow": 98304, "very_slow": 131072})


def models(files: FakeFolder, local: object = None) -> List[ModelInfo]:
    return dm.build_models(parse_catalog(catalog(BIG, SMALL), "cat"), parse_local_db(local, "db"), MDIR, files, HOME)


class InventoryTest(unittest.TestCase):
    def test_catalogue_folder_and_custom_models(self) -> None:
        files = FakeFolder({f"{MDIR}/Small-IQ3.gguf": 10 * GIB, f"{MDIR}/Big-Q4.gguf.aria2": 1,
                            f"{MDIR}/My Model.v2.gguf": 5, f"{MDIR}/mmproj-x.gguf": 1,
                            f"{MDIR}/Split-00002-of-00003.gguf": 1, "/elsewhere/c.gguf": 7})
        local = {"schema": 1, "models": {"cust": {"path": "/elsewhere/c.gguf", "source": "hf",
                                                  "hf": {"repo": "o/r", "file": "c.gguf", "bytes": 7}},
                                         "small": {"tune": {"settings": {"ctx": 65536}}}}}
        ms = models(files, local)
        by = {m["name"]: m for m in ms}
        self.assertEqual([m["name"] for m in ms], ["big", "small", "cust", "my-model.v2"])
        self.assertEqual(by["big"]["status"], "partial")
        self.assertEqual(by["small"]["status"], "downloaded")
        self.assertEqual(by["small"]["local"], {"tune": {"settings": {"ctx": 65536}}})
        self.assertEqual(by["cust"]["status"], "downloaded")
        self.assertEqual(by["cust"]["summary"], "Custom model from Hugging Face")
        self.assertTrue(by["my-model.v2"]["custom"])
        self.assertEqual(by["my-model.v2"]["path"], f"{MDIR}/My Model.v2.gguf")

    def test_wrong_size_is_partial(self) -> None:
        files = FakeFolder({f"{MDIR}/Big-Q4.gguf": 1})
        self.assertEqual(dm.status_of(f"{MDIR}/Big-Q4.gguf", 20 * GIB, files), "partial")
        self.assertEqual(dm.status_of(f"{MDIR}/none.gguf", 20 * GIB, files), "missing")

    def test_find_by_name_file_or_path(self) -> None:
        ms = models(FakeFolder({f"{MDIR}/Small-IQ3.gguf": 10 * GIB}))
        self.assertEqual(cast(ModelInfo, dm.find_model(ms, "small", "small"))["name"], "small")
        self.assertEqual(cast(ModelInfo, dm.find_model(ms, "Small-IQ3.gguf", "x"))["name"], "small")
        self.assertEqual(cast(ModelInfo, dm.find_model(ms, "~/models/gguf/Big-Q4.gguf",
                                                       f"{MDIR}/Big-Q4.gguf"))["name"], "big")
        self.assertIsNone(dm.find_model(ms, "nope", "nope"))

    def test_bad_catalogue_entries_are_rejected(self) -> None:
        bad = entry("evil", "../../etc/x.gguf")
        with self.assertRaisesRegex(ConfigError, "not a file name"):
            parse_catalog(catalog(bad), "cat")
        with self.assertRaisesRegex(ConfigError, "missing or wrong schema"):
            parse_catalog(None, "cat")
        with self.assertRaisesRegex(ConfigError, "tune.kv"):
            parse_catalog(catalog(cast(CatalogEntry, dict(BIG, tune={"kv": "q5"}))), "cat")
        with self.assertRaisesRegex(ConfigError, "speed: needs prose"):
            parse_catalog(catalog(cast(CatalogEntry, dict(BIG, speed={"prose": "fast", "code": 1, "edit": 1,
                                                                       "machine": "M2"}))), "cat")
        ok = cast(CatalogEntry, dict(BIG, speed={"prose": 43.5, "code": 43.5, "edit": 117, "machine": "M3 Pro 36 GB"}))
        self.assertEqual(parse_catalog(catalog(ok), "cat")["models"][0]["speed"]["edit"], 117)


class EffectiveTuneTest(unittest.TestCase):
    def setUp(self) -> None:
        files = FakeFolder({f"{MDIR}/Small-IQ3.gguf": 10 * GIB, f"{MDIR}/custom.gguf": 3})
        local = {"schema": 1, "models": {"small": {"tune": {"settings": {"ctx": 131072, "spec": "ngram-mod",
                                                                         "spec_n": 1, "slots": "2"}}}}}
        self.ms = {m["name"]: m for m in models(files, local)}

    def test_precedence_config_over_autotune_over_catalogue(self) -> None:
        cfg = Config(models={"small": {"ctx": 65536, "temp": 0.6}})
        vals, src = dm.effective_tune(self.ms["small"], cfg)
        self.assertEqual((vals["ctx"], src["ctx"]), (65536, "config"))
        self.assertEqual((vals["temp"], src["temp"]), (0.6, "config"))
        self.assertEqual((vals["spec"], src["spec"]), ("ngram-mod", "auto-tune"))
        self.assertEqual((vals["kv"], src["kv"]), ("q4_0", "catalogue"))
        self.assertEqual((vals["repeat"], src["repeat"]), (1.0, "default"))
        self.assertEqual((vals["alias"], src["alias"]), ("small", "default"))   # the model's alias fills an empty one

    def test_custom_model_starts_from_its_header(self) -> None:
        header = dm.custom_defaults(shape(experts=0, nextn=0, ctx_train=40960))[0]
        vals, src = dm.effective_tune(self.ms["custom"], Config(), header)
        self.assertEqual((vals["spec"], vals["spec_n"], src["spec"]), ("ngram-mod", 2, "header"))
        self.assertEqual(vals["ctx"], 40960)
        self.assertEqual(vals["alias"], "custom")


class CustomDefaultsTest(unittest.TestCase):
    def test_96k_for_dense_and_moe_capped_by_training(self) -> None:
        tune, info = dm.custom_defaults(shape(experts=0, nextn=1))
        self.assertEqual((tune["ctx"], tune["spec"], tune["spec_n"]), (98304, "draft-mtp,ngram-mod", 1))
        self.assertEqual(info, {"arch": "dense", "mtp": True, "quant": "Q4_K_M", "ctx_train": 262144, "thinking": "on-off"})
        self.assertEqual(dm.custom_defaults(shape(experts=128))[0]["ctx"], 98304)
        self.assertEqual(dm.custom_defaults(shape(ctx_train=32768))[0]["ctx"], 32768)
        self.assertEqual(dm.custom_defaults(shape(ctx_train=0))[0]["ctx"], 98304)

    def test_unreadable_header(self) -> None:
        gem = dm.custom_defaults({**shape(experts=0, nextn=0), "arch": "gemma4"}, drafter=True)[0]
        self.assertEqual((gem["spec"], gem["spec_n"]), ("draft-mtp,ngram-mod", 2))   # a custom Gemma 4 with its drafter
        self.assertEqual((gem["temp"], gem["top_p"], gem["top_k"]), (1.0, 0.95, 64))   # Google's sampling
        tune, info = dm.custom_defaults(None)
        self.assertEqual((tune["spec"], tune["spec_n"], tune["ctx"]), ("ngram-mod", 2, 98304))
        self.assertEqual(info, {"arch": "dense", "mtp": False, "quant": "?"})


class ContextZonesTest(unittest.TestCase):
    def model(self, zones: object = None, measured: object = None) -> ModelInfo:
        raw: Dict[str, object] = {"name": "m", "ctx_zones": zones}
        if measured:
            raw["local"] = {"tune": {"ctx_zones": measured}}
        return cast(ModelInfo, raw)

    def test_96k_is_good_even_when_measured_slower(self) -> None:
        m = self.model(measured={"good": 69632, "slow": 151552, "very_slow": 233472})
        self.assertEqual(dm.ctx_zones(m), (98304, 151552, 233472))
        self.assertEqual(dm.ctx_zone(m, 98304), "good")
        self.assertEqual(dm.ctx_zone(m, 98305), "slow")
        self.assertEqual(dm.ctx_zone(m, 240000), "very_slow")

    def test_slow_zones_stay_above_good(self) -> None:
        m = self.model(zones={"good": 32768, "slow": 49152, "very_slow": 65536})
        self.assertEqual(dm.ctx_zones(m), (98304, 98304, 98304))
        self.assertEqual(dm.ctx_zone(m, 131072), "very_slow")

    def test_measured_wins_and_default(self) -> None:
        m = self.model(zones={"good": 98304, "slow": 131072, "very_slow": 163840},
                       measured={"good": 131072, "slow": 196608, "very_slow": 262144})
        self.assertEqual(dm.ctx_zones(m), (131072, 196608, 262144))
        self.assertEqual(dm.ctx_zones(self.model()), (98304, 131072, 163840))


class SelectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ms = models(FakeFolder({f"{MDIR}/Small-IQ3.gguf": 10 * GIB}))

    def test_named(self) -> None:
        self.assertEqual(dm.select_named(self.ms, "small", "small")["name"], "small")
        with self.assertRaisesRegex(ConfigError, "unknown model 'x'"):
            dm.select_named(self.ms, "x", "x")
        with self.assertRaisesRegex(ConfigError, r"big is not downloaded. To download it: ./carl.sh download big"):
            dm.select_named(self.ms, "big", "big")

    def test_configured_model(self) -> None:
        self.assertIsNone(dm.configured_model(Config(llama={"model": "auto"})))
        self.assertEqual(dm.configured_model(Config(llama={"model": "small"})), "small")

    def test_local_db_validation(self) -> None:
        db: LocalDb = parse_local_db(None, "db")
        self.assertEqual(db, {"schema": 1, "models": {}})
        with self.assertRaisesRegex(ConfigError, "tune.settings.ctx"):
            parse_local_db({"models": {"m": {"tune": {"settings": {"ctx": "huge"}}}}}, "db")
        with self.assertRaisesRegex(ConfigError, "not a Hugging Face repo"):
            parse_local_db({"models": {"m": {"hf": {"repo": "../x"}}}}, "db")


if __name__ == "__main__":
    unittest.main()


class TuneAdviceTest(unittest.TestCase):
    """A catalogue window below the 96K floor (a build sized for small Macs) asks for a tune."""

    def model(self, tune: Dict[str, object] | None = None) -> ModelInfo:
        return cast(ModelInfo, {"name": "orcarouter-27b-q3", "tune": {"ctx": 65536},
                                "local": {"tune": tune} if tune else {}})

    def test_catalogue_window_below_floor_gets_advice(self) -> None:
        m = self.model()
        vals, src = dm.effective_tune(m, Config())
        note = dm.tune_advice(m, vals, src)
        self.assertIsNotNone(note)
        self.assertIn("./carl.sh tune orcarouter-27b-q3", note or "")

    def test_no_advice_after_auto_tune_or_user_choice(self) -> None:
        m = self.model({"settings": {"ctx": 65536}})
        self.assertIsNone(dm.tune_advice(m, *dm.effective_tune(m, Config())))
        m = self.model()
        vals, src = dm.effective_tune(m, Config())
        src["ctx"] = "config"
        self.assertIsNone(dm.tune_advice(m, vals, src))

    def test_no_advice_at_or_above_floor(self) -> None:
        m = cast(ModelInfo, {"name": "x", "tune": {"ctx": 98304}, "local": {}})
        self.assertIsNone(dm.tune_advice(m, *dm.effective_tune(m, Config())))


class CatalogueCardTest(unittest.TestCase):
    """The model card fields in host/catalog.json are checked when the catalogue loads."""

    def doc(self, **card: object) -> Dict[str, object]:
        base = {"name": "a", "abliterated": False,
                "hf": {"repo": "o/r", "file": "a.gguf", "bytes": 1, "revision": "0" * 40, "sha256": "0" * 64}}
        other = {"name": "b", "hf": {"repo": "o/r", "file": "b.gguf", "bytes": 1, "revision": "0" * 40, "sha256": "0" * 64}}
        return {"schema": 1, "default": "a", "models": [{**base, **card}, other]}

    def test_good_card_loads(self) -> None:
        parse_catalog(self.doc(role="Everyday agent coding", good_for=["agent coding", "hard code"], why_use="x",
                               trade_offs="y", hardware="32 GB", rank=3,
                               pick_instead=[{"model": "b", "when": "on 24 GB"}]), "t")

    def test_bad_cards_are_refused(self) -> None:
        for card in ({"good_for": ["poetry"]}, {"good_for": ["uncensored"]}, {"rank": 0}, {"rank": True},
                     {"role": "x" * 61}, {"why_use": 3}, {"pick_instead": [{"model": "nope", "when": "x"}]},
                     {"pick_instead": [{"model": "b"}]}):
            with self.subTest(card=card), self.assertRaises(ConfigError):
                parse_catalog(self.doc(**card), "t")

    def test_uncensored_tag_on_abliterated_model(self) -> None:
        parse_catalog(self.doc(abliterated=True, good_for=["uncensored"], uncensored="what it means"), "t")
