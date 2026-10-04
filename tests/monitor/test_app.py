"""Keys and clicks driving the dashboard, wired with fakes: a FakeStore and no server
(no subprocess is started: nothing here applies settings, downloads or tunes)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from mon_support import GIB, FakeStore
from monitor.arrange import FILTERS
from monitor.fmt import ANSI, vlen
from monitor.api import Endpoint
from monitor.app import App, Machine
from monitor.cli import Options
from monitor.collector import Collector
from monitor.jobs import Paths, ServerJobs
from monitor.keys import InputBuffer
from monitor.settings import Schema, SettingsService, net_choices
from monitor.settings_view import SettingsView
from monitor.state import SP_CACHE, SP_FIT, SP_MODELS, SP_ROUTER, SP_SERVER, SP_TUNE, UIState
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
        self.keys("5", "[", "[", "[")                   # back from the first panel: Caching, Router, Auto-tune
        self.assertEqual(self.ui.sp, SP_TUNE)
        first = self.ui.tune_model
        self.keys("\x1b[C")
        self.assertNotEqual(self.ui.tune_model, first)
        self.keys(" ")
        self.assertEqual(self.ui.tune_depth, "long")              # default -> long (space cycles)
        self.store.config["models"] = {self.ui.tune_model: {"ctx": 1}}
        self.ctl.do("tclear")
        self.assertEqual(self.store.saved[-1]["models"], {})

    def test_auto_tune_all_models(self) -> None:
        from unittest import mock
        from monitor.state import TUNE_ALL
        self.keys("5", "[", "[", "[")
        self.keys("\x1b[D")                                      # back from the first model: all models
        self.assertEqual(self.ui.tune_model, TUNE_ALL)
        text = " ".join(ANSI.sub("", self.screen()).split())
        self.assertIn("all models (", text)
        self.assertIn("Last results", text)
        with mock.patch("monitor.jobs.start_tool") as start, mock.patch("threading.Thread") as thread:
            self.app.jobs.run_tune(self.ctl.data, confirmed=True)
            thread.call_args.kwargs["target"]()                 # the work, here and now
        self.assertEqual(start.call_args.args[0][1:], ["all"])

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
                     "[ Update configs only (u) ]", "./carl.sh --vm", "(now: this Mac only)"):
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
        self.assertIn("Open a new terminal", self.ui.toast_msg[0])
        self.keys("x")                                  # close: the config preview is back
        self.assertFalse(self.ui.install_shown)
        self.assertIn("OPENCODE CONFIG", self.screen())

    def test_connect_warns_when_the_client_lists_are_out_of_date(self) -> None:
        self.write_client_state({"gone": {}})
        self.keys("2")
        text = " ".join(" ".join(x.strip(" │") for x in ANSI.sub("", self.screen()).splitlines()).split())
        installed = [m["name"] for m in self.store.models if m["status"] == "downloaded"]
        self.assertIn(f"Out of date: OpenCode lists 1 models; installed now: {len(installed)}", text)
        self.assertIn("removed gone", text)
        self.assertIn("Press u to list the installed models", text)          # the quick tip

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

    def test_caching_panel_saves_at_once_and_clears_after_a_question(self) -> None:
        conf = os.path.join(self.tmp.name, "carl", "config.json")
        self.app.jobs.paths = Paths(repo=self.tmp.name, logs=self.tmp.name, config_file=conf)
        slots = os.path.join(self.tmp.name, "carl", "slots")
        os.makedirs(slots)
        for n in ("carl-prefix+m+build+abc123.bin", "carl-session+m+0123456789+ses_1.bin"):
            with open(os.path.join(slots, n), "wb") as f:
                f.write(b"x" * 1000)
        self.keys("5", "[")                                     # back from the first panel: the last, Caching
        self.assertEqual(self.ui.sp, SP_CACHE)
        text = " ".join(ANSI.sub("", self.screen()).split())
        for part in ("CACHING", "Disk limit", "10 GB", "Prompts", "save conversations", "m · build", "m · ses_1"):
            self.assertIn(part, text)
        self.keys("d", "p")                                     # the next limit after the default 10; prompts off
        self.assertEqual(self.store.config["cache"], {"disk_gb": 20, "prefix": False})
        self.assertEqual(self.app.jobs.cache_conf().disk_gb, 20)
        self.ctl.do("cache:disk:50")
        self.assertEqual(self.store.config["cache"]["disk_gb"], 50)
        self.keys("o", "w")                                     # when to save: the next after auto; SWA: next after auto
        self.assertEqual((self.store.config["cache"]["save"], self.store.config["cache"]["swa"]), ("turn", "full"))
        self.ctl.do("cache:save:stop")
        self.assertEqual(self.app.jobs.cache_conf(fresh=True).save, "stop")
        self.keys("c")
        self.assertIsNotNone(self.ui.confirm2)
        self.assertEqual(len(os.listdir(slots)), 2)             # nothing removed before the answer
        self.keys("y")
        self.assertEqual(os.listdir(slots), [])

    def test_before_a_stop_the_recorded_sessions_are_saved(self) -> None:
        from unittest import mock
        from monitor.model import ServerData, SlotInfo
        conf = os.path.join(self.tmp.name, "carl", "config.json")
        self.app.jobs.paths = Paths(repo=self.tmp.name, logs=self.tmp.name, config_file=conf)
        slots = os.path.join(self.tmp.name, "carl", "slots")
        os.makedirs(slots)
        for slot, task in ((0, 7), (1, 3)):
            with open(os.path.join(slots, f".resident+m+{slot}.json"), "w") as f:
                json.dump({"model": "m", "slot": slot, "file": f"carl-session+m+k+s{slot}.bin", "task": task}, f)
        live = [{"id": 0, "id_task": 7, "is_processing": False}, {"id": 1, "id_task": 9, "is_processing": False}]
        d = ServerData(up=True, props={"model_alias": "m"})
        d.slot_list = [SlotInfo.from_json(x) for x in live]
        ep = self.app.jobs.collector.endpoint
        with mock.patch.object(ep, "get", return_value=json.dumps(live)), \
                mock.patch.object(ep, "post", return_value="{}") as post, \
                mock.patch("monitor.diskcache.free_enough", return_value=True):
            self.app.jobs.save_before_stop(d)
        post.assert_called_once_with("/slots/0?action=save", {"filename": "carl-session+m+k+s0.bin"}, timeout=600)
        self.assertEqual(sorted(os.listdir(slots)), [".resident+m+1.json"])   # slot 1 ran something since: kept
        self.store.config["cache"] = {"save": "turn"}                            # every turn saved: nothing to do
        with mock.patch.object(ep, "post") as post2:
            self.app.jobs.save_before_stop(d)
        post2.assert_not_called()

    def test_stop_waits_for_the_turn_and_its_save(self) -> None:
        """Stop while a client's turn runs: asked first; Wait goes on only when no slot writes, no turn is
        marked and the save is on disk (two looks in a row); then the server stops."""
        from unittest import mock
        from monitor.model import ServerData
        conf = os.path.join(self.tmp.name, "carl", "config.json")
        self.app.jobs.paths = Paths(repo=self.tmp.name, logs=self.tmp.name, config_file=conf)
        slots = os.path.join(self.tmp.name, "carl", "slots")
        os.makedirs(slots)
        mark = os.path.join(slots, ".turn+m+1")
        with open(mark, "w") as f:
            json.dump({"model": "m", "slot": 1, "session": "s"}, f)
        live = [{"id": 0, "is_processing": False}, {"id": 1, "is_processing": True}]
        self.ctl.data = d = ServerData(up=True, props={"model_alias": "m"}, target_pid=4242)
        ep = self.app.jobs.collector.endpoint
        jobs = self.app.jobs
        with mock.patch.object(ep, "get", side_effect=lambda *a, **k: json.dumps(live)), \
                mock.patch.object(jobs, "save_before_stop") as save, \
                mock.patch("monitor.system.kill") as kill:
            self.ctl.do("stop")
            self.assertIsNotNone(self.ui.drain)
            self.assertIn("A TURN IS RUNNING", "\n".join(self.app.frame(d)))
            self.keys("w")                                          # wait for the end of the turn
            self.assertTrue(self.ui.drain and self.ui.drain.waiting)
            live[1]["is_processing"] = False                        # the reply ended, the agent runs a tool
            for _ in range(3):
                self.ui.drain.checked = 0
                jobs.drain_tick(d)
            self.assertIsNotNone(self.ui.drain)                     # the turn mark: still waiting
            self.assertEqual(self.ui.drain.turns, [1])
            os.remove(mark)                                         # the turn's save is done
            self.ui.drain.checked = 0
            jobs.drain_tick(d)
            kill.assert_not_called()                                # one idle look is not enough
            self.ui.drain.checked = 0
            jobs.drain_tick(d)
            self.assertIsNone(self.ui.drain)
            save.assert_called_once()
            kill.assert_called_once()
            self.assertEqual(self.ui.stopping[0], 4242)
            # nothing runs: Stop goes at once; a turn runs and the user says Now: at once too
            self.ui.stopping = None
            kill.reset_mock()
            self.ctl.do("stop")
            self.assertIsNone(self.ui.drain)
            kill.assert_called_once()
            live[0]["is_processing"] = True
            kill.reset_mock()
            self.ctl.do("stop")
            self.keys("y")
            self.assertIsNone(self.ui.drain)
            kill.assert_called_once()

    def test_connect_clients_tab(self) -> None:
        from monitor.cacheapi import Client
        conf = os.path.join(self.tmp.name, "carl", "config.json")
        self.app.jobs.paths = Paths(repo=self.tmp.name, logs=self.tmp.name, config_file=conf)
        self.keys("2", "]")
        self.assertEqual(self.ui.connect_sp, 1)
        text = " ".join(ANSI.sub("", self.screen()).split())
        self.assertIn("CLIENTS", text)
        self.assertIn("no other computer yet", text)
        self.store.client_models = lambda: {"schema": 1, "models": [{"id": "m"}]}   # type: ignore[method-assign]
        self.keys("P")                                                  # push
        self.assertTrue(self.app.jobs.pushed())
        version = self.app.jobs.pushed().split(" at ")[0]
        from monitor.views import clients_lines
        from monitor.cards import View
        view = View(levels={}, host="h", port=1, base="b", key="", key_file="", key_shown=False, server_pid=None, log=None,
                    log_path=None, model_path=None, model_size=None, gpu_limit=None, slow=None, total_mem=0, home="/",
                    pushed=f"{version} at now",
                    clients=(Client("0123456789ab", host="vm-1", user="u", os="Linux", applied=version, mode="service",
                                    connected=1),
                             Client("ba9876543210", host="vm-2", applied="old", mode="check", auto_apply=False)))
        lines = " ".join(ANSI.sub("", x if isinstance(x, str) else x.text) for x in clients_lines(view, [], [], 400))
        self.assertIn("vm-1", lines)
        self.assertIn(f"up to date ({version})", lines)
        self.assertIn(f"has old, pushed {version} (auto-apply off: it waits)", lines)

    def test_router_panel_switches_the_mode_after_a_question(self) -> None:
        self.keys("5", "[", "[")                                # back from the first panel: Caching, then Router
        self.assertEqual(self.ui.sp, SP_ROUTER)
        text = " ".join(" ".join(x.strip(" │") for x in ANSI.sub("", self.screen()).splitlines()).split())
        for part in ("ROUTER", "Dashboard only (single model)", "OpenCode / Pi switch models (router)",
                     "change models during their work", "Each switch empties the prompt cache",
                     "Update the OpenCode / Pi configs"):
            self.assertIn(part, text)
        self.ctl.do("rmode:router")
        c = self.ui.confirm2
        assert c is not None
        self.assertEqual((c.title, c.yes, c.model), ("MODEL SWITCHING?", "rmodeyes", "router"))
        self.assertTrue(any("empties the prompt cache" in x for x in c.lines))
        self.keys("y")                                          # no server runs: saved for the next start
        self.assertEqual(self.store.saved[-1]["llama"]["mode"], "router")
        self.assertIn("the next start uses it", self.ui.toast_msg[0])
        self.assertIsNone(self.ui.install)                      # CARL didn't set up clients here: nothing to update
        self.ctl.do("rmode:single")
        self.keys("y")
        self.assertNotIn("mode", self.store.saved[-1].get("llama", {}))      # the default is left out

    def test_a_mode_switch_updates_the_clients_set_up_here(self) -> None:
        self.fake_serve('echo "args: $*"\n')
        self.write_client_state()
        self.keys("5")
        self.ctl.do("rmode:router")
        self.keys("y")
        self.finish_install()                                   # no server: at once (else after the restart)
        assert self.ui.install is not None
        self.assertEqual(self.ui.install.lines, ["args: install --local --port 8095 --config-only"])

    def write_client_state(self, models: object = None) -> None:
        home = self.tmp.name
        os.makedirs(os.path.join(home, ".config/opencode"), exist_ok=True)
        with open(os.path.join(home, ".config/opencode/carl.json"), "w", encoding="utf-8") as f:
            json.dump({"providers": {"llamacpp": "llamacpp"}, "base_url": "http://127.0.0.1:8095/v1"}, f)
        with open(os.path.join(home, ".config/opencode/opencode.json"), "w", encoding="utf-8") as f:
            json.dump({"provider": {"llamacpp": {"models": models if models is not None else {}}}}, f)

    def test_connect_tab_label_warns_while_the_configs_are_out_of_date(self) -> None:
        self.assertNotIn("Connect ⚠", self.screen())           # no client set up here
        self.write_client_state({"gone": {}})
        self.assertIn("2 Connect ⚠", self.screen())
        installed = {m["name"]: {} for m in self.store.models if m["status"] == "downloaded"}
        self.write_client_state(installed)
        self.assertNotIn("Connect ⚠", self.screen())

    def test_server_card_sections_at_80_160_and_200_columns(self) -> None:
        """The table and the buttons; the quick tip, About this setting and Status under their own headers (beside
        the table when the card is wide, under it else); no key list in the card (the footer and ? have it)."""
        self.keys("5")
        for cols in (80, 160, 200):
            with self.subTest(cols=cols):
                text = self.server_text(cols)
                heads = {h: next(i for i, x in enumerate(text) if f" {h} ─" in x)
                         for h in ("Quick tip", "About this setting", "Status")}
                self.assertLess(heads["Quick tip"], heads["About this setting"])
                self.assertLess(heads["About this setting"], heads["Status"])
                self.assertFalse(any(" Keys ─" in x for x in text))
                buttons = [i for i, x in enumerate(text) if "[ Start server (a) ]" in x]
                self.assertEqual(len(buttons), 1)
                advanced = next(i for i, x in enumerate(text) if "advanced" in x)
                if cols == 200:                     # beside the table
                    self.assertLess(heads["Quick tip"], advanced)
                else:
                    self.assertGreater(heads["Quick tip"], buttons[0])
                card = text[:next(i for i, x in enumerate(text) if "╰" in x)]
                self.assertFalse(any("…" in x for x in card), [x for x in card if "…" in x])
                joined = " ".join(" ".join(x.strip(" │").split()) for x in card)
                self.assertIn("GGUF from Hugging Face.", joined.replace("│ ", ""))   # the help, in full
        self.assertIn("start", self.app.footer())
        self.assertIn("all keys", self.app.footer())

    def test_every_panel_has_its_keys_a_quick_tip_and_fits(self) -> None:
        """Every Settings panel and Connect sub-tab, at 100, 140 and 200 columns: a quick tip, its own keys
        for the footer, every line within the width; ? shows them all in a card."""
        import shutil
        from unittest import mock
        conf = os.path.join(self.tmp.name, "carl", "config.json")
        self.app.jobs.paths = Paths(repo=self.tmp.name, logs=self.tmp.name, config_file=conf)
        for cols in (100, 140, 200):
            with mock.patch.object(shutil, "get_terminal_size", return_value=os.terminal_size((cols, 60))):
                for tab, sub in [(4, sp) for sp in range(6)] + [(1, 0), (1, 1)]:
                    with self.subTest(cols=cols, tab=tab, sub=sub):
                        self.ui.tab = tab
                        if tab == 4:
                            self.ui.sp = sub
                        else:
                            self.ui.connect_sp = sub
                        lines = self.app.frame(self.ctl.data)
                        self.assertTrue(all(vlen(x) <= cols for x in lines))
                        self.assertTrue(self.ui.keys)
                        if (tab, sub) != (4, 3) or self.store.models:
                            self.assertIn("Quick tip", ANSI.sub("", "\n".join(lines)))
        for tab, sp in [(4, n) for n in range(6)] + [(0, 0), (1, 0), (2, 0), (3, 0)]:   # ? works everywhere
            self.ui.tab, self.ui.sp, self.ui.help = tab, sp, False
            self.keys("?")
            self.assertTrue(self.ui.help, (tab, sp))
        self.ui.help = False
        self.ui.tab, self.ui.sp = 4, 0
        self.keys("?")
        text = ANSI.sub("", self.screen())
        for part in ("KEYS", "This panel", "Every tab", "tuned values", "quit (asks)"):
            self.assertIn(part, text)
        self.keys("?")
        self.assertNotIn("Every tab", ANSI.sub("", self.screen()))


if __name__ == "__main__":
    unittest.main()
