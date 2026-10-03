"""Keys and clicks driving the dashboard, wired with fakes: a FakeStore and no server
(no subprocess is started: nothing here applies settings, downloads or tunes)."""
from __future__ import annotations

import os
import tempfile
import unittest

from mon_support import GIB, FakeStore
from monitor.arrange import FILTERS
from monitor.fmt import ANSI
from monitor.api import Endpoint
from monitor.app import App, Machine
from monitor.cli import Options
from monitor.collector import Collector
from monitor.jobs import Paths, ServerJobs
from monitor.keys import InputBuffer
from monitor.settings import Schema, SettingsService, net_choices
from monitor.settings_view import SettingsView
from monitor.state import SP_FIT, SP_MODELS, SP_SERVER, SP_TUNE, UIState
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
        self.keys(DOWN, DOWN, "\r")                     # model, KV cache, context: type a value
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
        self.assertEqual(self.ui.sp, SP_MODELS)
        self.screen()
        self.keys("]", DOWN)                            # Auto fit panel
        self.assertEqual(self.ui.sp, SP_FIT)
        self.screen()
        self.keys("]", DOWN)                            # Auto-tune panel (Enter would start a tune)
        self.assertEqual(self.ui.sp, SP_TUNE)
        self.screen()
        self.store.broken = None                       # fixed: the list comes back on the next read
        self.app.svc.models.get(refresh=True)
        self.assertIsNone(self.app.svc.models.error)
        self.keys("[", "[", "[")
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
        self.assertEqual(self.ui.sp, SP_MODELS)
        self.keys("x", "n")                             # big: asked, then cancelled
        self.assertIsNone(self.ui.confirm2)
        self.keys("x", "y")
        self.assertEqual(self.store.deleted, ["big"])
        self.keys("h", "a\x01b", "\x7f", ESC)          # control characters are not typed; Esc cancels
        self.assertIsNone(self.ui.text)
        self.keys("h", "ab")
        self.assertEqual(self.ui.text and self.ui.text.value, "ab")

    def test_auto_tune_panel_choice_and_clear(self) -> None:
        self.keys("5", "[")                             # from the first panel back to the last
        self.assertEqual(self.ui.sp, SP_TUNE)
        first = self.ui.tune_model
        self.keys("\x1b[C")
        self.assertNotEqual(self.ui.tune_model, first)
        self.keys(" ")
        self.assertTrue(self.ui.tune_quick)
        self.store.config["models"] = {self.ui.tune_model: {"ctx": 1}}
        self.ctl.do("tclear")
        self.assertEqual(self.store.saved[-1]["models"], {})

    def server_text(self, cols: int) -> list:
        p = self.ui.pending
        assert p is not None
        rows = self.app.view.server(self.ui, p, self.ctl.data, cols, 400, 8095)
        return [ANSI.sub("", t) for t, _ in rows]

    def test_auto_fit_panel_use_this_sets_the_pick_or_offers_the_download(self) -> None:
        self.keys("5")
        p = self.ui.pending
        assert p is not None
        p["model"], p["ctx"] = "iq", 32768
        self.keys("A")                                          # the Server panel's A opens the Auto fit panel
        self.assertEqual(self.ui.sp, SP_FIT)
        text = self.screen()
        for part in ("AUTO FIT", "This Mac", "The pick", "Use this (Enter)", "Ranking"):
            self.assertIn(part, text)
        self.keys("\r")                                         # Use this: the pick (big) is here, back to Server
        self.assertEqual((p["model"], p["ctx"], p["slots"]), ("big", 98304, "auto"))
        self.assertEqual(self.ui.sp, SP_SERVER)
        self.assertIn("press a to start it", self.ui.toast_msg[0])
        self.store.pick = ("remote", False)                    # not downloaded: asked first
        self.app.svc.models._fit.clear()
        self.keys("A", "\r")
        c = self.ui.confirm2
        assert c is not None
        self.assertEqual((c.title, c.yes, c.model), ("DOWNLOAD?", "mautodl", "remote"))
        self.keys("n")                                          # no download (it would start a process)
        self.assertIsNone(self.ui.confirm2)
        self.assertEqual(p["model"], "remote")
        self.assertTrue(any("Auto fit" in x and "picked remote" in x for x in self.server_text(160)))

    def fake_serve(self, body: str) -> None:
        """A host/serve.sh in the test repo that the Connect tab's installer runs instead."""
        d = os.path.join(self.tmp.name, "host")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "serve.sh")
        with open(path, "w") as f:
            f.write("#!/bin/sh\n" + body)
        os.chmod(path, 0o755)

    def finish_install(self) -> None:
        ins = self.ui.install
        assert ins is not None
        ins.proc.wait(10)                               # type: ignore[attr-defined]
        self.ctl.jobs.poll()
        self.assertTrue(ins.done)

    def test_connect_tab_says_how_to_install(self) -> None:
        self.keys("2")
        text = " ".join(self.screen().split())
        for part in ("SET UP OPENCODE AND PI", "./carl.sh install", "[ Install on this Mac (i) ]",
                     "[ Update configs only (u) ]", "./carl.sh --vm", "(now it listens on this Mac only)"):
            self.assertIn(part, text)

    def test_connect_install_asks_first_then_runs_and_shows_its_output(self) -> None:
        self.fake_serve('echo "args: $*"; echo "CARL_CMD=$CARL_CMD"\n')
        self.keys("2", "i")
        self.assertEqual(self.ui.install_ask, "all")
        self.assertIn("Yes, run it (y)", self.screen())
        self.keys("n")                                  # cancelled: nothing runs
        self.assertIsNone(self.ui.install_ask)
        self.assertIsNone(self.ui.install)
        self.keys("u", ESC)
        self.assertIsNone(self.ui.install_ask)
        self.keys("u", "y")
        self.finish_install()
        assert self.ui.install is not None
        self.assertEqual(self.ui.install.lines, ["args: install --local --port 8095 --config-only", "CARL_CMD=./carl.sh"])
        text = self.screen()
        self.assertIn("INSTALLER", text)
        self.assertIn("done: configs", text)
        self.assertIn("open a new terminal", self.ui.toast_msg[0])
        self.keys("x")                                  # close: the config preview is back
        self.assertFalse(self.ui.install_shown)
        self.assertIn("OPENCODE CONFIG", self.screen())

    def test_connect_install_failure_is_shown(self) -> None:
        self.fake_serve('echo "npm: network down"; exit 3\n')
        self.ctl.do("insall")
        self.ctl.do("insyes")
        self.finish_install()
        text = self.screen()
        self.assertIn("failed (exit 3)", text)
        self.assertIn("npm: network down", text)
        self.assertIn("installer failed", self.ui.toast_msg[0])

    def test_auto_fit_panel_goal_and_scope_are_saved_at_once(self) -> None:
        self.keys("5", "A")
        p = self.ui.pending
        assert p is not None
        self.keys("g")
        self.assertEqual(p["goal"], "hard-code")
        self.assertEqual(self.store.saved[-1]["llama"].get("auto_goal"), "hard-code")
        self.keys("f")
        self.assertEqual(p["scope"], "downloaded")
        self.assertEqual(self.store.saved[-1]["llama"].get("auto_fit"), "downloaded")
        click = next(r for r in self.ctl.regions if r.action == "fgoal:everyday")
        self.keys(f"\x1b[<0;{click.x0};{click.y}M\x1b[<0;{click.x0};{click.y}m")
        self.assertEqual(p["goal"], "everyday")
        self.assertNotIn("auto_goal", self.store.saved[-1]["llama"])  # the default is left out
        rows = self.app.view.autofit(self.ui, p, 100, 12)             # a short screen scrolls
        self.ui.fit_scroll = 10**6
        self.assertEqual(len(self.app.view.autofit(self.ui, p, 100, 12)), 12)
        self.assertGreater(self.ui.fit_scroll, 0)
        self.assertEqual(len(rows), 12)

    def test_server_card_sections_at_80_and_160_columns(self) -> None:
        """About this setting, Status, the buttons and Keys are separate, and nothing is cut."""
        self.keys("5")
        for cols in (80, 160):
            with self.subTest(cols=cols):
                text = self.server_text(cols)
                heads = {h: next(i for i, x in enumerate(text) if f" {h} ─" in x)
                         for h in ("About this setting", "Status", "Keys")}
                self.assertLess(heads["About this setting"], heads["Status"])
                self.assertLess(heads["Status"], heads["Keys"])
                buttons = [i for i, x in enumerate(text) if "[ Start server (a) ]" in x]
                self.assertEqual(len(buttons), 1)
                self.assertTrue(heads["Status"] < buttons[0] < heads["Keys"])
                self.assertNotIn("Status", text[buttons[0]])
                card = text[:next(i for i, x in enumerate(text) if "╰" in x)]
                self.assertFalse(any("…" in x for x in card), [x for x in card if "…" in x])
                joined = " ".join(" ".join(x.strip(" │").split()) for x in card)
                self.assertIn("then h to add one from Hugging Face.", joined)      # the model row's help, in full
                self.assertIn("Press a to start the server", joined)


if __name__ == "__main__":
    unittest.main()
