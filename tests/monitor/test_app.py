"""Keys and clicks driving the dashboard, wired with fakes: a FakeStore and no server
(no subprocess is started: nothing here applies settings, downloads or tunes)."""
from __future__ import annotations

import os
import tempfile
import unittest

from mon_support import GIB, FakeStore
from monitor.arrange import FILTERS
from monitor.api import Endpoint
from monitor.app import App, Machine
from monitor.cli import Options
from monitor.collector import Collector
from monitor.jobs import Paths, ServerJobs
from monitor.keys import InputBuffer
from monitor.settings import Schema, SettingsService, net_choices
from monitor.settings_view import SettingsView
from monitor.state import UIState
from monitor.store import ModelList

DOWN, UP, ESC = "\x1b[B", "\x1b[A", "\x1b"


def click(x: int, y: int) -> str:
    return f"\x1b[<0;{x};{y}M\x1b[<0;{x};{y}m"


class AppTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        home = self.tmp.name
        opts = Options(host="127.0.0.1", port=8095, lines=6, interval=2.0, log=None, server_pid=None, console=None,
                       once=True, tab=0, expand=False, home=home, key_file=os.path.join(home, "key"))
        endpoint = Endpoint("127.0.0.1", 8095, "")
        collector = Collector(endpoint, True, opts.key_file, None, None, None, home, 16384)
        self.ui = ui = UIState()
        self.store = store = FakeStore()
        models = ModelList(store, lambda msg: ui.toast(msg, 10))
        svc = SettingsService(models, Schema(net_choices([])), "192.168.42.1", lambda: store.limit)
        paths = Paths(repo=home, logs=os.path.join(home, "logs"), config_file=store.config_file)
        jobs = ServerJobs(ui, collector, svc, paths, "192.168.42.1")
        view = SettingsView(svc, store.config_file, home)
        self.app = App(opts, Machine(16384, 32 * GIB), collector, ui, svc, jobs, view, None, None)
        self.ctl = self.app.ctl
        self.app.frame(self.ctl.data)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def keys(self, *chunks: str) -> None:
        for c in chunks:
            self.ctl.handle_input(c)
            self.app.frame(self.ctl.data)

    def test_tabs_by_key_and_by_click(self) -> None:
        self.keys("2")
        self.assertEqual(self.ui.tab, 1)
        self.keys("\t")
        self.assertEqual(self.ui.tab, 2)
        tab4 = next(r for r in self.ctl.regions if r.action == "tab:3")
        self.keys(click(tab4.x0, tab4.y))
        self.assertEqual(self.ui.tab, 3)

    def test_split_mouse_report_is_one_click(self) -> None:
        tab2 = next(r for r in self.ctl.regions if r.action == "tab:1")
        report = click(tab2.x0, tab2.y)
        buf = InputBuffer()
        for part in (report[:5], report[5:9], report[9:]):
            data = buf.feed(part, lambda: True)
            if data:
                self.ctl.handle_input(data)
        self.assertEqual(self.ui.tab, 1)
        self.assertEqual(self.ui.lines, 6)              # no "-" / "+" taken from the report's digits

    def test_card_levels_lines_help_and_refresh(self) -> None:
        self.keys("e")
        self.assertEqual(set(self.ui.levels.values()), {2})
        self.keys("c")
        self.assertEqual(set(self.ui.levels.values()), {0})
        title = next(r for r in self.ctl.regions if r.action == "level:connect")
        self.keys(click(title.x0 + 3, title.y))
        self.assertEqual(self.ui.levels["connect"], 1)
        self.keys("++-?")
        self.assertEqual((self.ui.lines, self.ui.help), (8, True))
        self.assertTrue(self.ctl.handle_input(" "))

    def test_quit_dialog(self) -> None:
        self.keys("q")
        self.assertTrue(self.ui.quit)
        self.keys("2")                                  # other keys are ignored in the dialog
        self.assertEqual(self.ui.tab, 0)
        self.keys(ESC)
        self.assertFalse(self.ui.quit)
        self.keys("q")
        with self.assertRaises(SystemExit):
            self.ctl.handle_input("d")

    def test_settings_model_picker_and_typed_value(self) -> None:
        self.keys("5")
        p = self.ui.pending
        assert p is not None
        self.assertEqual(p["model"], "auto")
        self.assertNotIn("backend", p)                  # llama.cpp only: no backend row
        self.assertEqual(self.ui.set_row, 0)            # the model row is the first
        self.keys("\r")
        assert self.ui.picker is not None
        self.assertEqual(self.ui.picker.items[0][0], "auto")
        self.keys(DOWN, DOWN, "\r")                     # auto -> big -> iq
        self.assertIsNone(self.ui.picker)
        self.assertEqual(p["model"], "iq")
        self.keys(DOWN, DOWN, "\r")                     # the context row: type a value
        self.assertEqual(self.ui.edit, "")
        self.keys("6", "4", "k", "\r")
        self.assertEqual(p["ctx"], 65536)
        self.keys("\r", "1", "\r")                      # out of range: kept, and said
        self.assertEqual(p["ctx"], 65536)
        self.assertIn("out of range", self.ui.toast_msg[0])
        self.keys(UP, "\x1b[C")                         # KV cache: next choice
        self.assertEqual(p["kv"], "q8_0")
        self.keys("x")                                  # tuned values
        self.assertEqual(self.ui.pending and self.ui.pending["kv"], "q4_0")
        self.keys("r")                                  # revert: from config.json again on the next frame
        self.assertEqual(self.ui.pending and self.ui.pending["model"], "auto")

    def test_server_model_list_keys_and_click(self) -> None:
        self.keys("5")
        p = self.ui.pending
        assert p is not None
        self.keys("m")                                  # focus the list beside the settings
        self.assertTrue(self.ui.slist)
        self.keys(DOWN, "\r")                           # auto -> the first listed model
        self.assertFalse(self.ui.slist)
        first = p["model"]
        self.assertNotEqual(first, "auto")
        click = next(r for r in self.ctl.regions if r.action == "smodel:auto")
        self.keys(f"\x1b[<0;{click.x0};{click.y}M\x1b[<0;{click.x0};{click.y}m")
        self.assertEqual(p["model"], "auto")
        before = (self.ui.msort, self.ui.mfilter)
        self.keys("s", "f")                             # sort and filter from the Server panel
        self.assertEqual((self.ui.msort, self.ui.mfilter), (before[0] + 1, before[1] + 1))

    def test_sort_and_filter_drop_downs_and_back_steps(self) -> None:
        self.keys("5")
        self.ctl.do("msortpick")                         # the side list's "sort: … ▾" opens a drop-down
        pk = self.ui.picker
        assert pk is not None and pk.on_pick == "picksort"
        self.keys(DOWN, DOWN, "\r")
        self.assertEqual(self.ui.msort, 2)
        self.keys("S")                                  # back one
        self.assertEqual(self.ui.msort, 1)
        self.keys("F")                                  # filter: back from "all" wraps to the last option
        self.assertEqual(self.ui.mfilter, len(FILTERS) - 1)
        self.ctl.do("mfilterset:0")                     # a chip click sets it directly
        self.assertEqual(self.ui.mfilter, 0)

    def screen(self) -> str:
        return "\n".join(self.app.frame(self.ctl.data))

    def test_settings_survive_a_catalogue_that_cannot_be_read(self) -> None:
        """A half-updated catalogue (or models.json) once crashed the Settings tab: every
        panel must draw, say what is wrong, and take keys."""
        self.store.broken = "host/catalog.json: models.x.rank: not a number"
        self.keys("5")
        text = self.screen()
        self.assertIn("model list unavailable", text)
        self.assertIn("models.x.rank: not a number", text)
        self.keys(DOWN, "\x1b[C", UP, "\r", ESC, "x", "r")       # rows, a choice, the picker, defaults, revert
        self.keys("]")
        self.assertEqual(self.ui.sp, 1)
        self.screen()
        self.keys("]", DOWN)                            # Auto-tune panel (Enter would start a tune)
        self.assertEqual(self.ui.sp, 2)
        self.screen()
        self.store.broken = None                       # fixed: the list comes back on the next read
        self.app.svc.models.get(refresh=True)
        self.assertIsNone(self.app.svc.models.error)
        self.keys("[", "[")
        self.assertNotIn("model list unavailable", self.screen())

    def test_settings_panel_error_is_shown_not_raised(self) -> None:
        def boom(*_: object) -> list:
            raise RuntimeError("unexpected shape")
        self.app.view.server = boom                     # type: ignore[method-assign]
        self.keys("5")
        text = self.screen()
        self.assertIn("SETTINGS UNAVAILABLE", text)
        self.assertIn("RuntimeError: unexpected shape", text)

    def test_apply_asks_first_and_no_cancels(self) -> None:
        self.keys("5", "a")
        self.assertTrue(self.ui.confirm)
        self.keys("2")                                  # ignored while asking
        self.assertEqual(self.ui.tab, 4)
        self.keys("n")
        self.assertFalse(self.ui.confirm)
        self.assertIsNone(self.ui.restart)

    def test_models_panel_delete_and_text_prompt(self) -> None:
        self.keys("5", "]")
        self.assertEqual(self.ui.sp, 1)
        self.keys("x", "n")                             # big: asked, then cancelled
        self.assertIsNone(self.ui.confirm2)
        self.keys("x", "y")
        self.assertEqual(self.store.deleted, ["big"])
        self.keys("h", "a\x01b", "\x7f", ESC)          # control characters are not typed; Esc cancels
        self.assertIsNone(self.ui.text)
        self.keys("h", "ab")
        self.assertEqual(self.ui.text and self.ui.text.value, "ab")

    def test_auto_tune_panel_choice_and_clear(self) -> None:
        self.keys("5", "[")
        self.assertEqual(self.ui.sp, 2)
        first = self.ui.tune_model
        self.keys("\x1b[C")
        self.assertNotEqual(self.ui.tune_model, first)
        self.keys(" ")
        self.assertTrue(self.ui.tune_quick)
        self.store.config["models"] = {self.ui.tune_model: {"ctx": 1}}
        self.ctl.do("tclear")
        self.assertEqual(self.store.saved[-1]["models"], {})


if __name__ == "__main__":
    unittest.main()
