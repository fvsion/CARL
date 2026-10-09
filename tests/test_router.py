"""Router mode and the client model list (domain + application): each downloaded model's
preset section is what a single start of it would run, a model that doesn't fit is left out
with the reason, the start model loads first; the clients list only the installed models."""
from __future__ import annotations

import unittest
from typing import Dict, List, cast

from support import GIB, MDIR, FakeShapes, World, catalog, entry, shape, with_window
from carl_core.domain.clientlist import client_entry, client_list
from carl_core.domain.router import Common, Preset, plan_model, preset_ini
from carl_core.domain.settings import LLAMA_KEYS
from carl_core.domain.types import JsonObject, JsonValue, ModelInfo, Settings

COMMON = Common(batch=2048, ubatch=512, ckpt=8, ckpt_step=4096, cache_ram=None)
VALS: Settings = {"ctx": 98304, "slots": "auto", "kv": "q4_0", "spec": "draft-mtp,ngram-mod", "spec_n": 1, "temp": 1.0,
        "top_p": 0.95, "top_k": 20, "min_p": 0, "presence": 0, "repeat": 1.0}
SMALL = entry("small", "Small-IQ3.gguf", 10 * GIB, rank=2)
BIG = entry("big", "Big-Q4.gguf", 30 * GIB, rank=1)


class PlanTest(unittest.TestCase):
    def test_two_slots_when_they_fit_and_the_cache_from_free_ram(self) -> None:
        p, why = plan_model("m", "/m.gguf", VALS, shape(), 10 * GIB, 24 * GIB, 32 * GIB, 6 * GIB, COMMON, "/t.jinja")
        assert p is not None, why
        self.assertEqual((p.slots, p.ctx, p.kv, p.label()), (2, 98304, "q4_0", "2 × 96K q4_0"))
        self.assertEqual(p.cache_mib, 8192)                              # 32 - ~11 - 6 GiB, capped at 8 GiB
        fixed = Common(2048, 512, 8, 4096, cache_ram=2048)
        self.assertEqual(plan_model("m", "/m.gguf", VALS, shape(), 10 * GIB, 24 * GIB, 32 * GIB, 6 * GIB, fixed,
                                    None)[0].cache_mib, 2048)            # type: ignore[union-attr]

    def test_a_model_that_does_not_fit_is_left_out_with_the_reason(self) -> None:
        p, why = plan_model("m", "/m.gguf", {**VALS, "slots": "1"}, shape(), 30 * GIB, 24 * GIB, 32 * GIB, 6 * GIB,
                            COMMON, None)
        self.assertIsNone(p)
        self.assertIn("The weights alone do not fit.", why)

    def test_a_sliding_window_model_gets_swa_full_when_it_fits(self) -> None:
        swa = with_window(shape(), 512, 32768)                                    # 18 KiB per token at full length
        p, why = plan_model("m", "/m.gguf", VALS, swa, GIB, 24 * GIB, 32 * GIB, 6 * GIB, COMMON, None)
        assert p is not None, why
        self.assertIn("swa-full = true", preset_ini(Preset(models=[p]), COMMON))
        tight, why = plan_model("m", "/m.gguf", VALS, swa, GIB, 6 * GIB, 32 * GIB, 6 * GIB, COMMON, None)
        assert tight is not None, why                                                # fits only with the window
        self.assertNotIn("swa-full", preset_ini(Preset(models=[tight]), COMMON))
        window = Common(2048, 512, 8, 4096, cache_ram=None, swa_mode="window")
        w, _ = plan_model("m", "/m.gguf", VALS, swa, GIB, 24 * GIB, 32 * GIB, 6 * GIB, window, None)
        assert w is not None
        self.assertNotIn("swa-full", preset_ini(Preset(models=[w]), window))
        q, _ = plan_model("m", "/m.gguf", VALS, shape(), GIB, 24 * GIB, 32 * GIB, 6 * GIB, COMMON, None)
        assert q is not None
        self.assertNotIn("swa-full", preset_ini(Preset(models=[q]), COMMON))

    def test_a_path_with_a_line_break_is_refused(self) -> None:
        from carl_core.domain.errors import ConfigError
        with self.assertRaises(ConfigError):
            plan_model("m", "/m\n[x].gguf", VALS, shape(), GIB, 24 * GIB, 32 * GIB, 6 * GIB, COMMON, None)

    def test_ini(self) -> None:
        two, _ = plan_model("two", "/two.gguf", VALS, shape(), 10 * GIB, 24 * GIB, 32 * GIB, 6 * GIB, COMMON, "/t.jinja")
        one, _ = plan_model("one", "/one.gguf", {**VALS, "slots": "1", "spec": "none"}, shape(), GIB, 24 * GIB, 32 * GIB,
                            6 * GIB, COMMON, None)
        assert two and one
        text = preset_ini(Preset([two, one], [("big", "needs 40.0 GiB")], start="one"), COMMON)
        self.assertIn("version = 1\n\n[*]\njinja = true\n", text)
        self.assertIn('chat-template-kwargs = {"preserve_thinking":true}', text)
        shared = text.split("[*]\n", 1)[1].split("\n\n", 1)[0]               # every model the router starts
        self.assertIn("fit = off\n", shared + "\n")                          # llama.cpp 0.6 fits by default
        self.assertNotIn("verbosity", text)                                   # no -lv 4: no buffer sizes read
        sec_two = text.split("[two]")[1].split("[one]")[0]
        sec_one = text.split("[one]")[1]
        for line in ("model = /two.gguf", "ctx-size = 196608", "parallel = 2", "kv-unified = true",
                     "kv-unified-per-slot = 98304", "cache-idle-slots = false", "spec-type = draft-mtp,ngram-mod",
                     "chat-template-file = /t.jinja", "temp = 1", "top-p = 0.95"):
            self.assertIn(line + "\n", sec_two)
        self.assertNotIn("load-on-startup", sec_two)
        self.assertIn("parallel = 1\n", sec_one)
        self.assertNotIn("kv-unified", sec_one)
        self.assertNotIn("spec-type", sec_one)                           # speculation off: no flag
        self.assertIn("load-on-startup = true", sec_one)
        self.assertIn(";   big: needs 40.0 GiB", text)


