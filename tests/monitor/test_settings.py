"""The Settings tab's model: values, typed input, colours, fit maths and the config.json mapping,
through a fake ModelStore."""
from __future__ import annotations

import unittest

from mon_support import GIB, FakeStore, model, model_list, shape
from monitor.fmt import GRN, RED, YEL
from monitor.model import ServerData
from monitor.settings import (LLAMA_ADV, MODEL_ROW_KEYS, SET_HELP, Schema, SettingsService, env_from_cmd, fmt_val,
                              llama_fit, max_ctx_per_slot, net_choices, parse_typed, row_instruction, rows,
                              running_settings, settings_to_config, shown_value, step_choice)

SCHEMA = Schema(net_choices(["192.168.1.5"]))
LLAMA_CMD = ("/opt/llama-server -m /m/big.gguf --host 127.0.0.1 -c 196608 --parallel 2 -ctk q4_0 -ctv q4_0 "
             "--spec-type draft-mtp,ngram-mod --spec-draft-n-max 1 --cache-ram 4096 --temp 1.0 --top-k 20 -ub 512 "
             "--alias big")


def service(store: FakeStore) -> SettingsService:
    return SettingsService(model_list(store), SCHEMA, "192.168.42.1", gpu_limit=lambda: store.limit)


class ValuesTest(unittest.TestCase):
    def test_fmt_val(self) -> None:
        self.assertEqual(fmt_val("ctx", "98304"), 98304)
        self.assertEqual(fmt_val("cache", "auto"), "auto")
        self.assertEqual(fmt_val("temp", 1), "1.0")
        self.assertEqual(fmt_val("top_p", 0.950), "0.95")
        self.assertEqual(fmt_val("presence", "1.50"), "1.5")
        self.assertEqual(fmt_val("specn", 2), "2")
        self.assertEqual(fmt_val("temp", "N/A"), "N/A")

    def test_shown_value(self) -> None:
        self.assertEqual(shown_value("ctx", 65536), "64K")
        self.assertEqual(shown_value("kv", "q4_0"), "q4_0")
        self.assertEqual(shown_value("cache", 4096), "4096")

    def test_parse_typed(self) -> None:
        self.assertEqual(parse_typed("ctx", "64k"), (65536, ""))
        self.assertEqual(parse_typed("ctx", " 98304 "), (98304, ""))
        self.assertEqual(parse_typed("temp", "1"), ("1.0", ""))
        self.assertEqual(parse_typed("temp", "0.65"), ("0.65", ""))
        self.assertEqual(parse_typed("top_k", "40"), ("40", ""))
        self.assertEqual(parse_typed("cache", "2k"), (2048, ""))
        self.assertEqual(parse_typed("ctx", "1k"), (None, "1k is out of range for ctx"))
        self.assertEqual(parse_typed("top_p", "1.5"), (None, "1.5 is out of range for top_p"))
        self.assertEqual(parse_typed("ctx", "1.2.3"), (None, "not a number: '1.2.3'"))
        self.assertEqual(parse_typed("ctx", ""), (None, "not a number: ''"))

    def test_step_choice_cycles(self) -> None:
        self.assertEqual(step_choice(["a", "b", "c"], "c", 1), "a")
        self.assertEqual(step_choice(["a", "b", "c"], "a", -1), "c")
        self.assertEqual(step_choice([32768, 65536], "65536", -1), 32768)
        self.assertEqual(step_choice(["a", "b"], "zzz", 1), "b")         # not a choice: from the first

    def test_rows(self) -> None:
        llama = rows({"adv": "hidden"}, SCHEMA, lambda: ["auto", "big"])
        self.assertEqual(llama[0].key, "model")                    # no backend row: llama.cpp only
        self.assertEqual([r.key for r in llama][-2:], ["presence", "adv"])
        self.assertEqual(llama[0].choices, ["auto", "big"])
        self.assertIn("192.168.1.5", next(r for r in llama if r.key == "net").choices or [])
        shown = rows({"adv": "shown"}, SCHEMA, lambda: [])
        self.assertEqual(shown[-len(LLAMA_ADV):], LLAMA_ADV)
        self.assertEqual(set(SCHEMA.defaults()), {r.key for r in SCHEMA.llama + LLAMA_ADV} | {"goal", "scope"})
        self.assertNotIn("goal", [r.key for r in llama])                # the Auto fit panel has them
        self.assertEqual(next(r for r in llama if r.key == "net").default, "local")
        self.assertEqual(net_choices(["10.0.0.2"]), ("local", "vm", "10.0.0.2"))       # no "auto" since 1.3.0

    def test_model_row_keys(self) -> None:
        self.assertEqual(MODEL_ROW_KEYS["specn"], "spec_n")
        self.assertEqual(set(MODEL_ROW_KEYS), {"kv", "ctx", "slots", "spec", "specn", "temp", "presence", "top_k", "top_p",
                                               "min_p", "repeat"})


