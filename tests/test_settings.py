"""config.json: value validation, warnings, migration from llama.env, key paths."""
from __future__ import annotations

import unittest

import support  # noqa: F401  (import path)
from carl_core.domain.errors import ConfigError
from carl_core.domain.settings import (CONFIG_COMMENT, LLAMA_KEYS, MODEL_KEYS, Config, coerce, get_path, key_path,
                                       migrate_env, models_dir_setting, set_path, unset_path, validate_config)
from carl_core.domain.types import JsonObject


class CoerceTest(unittest.TestCase):
    def test_k_suffix_and_numbers(self) -> None:
        self.assertEqual(coerce(MODEL_KEYS["ctx"], "64k", "ctx"), 65536)
        self.assertEqual(coerce(MODEL_KEYS["ctx"], 98304.0, "ctx"), 98304)
        self.assertEqual(coerce(MODEL_KEYS["temp"], "0.6", "temp"), 0.6)
        self.assertEqual(coerce(LLAMA_KEYS["cache_ram"], "auto", "c"), "auto")
        self.assertEqual(coerce(LLAMA_KEYS["cache_ram"], "4096", "c"), 4096)

    def test_bool_and_list(self) -> None:
        self.assertIs(coerce(LLAMA_KEYS["think_toggle"], "off", "t"), False)
        self.assertIs(coerce(LLAMA_KEYS["think_toggle"], "yes", "t"), True)
        self.assertEqual(coerce(LLAMA_KEYS["extra_args"], "--a 1", "x"), ["--a", "1"])
        self.assertEqual(coerce(LLAMA_KEYS["extra_args"], ["--b", 2], "x"), ["--b", "2"])

    def test_errors_name_the_setting(self) -> None:
        with self.assertRaisesRegex(ConfigError, r"models.x.kv: 'q5' is not one of q4_0, q8_0, f16"):
            coerce(MODEL_KEYS["kv"], "q5", "models.x.kv")
        with self.assertRaisesRegex(ConfigError, r"ctx: 1024 is out of range 4096..262144"):
            coerce(MODEL_KEYS["ctx"], 1024, "ctx")
        with self.assertRaisesRegex(ConfigError, r"ctx: 'lots' is not a valid int"):
            coerce(MODEL_KEYS["ctx"], "lots", "ctx")
        with self.assertRaisesRegex(ConfigError, r"is not a valid int"):
            coerce(MODEL_KEYS["ctx"], None, "ctx")


class ValidateConfigTest(unittest.TestCase):
    def test_valid_document(self) -> None:
        cfg, warn = validate_config({"schema": 1, "_comment": "x",
                                     "llama": {"model": "m", "net": "local", "ub": "1k"},
                                     "models": {"m.v2": {"ctx": "128k", "spec": "ngram-mod"}}})
        self.assertEqual(warn, [])
        self.assertEqual(cfg.llama, {"model": "m", "net": "local", "ub": 1024})
        self.assertEqual(cfg.profile("m.v2"), {"ctx": 131072, "spec": "ngram-mod"})
        self.assertEqual(cfg.to_json(), {"schema": 1,
                                         "llama": {"model": "m", "net": "local", "ub": 1024},
                                         "models": {"m.v2": {"ctx": 131072, "spec": "ngram-mod"}}})

    def test_unknown_keys_warn(self) -> None:
        _, warn = validate_config({"llama": {"nope": 1}, "extra": {}, "models": {"m": {"x": 1}}})
        self.assertEqual(warn, ["llama.nope: unknown setting (ignored)", "extra: unknown section (ignored)",
                                "models.m.x: unknown setting (ignored)"])

    def test_removed_mtplx_keys_load_and_are_dropped(self) -> None:
        """A config.json from before 1.2.0: backend (any value) is ignored silently, the
        mtplx section (even with values no longer valid anywhere) with one warning."""
        for backend in ("mtplx", "llama", "ollama", None, 3):
            with self.subTest(backend=backend):
                cfg, warn = validate_config({"schema": 1, "backend": backend, "llama": {"net": "vm"},
                                             "mtplx": {"preset": "grant", "context": "huge", "kv_quant": "q9"}})
                self.assertEqual(cfg.llama, {"net": "vm"})
                self.assertEqual(warn, ["mtplx: MTPLX support was removed in CARL 1.2.0 (ignored; dropped when "
                                        "the settings are next saved)"])
                self.assertEqual(cfg.to_file(), {"schema": 1, "_comment": CONFIG_COMMENT, "llama": {"net": "vm"}})
        _, warn = validate_config({"backend": "llama"})
        self.assertEqual(warn, [])

    def test_bad_documents(self) -> None:
        for raw, msg in (([], "must hold a JSON object"),
                         ({"llama": ["x"]}, "llama: must be a JSON object"),
                         ({"models": {"m": {"ctx": 10}}}, "models.m.ctx: 10 is out of range")):
            with self.subTest(raw=raw), self.assertRaisesRegex(ConfigError, msg):
                validate_config(raw)

    def test_file_form_has_comment_and_drops_empty_sections(self) -> None:
        doc = Config(models={"gone": {}}).to_file()
        self.assertEqual(doc, {"schema": 1, "_comment": CONFIG_COMMENT})


class MigrationTest(unittest.TestCase):
    def test_llama_env_maps_to_sections(self) -> None:
        cfg = migrate_env({"MODEL_NAME": "qwen", "NET": "vm", "CTX": "65536", "SPEC": "ngram-mod", "UNKNOWN": "1"})
        assert cfg is not None
        self.assertEqual(cfg.llama, {"model": "qwen", "net": "vm"})
        self.assertEqual(cfg.profile("qwen"), {"ctx": 65536, "spec": "ngram-mod"})

    def test_model_values_need_a_named_model(self) -> None:
        cfg = migrate_env({"CTX": "65536", "NET": "local"})
        assert cfg is not None
        self.assertEqual(cfg.models, {})
        self.assertEqual(cfg.llama, {"net": "local"})

    def test_nothing_or_invalid(self) -> None:
        self.assertIsNone(migrate_env({}))
        self.assertIsNone(migrate_env({"NET": "moon"}))


class KeyPathTest(unittest.TestCase):
    def test_model_names_with_dots(self) -> None:
        self.assertEqual(key_path("llama.net"), ["llama", "net"])
        self.assertEqual(key_path("models.qwen3.6-35b.ctx"), ["models", "qwen3.6-35b", "ctx"])
        with self.assertRaises(ConfigError):
            key_path("llama..net")

    def test_get_set_unset(self) -> None:
        doc: JsonObject = {"schema": 1, "note": "text"}
        set_path(doc, ["models", "m", "ctx"], "64k")
        self.assertEqual(get_path(doc, ["models", "m", "ctx"]), "64k")
        self.assertIsNone(get_path(doc, ["note", "x"]))
        with self.assertRaisesRegex(ConfigError, "note is not a section"):
            set_path(doc, ["note", "x"], 1)
        unset_path(doc, ["models", "m", "ctx"])
        unset_path(doc, ["nothing", "here"])
        self.assertEqual(doc["models"], {"m": {}})

    def test_models_dir_precedence(self) -> None:
        cfg = Config(paths={"models_dir": "/data/gguf"})
        self.assertEqual(models_dir_setting("/env/dir", cfg), "/env/dir")
        self.assertEqual(models_dir_setting(None, cfg), "/data/gguf")
        self.assertEqual(models_dir_setting(None, Config()), "~/models/gguf")


if __name__ == "__main__":
    unittest.main()