class AppRouterTest(unittest.TestCase):
    def world(self, **kw: object) -> World:
        files = {f"{MDIR}/Small-IQ3.gguf": 10 * GIB, f"{MDIR}/Big-Q4.gguf": 30 * GIB,
                 "/home/u/models/templates/Small-IQ3.thinking-toggle.jinja": 10}
        shapes = FakeShapes(local={f"{MDIR}/Small-IQ3.gguf": shape(), f"{MDIR}/Big-Q4.gguf": shape()})
        return World(catalog(BIG, SMALL), files=files, shapes=shapes, **kw)    # type: ignore[arg-type]

    def test_downloaded_models_that_fit_and_the_start_model(self) -> None:
        w = self.world()
        preset, common = w.carl.router_preset(w.carl.load_config(), "/home/u/models/templates")
        self.assertEqual([p.name for p in preset.models], ["small"])
        self.assertEqual([n for n, _ in preset.skipped], ["big"])            # 30 GiB on a 24 GiB GPU
        self.assertEqual(preset.start, "small")
        self.assertEqual(preset.models[0].template, "/home/u/models/templates/Small-IQ3.thinking-toggle.jinja")
        self.assertEqual((common.batch, common.ubatch, common.cache_ram), (2048, 512, None))

    def test_config_values_reach_the_preset(self) -> None:
        w = self.world(config={"schema": 1, "llama": {"ub": 1024, "cache_ram": 3072, "think_toggle": False},
                               "models": {"small": {"ctx": 65536, "slots": "1"}}})
        preset, common = w.carl.router_preset(w.carl.load_config(), "/home/u/models/templates")
        p = preset.models[0]
        self.assertEqual((p.ctx, p.slots, p.template, p.cache_mib, common.ubatch), (65536, 1, None, 3072, 1024))

    def test_mode_setting(self) -> None:
        self.assertEqual((LLAMA_KEYS["mode"].default, LLAMA_KEYS["mode"].choices), ("single", ("single", "router")))
        self.assertEqual(LLAMA_KEYS["mode"].env, "LLAMA_MODE")