class RunningTest(unittest.TestCase):
    def test_llama_command_line(self) -> None:
        ml = model_list()
        run = running_settings(ServerData(cmd=LLAMA_CMD, n_ctx=98304), "192.168.42.1", ml.by_name)
        self.assertEqual((run["model"], run["kv"], run["ctx"], run["slots"]), ("big", "q4_0", 98304, "2"))
        self.assertEqual((run["cache"], run["net"], run["presence"], run["ub"]), (4096, "local", "N/A", "512"))

    def test_not_a_known_server(self) -> None:
        self.assertEqual(running_settings(ServerData(cmd="python3 other.py"), "x", lambda n: None), {})

class ConfigMappingTest(unittest.TestCase):
    def pending(self, **kw: object) -> dict:
        p = dict(SCHEMA.defaults(), adv="hidden", **{k: v for k, v in kw.items()})
        return p

    def test_defaults_are_not_saved_and_an_address_is_a_host(self) -> None:
        tuned = dict(mon_tune())
        p = self.pending(model="big", net="192.168.1.5", cache=4096, ub="1024", kv="q4_0", ctx=98304, slots="auto",
                         spec="draft-mtp,ngram-mod", specn="1", temp="1.0", presence="0", top_k="20", top_p="0.95",
                         min_p="0", repeat="1.0")
        cfg = settings_to_config(p, {"schema": 1, "llama": {"net": "vm", "ckpt": "16"}}, SCHEMA, "big", tuned)
        self.assertNotIn("backend", cfg)
        self.assertEqual(cfg["llama"], {"host": "192.168.1.5", "model": "big", "cache_ram": 4096, "ub": "1024"})
        self.assertEqual(cfg["models"], {})

    def test_model_profile_keeps_only_values_that_differ_from_the_tune(self) -> None:
        p = self.pending(model="big", kv="q8_0", ctx=65536, slots="auto", spec="draft-mtp,ngram-mod", specn="1",
                         temp="0.6", presence="0", top_k="20", top_p="0.95", min_p="0", repeat="1.0")
        cfg = settings_to_config(p, {"schema": 1, "models": {"big": {"kv": "q4_0"}, "other": {"ctx": 1}}}, SCHEMA, "big",
                                 mon_tune())
        self.assertEqual(cfg["models"], {"big": {"kv": "q8_0", "ctx": 65536, "temp": "0.6"}, "other": {"ctx": 1}})

    def test_rollback_environments(self) -> None:
        env = env_from_cmd(LLAMA_CMD)
        self.assertEqual(env["MODEL"], "/m/big.gguf")
        self.assertEqual((env["CTX"], env["SLOTS"], env["KV_K"], env["CACHE_RAM"], env["SETTINGS_FILE"]),
                         ("196608", "2", "q4_0", "4096", "none"))
        self.assertNotIn("TOP_P_X", env)


def mon_tune() -> dict:
    vals, _ = FakeStore().effective_tune(model("big"), {})
    return vals


