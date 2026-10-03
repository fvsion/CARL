"""Router mode and the client model list (domain + application): each downloaded model's
preset section is what a single start of it would run, a model that doesn't fit is left out
with the reason, the start model loads first; the clients list only the installed models."""
from __future__ import annotations

import unittest

from support import GIB, MDIR, FakeShapes, World, catalog, entry, shape
from carl_core.domain.clientlist import client_entry, client_list
from carl_core.domain.router import Common, Preset, plan_model, preset_ini
from carl_core.domain.settings import LLAMA_KEYS
from carl_core.domain.types import ModelInfo

COMMON = Common(batch=2048, ubatch=512, ckpt=8, ckpt_step=4096, cache_ram=None)
VALS = {"ctx": 98304, "slots": "auto", "kv": "q4_0", "spec": "draft-mtp,ngram-mod", "spec_n": 1, "temp": 1.0,
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
        p, why = plan_model("m", "/m.gguf", dict(VALS, slots="1"), shape(), 30 * GIB, 24 * GIB, 32 * GIB, 6 * GIB,
                            COMMON, None)
        self.assertIsNone(p)
        self.assertIn("the weights alone don't fit", why)

    def test_a_path_with_a_line_break_is_refused(self) -> None:
        from carl_core.domain.errors import ConfigError
        with self.assertRaises(ConfigError):
            plan_model("m", "/m\n[x].gguf", VALS, shape(), GIB, 24 * GIB, 32 * GIB, 6 * GIB, COMMON, None)

    def test_ini(self) -> None:
        two, _ = plan_model("two", "/two.gguf", VALS, shape(), 10 * GIB, 24 * GIB, 32 * GIB, 6 * GIB, COMMON, "/t.jinja")
        one, _ = plan_model("one", "/one.gguf", dict(VALS, slots="1", spec="none"), shape(), GIB, 24 * GIB, 32 * GIB,
                            6 * GIB, COMMON, None)
        assert two and one
        text = preset_ini(Preset([two, one], [("big", "needs 40.0 GiB")], start="one"), COMMON)
        self.assertIn("version = 1\n\n[*]\njinja = true\n", text)
        self.assertIn('chat-template-kwargs = {"preserve_thinking":true}', text)
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
    def test_only_downloaded_models(self) -> None:
        ms: list[ModelInfo] = [{"name": "a", "label": "A · Q4", "status": "downloaded", "thinking": "effort"},
                               {"name": "b", "status": "missing"},
                               {"name": "c", "label": "C\x1b[2J", "status": "downloaded"}]
        doc = client_list(ms, lambda m: 65536, default="b")
        self.assertEqual(doc["default"], None)                               # not installed: no default
        self.assertEqual(doc["models"], [{"id": "a", "label": "A · Q4", "ctx": 65536, "thinking": "effort"},
                                         {"id": "c", "label": "C[2J", "ctx": 65536, "thinking": "on-off"}])
        self.assertEqual(client_entry({"name": "x"}, 4096)["label"], "x")

    def test_app_client_models(self) -> None:
        w = World(catalog(BIG, SMALL), files={f"{MDIR}/Small-IQ3.gguf": 10 * GIB},
                  shapes=FakeShapes(local={f"{MDIR}/Small-IQ3.gguf": shape()}),
                  config={"schema": 1, "models": {"small": {"ctx": 131072}}})
        doc = w.carl.client_models(w.carl.load_config())
        self.assertEqual(doc["default"], "small")
        self.assertEqual(doc["models"], [{"id": "small", "label": "small", "ctx": 131072, "thinking": "on-off"}])


if __name__ == "__main__":
    unittest.main()