class ClientListTest(unittest.TestCase):
    def test_a_custom_models_thinking_comes_from_its_header(self) -> None:
        sh = shape()
        sh["effort_levels"] = True
        w = World(catalog(BIG, SMALL), files={f"{MDIR}/mine.gguf": GIB}, shapes=FakeShapes(local={f"{MDIR}/mine.gguf": sh}))
        doc = w.carl.client_models(w.carl.load_config())
        self.assertEqual([(m["id"], m["thinking"]) for m in cast(List[Dict[str, JsonValue]], doc["models"])], [("mine", "effort")])

    def test_only_downloaded_models(self) -> None:
        ms: list[ModelInfo] = [{"name": "a", "label": "A · Q4", "status": "downloaded", "thinking": "effort",
                                "family": "qwen3.8-27b"},
                               {"name": "b", "status": "missing"},
                               {"name": "c", "label": "C\x1b[2J", "status": "downloaded"}]
        doc = client_list(ms, lambda m: 65536, default="b")
        self.assertEqual(doc["default"], None)                               # not installed: no default
        self.assertEqual(doc["models"], [{"id": "a", "label": "A · Q4", "ctx": 65536, "thinking": "effort",
                                          "off_sampling": "qwen",                  # a Qwen family: Qwen's off sampling
                                          "thinking_main": "low", "thinking_coder": "main"},
                                         {"id": "c", "label": "C[2J", "ctx": 65536, "thinking": "on-off",
                                          "off_sampling": "same", "thinking_main": "on", "thinking_coder": "main"}])
        self.assertEqual(client_entry({"name": "x"}, 4096)["label"], "x")

    def test_app_client_models(self) -> None:
        w = World(catalog(BIG, SMALL), files={f"{MDIR}/Small-IQ3.gguf": 10 * GIB},
                  shapes=FakeShapes(local={f"{MDIR}/Small-IQ3.gguf": shape()}),
                  config={"schema": 1, "models": {"small": {"ctx": 131072}}})
        doc = w.carl.client_models(w.carl.load_config())
        self.assertEqual(doc["default"], "small")
        self.assertEqual(doc["models"], [{"id": "small", "label": "small", "ctx": 131072, "thinking": "on-off",
                                          "off_sampling": "same", "thinking_main": "on", "thinking_coder": "main"}])

    def test_the_thinking_of_each_role_comes_from_the_models_settings(self) -> None:
        """Phase 23.4.4: config.json models.NAME.thinking_main / thinking_coder reach the client list, as the model
        takes them (a level on an on / off model is on; on for an effort model is the role's default level)."""
        sh = shape()
        sh["effort_levels"] = True
        w = World(catalog(BIG, SMALL), files={f"{MDIR}/Small-IQ3.gguf": 10 * GIB, f"{MDIR}/mine.gguf": GIB},
                  shapes=FakeShapes(local={f"{MDIR}/Small-IQ3.gguf": shape(), f"{MDIR}/mine.gguf": sh}),
                  config={"schema": 1, "models": {"small": {"thinking_main": "off", "thinking_coder": "xhigh"},
                                                  "mine": {"thinking_main": "xhigh", "thinking_coder": "on"}}})
        doc = w.carl.client_models(w.carl.load_config(), router=True)            # every installed model
        got = {m["id"]: (m["thinking_main"], m["thinking_coder"]) for m in cast(List[Dict[str, JsonValue]], doc["models"])}
        self.assertEqual(got, {"small": ("off", "on"), "mine": ("xhigh", "medium")})

    def test_single_model_mode_lists_only_the_model_the_server_runs(self) -> None:
        """Phase 23.4.4 item 13: single-model mode, only the running model (by its name or its alias), else the model
        a start loads; router mode (config.json or the server's): every installed model."""
        files = {f"{MDIR}/Small-IQ3.gguf": 10 * GIB, f"{MDIR}/mine.gguf": GIB}
        shapes = FakeShapes(local={f"{MDIR}/Small-IQ3.gguf": shape(), f"{MDIR}/mine.gguf": shape()})
        w = World(catalog(BIG, SMALL), files=files, shapes=shapes,
                  config={"schema": 1, "models": {"mine": {"alias": "my-alias"}}})
        cfg = w.carl.load_config()

        def ids(doc: JsonObject) -> List[object]:
            return [m["id"] for m in cast(List[Dict[str, JsonValue]], doc["models"])]
        self.assertEqual((ids(w.carl.client_models(cfg)), w.carl.client_models(cfg)["default"]), (["small"], "small"))
        self.assertEqual((ids(w.carl.client_models(cfg, "mine")), w.carl.client_models(cfg, "mine")["default"]),
                         (["mine"], "mine"))
        self.assertEqual(ids(w.carl.client_models(cfg, "my-alias")), ["mine"])          # the server's alias
        self.assertEqual(ids(w.carl.client_models(cfg, "not-here")), ["small"])         # unknown: the start's model
        self.assertEqual(sorted(map(str, ids(w.carl.client_models(cfg, "mine", router=True)))), ["mine", "small"])
        r = World(catalog(BIG, SMALL), files=files, shapes=shapes, config={"schema": 1, "llama": {"mode": "router"}})
        self.assertEqual(sorted(map(str, ids(r.carl.client_models(r.carl.load_config())))), ["mine", "small"])

    def test_the_client_side_defaults_are_the_domains(self) -> None:
        """client/carl_models.py (stdlib only, on other computers) keeps its own copy of the rules."""
        import os
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "client"))
        import carl_models
        from carl_core.domain import thinking
        self.assertEqual(carl_models.ROLE_DEFAULTS, thinking.DEFAULTS)
        self.assertEqual((carl_models.ROLE_VALUES, carl_models.ROLE_LEVELS, carl_models.ON_LEVEL, carl_models.MAIN),
                         (thinking.VALUES_OF, thinking.LEVELS, thinking.ON_LEVEL, thinking.MAIN))
        for kind in ("effort", "on-off"):
            for role in thinking.ROLES:
                for v in (*thinking.CODER_VALUES, "bad"):
                    self.assertEqual(carl_models.role_value(kind, role, v), thinking.normalize(kind, role, v))


if __name__ == "__main__":
    unittest.main()