class FitMathTest(unittest.TestCase):
    def test_llama_need_and_auto_slots(self) -> None:
        shp = shape(kv_elems=10240, rs_bytes=0)
        per_tok = 10240 * 18 / 32
        ok, text = llama_fit("m", 10 * GIB, shp, "q4_0", 65536, "auto", 25 * GIB)
        self.assertTrue(ok)
        self.assertIn("for 2 × 64K (q4_0)", text)
        need2 = 10 * GIB + per_tok * 65536 * 2 + GIB
        ok1, text1 = llama_fit("m", 10 * GIB, shp, "q4_0", 65536, "auto", int(need2) - 1)
        self.assertTrue(ok1)
        self.assertIn("for 1 ×", text1)
        self.assertFalse(llama_fit("m", 30 * GIB, shp, "q4_0", 65536, "1", 25 * GIB)[0])

    def test_max_ctx_per_slot(self) -> None:
        shp = shape(kv_elems=10240, rs_bytes=0, ctx_train=40960)
        self.assertEqual(max_ctx_per_slot(10 * GIB, shp, 25 * GIB), 40960)        # capped at the trained context
        self.assertEqual(max_ctx_per_slot(30 * GIB, shp, 25 * GIB), 0)
        room = 12 * GIB - 10 * GIB - GIB
        self.assertEqual(max_ctx_per_slot(10 * GIB, shape(kv_elems=10240, rs_bytes=0), 12 * GIB),
                         int(room // (10240 * 18 / 32)) // 4096 * 4096)


class ServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeStore(config={"schema": 1, "llama": {"cache_ram": 2560}, "models": {"big": {"temp": 0.6}}})
        self.svc = service(self.store)

    def test_value_colours(self) -> None:
        p = dict(SCHEMA.defaults(), model="big", ctx=65536, spec="draft-mtp", specn="1", kv="q4_0")
        self.assertEqual(self.svc.value_color("ctx", p), GRN)
        self.assertEqual(self.svc.value_color("ctx", dict(p, ctx=98304)), YEL)
        self.assertEqual(self.svc.value_color("ctx", dict(p, ctx=262144)), RED)
        self.assertEqual(self.svc.value_color("kv", p), GRN)
        self.assertEqual(self.svc.value_color("kv", dict(p, kv="q8_0")), YEL)
        self.assertEqual(self.svc.value_color("spec", dict(p, model="nomtp")), RED)              # no MTP head
        self.assertEqual(self.svc.value_color("spec", dict(p, model="iq", specn="2")), RED)      # IQ quant, n > 1
        self.assertEqual(self.svc.value_color("net", p), "")

    def test_pending_init_from_config_and_the_running_server(self) -> None:
        p = self.svc.pending_init(ServerData(cmd=LLAMA_CMD, n_ctx=98304), self.store.load_config())
        self.assertEqual((p["model"], p["cache"], p["ctx"], p["slots"], p["net"]),
                         ("big", 2560, 98304, "2", "local"))
        self.assertEqual(p["temp"], "1.0")         # what runs wins over the profile's 0.6
        p2 = self.svc.pending_init(ServerData(), self.store.load_config())
        self.assertEqual((p2["model"], p2["temp"], p2["net"]), ("auto", "0.6", "local"))

    def test_slots_3_and_4_only_when_they_fit(self) -> None:
        p = dict(SCHEMA.defaults(), adv="hidden", model="big")
        choices = next(r for r in self.svc.rows(p) if r.key == "slots").choices
        most = self.svc.max_slots(p)
        self.assertEqual(choices, ["auto", "1", "2", "3", "4"][: 3 + max(most - 2, 0)])
        tiny = SettingsService(self.svc.models, SCHEMA, "192.168.42.1", lambda: 1)   # nothing fits
        self.assertEqual(next(r for r in tiny.rows(p) if r.key == "slots").choices, ["auto", "1", "2"])

    def test_defaults_for_keeps_model_and_advanced(self) -> None:
        q = self.svc.defaults_for(dict(SCHEMA.defaults(), model="big", adv="shown", kv="q8_0", cache=8192))
        self.assertEqual((q["model"], q["adv"], q["kv"], q["cache"]), ("big", "shown", "q4_0", "auto"))

    def test_fit_line(self) -> None:
        p = dict(SCHEMA.defaults(), adv="hidden", model="remote")
        self.assertIn("is not downloaded", self.svc.fit_line(p)[1])
        self.assertIn("unknown model", self.svc.fit_line(dict(p, model="nope"))[1])
        ok, text = self.svc.fit_cached(dict(p, model="big", ctx=65536, slots="1"))
        self.assertTrue(ok)
        self.assertIn("big needs", text)

    def test_save_writes_the_mapping(self) -> None:
        p = self.svc.pending_init(ServerData(), self.store.load_config())
        p["kv"] = "q8_0"
        self.svc.save(p)
        saved = self.store.saved[-1]
        self.assertEqual(saved["llama"], {"cache_ram": 2560})
        self.assertEqual(saved["models"]["big"], {"kv": "q8_0", "temp": "0.6"})

    def test_unreadable_catalogue_or_config(self) -> None:
        """A catalogue / models.json that can't be read: no model is known, nothing raises."""
        self.store.broken = "catalog.json: bad"
        svc = service(self.store)
        self.assertIsNone(svc.models.by_name("big"))
        self.assertEqual(svc.models.error, "catalog.json: bad")
        self.assertEqual(svc.resolved_model({"model": "auto"}), "auto")
        p = svc.pending_init(ServerData(cmd=LLAMA_CMD, n_ctx=98304), self.store.load_config())
        self.assertEqual(p["model"], "auto")
        self.assertIn("unknown model", svc.fit_line(p)[1])
        self.assertEqual(svc.recommended("big")[1], {})

        class BadConfig(FakeStore):
            def load_config(self) -> dict:
                raise ValueError("config.json: llama.net: 'moon' is not one of auto, local, vm")
        bad = BadConfig()
        vals, _ = service(bad).recommended("big", with_config=True)     # the tune without the overrides
        self.assertEqual(vals["kv"], "q4_0")

    def test_auto_model_and_max_ctx(self) -> None:
        self.assertEqual(self.svc.resolved_model({"model": "auto"}), "big")
        self.assertGreater(self.svc.max_ctx(model("remote", status="missing")) or 0, 0)   # catalogue: its HF header
        custom = dict(model("hfonly", status="missing"), source="hf")
        self.assertIsNone(self.svc.max_ctx(custom))                                         # custom, not here: unknown
        self.assertGreater(self.svc.max_ctx(self.store.models[0]) or 0, 0)



class AutoFitTest(unittest.TestCase):
    """Auto fit in the Settings tab: goal and scope (the Auto fit panel), the one-step Use this, the start model."""

    def setUp(self) -> None:
        self.store = FakeStore()
        self.svc = service(self.store)

    def test_goal_and_scope_are_saved_to_llama(self) -> None:
        p = dict(SCHEMA.defaults(), adv="hidden", goal="hard-code", scope="downloaded")
        cfg = settings_to_config(p, {"schema": 1}, SCHEMA, None, None)
        self.assertEqual((cfg["llama"]["auto_goal"], cfg["llama"]["auto_fit"]), ("hard-code", "downloaded"))
        cfg = settings_to_config(dict(SCHEMA.defaults(), adv="hidden"), cfg, SCHEMA, None, None)
        self.assertNotIn("auto_goal", cfg["llama"])                     # defaults are not saved

    def test_panel_choice_is_saved_at_once(self) -> None:
        self.store.config = {"schema": 1, "llama": {"model": "iq"}}
        p: dict = {"goal": "everyday", "scope": "catalogue"}
        self.svc.save_auto_choice(p, "goal", "hard-code")
        self.assertEqual(p["goal"], "hard-code")
        self.assertEqual(self.store.saved[-1]["llama"], {"model": "iq", "auto_goal": "hard-code"})
        self.svc.save_auto_choice(p, "goal", "everyday")                # the default is left out
        self.assertEqual(self.store.saved[-1]["llama"], {"model": "iq"})
        with self.assertRaises(ValueError):
            self.svc.save_auto_choice(p, "scope", "everything")

    def test_auto_resolves_with_the_rows_goal_and_scope(self) -> None:
        seen = []
        orig = self.store.launch_model

        def launch(cfg: dict) -> str:
            seen.append((cfg["llama"].get("auto_goal"), cfg["llama"].get("auto_fit"), "model" in cfg["llama"]))
            return orig(cfg)
        self.store.launch_model = launch                                # type: ignore[method-assign]
        self.store.config = {"schema": 1, "llama": {"model": "iq"}}
        self.assertEqual(self.svc.resolved_model({"model": "auto", "goal": "hard-code", "scope": "downloaded"}), "big")
        self.assertEqual(seen, [("hard-code", "downloaded", False)])   # config's llama.model ignored for auto
        fit = self.svc.auto_fit({"goal": "hard-code"})
        self.assertEqual((fit.goal if fit else None, self.store.fit_calls[-1]), ("hard-code", ("hard-code", "catalogue")))

    def test_apply_auto_fit_sets_model_ctx_slots_kv(self) -> None:
        p = dict(SCHEMA.defaults(), adv="hidden", model="iq", ctx=32768, kv="q8_0", slots="1")
        fit = self.svc.apply_auto_fit(p)
        self.assertIsNotNone(fit)
        self.assertEqual((p["model"], p["ctx"], p["kv"], p["slots"]), ("big", 98304, "q4_0", "auto"))
        self.store.pick = None                                          # nothing fits: p unchanged
        q = dict(SCHEMA.defaults(), adv="hidden", model="iq")
        none = service(self.store).apply_auto_fit(q)
        self.assertIsNone(none.pick if none else "no answer")
        self.assertEqual(q["model"], "iq")

    def test_fit_line_names_the_largest_window_when_too_big(self) -> None:
        ok, text = llama_fit("m", 20 * GIB, shape(), "q4_0", 262144, "2", 22 * GIB)
        self.assertFalse(ok)
        self.assertIn("largest window", text)
        ok, text = llama_fit("m", 30 * GIB, shape(), "q4_0", 4096, "1", 22 * GIB)
        self.assertIn("the weights alone don't fit", text)

    def test_row_instructions_are_for_new_users(self) -> None:
        self.assertTrue(row_instruction("model").startswith("Press Enter"))
        self.assertTrue(row_instruction("ctx").startswith("Type a number and press Enter"))
        self.assertTrue(row_instruction("kv").startswith("Press ← →"))
        for key, text in SET_HELP.items():
            with self.subTest(key=key):
                self.assertNotIn("Enter:", text)


if __name__ == "__main__":
    unittest.main()
