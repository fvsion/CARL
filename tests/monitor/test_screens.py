"""Every screen read as text (Phase 21): at 100, 140 and 200 columns, in simple and full detail, with no server,
a server at work and a dialog open. Each line fits the width; no word is repeated next to itself ("fit fits");
the simple detail level uses the glossary's names only (docs/phase21/glossary.md); the footer keeps "? all keys"
and "q quit"; D switches the detail level and the dashboard keeps it for its next start."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import unittest
from typing import Iterator, List, Tuple
from unittest import mock

from mon_support import GIB, FakeStore, shape
from monitor import uiprefs
from monitor.api import Endpoint
from monitor.app import App, Machine
from monitor.cli import Options
from monitor.collector import Collector
from monitor.fmt import ANSI, vlen
from monitor.jobs import Paths, ServerJobs
from monitor.model import ServerData, SlotInfo, SystemStats
from monitor.settings import Schema, SettingsService, net_choices
from monitor.settings_view import SettingsView
from monitor.state import Confirm, UIState

WIDTHS = (100, 140, 200)
CMD = ("llama-server -m /m/big.gguf --alias big --host 127.0.0.1 --port 8095 -c 196608 --parallel 2 "
       "--kv-unified-per-slot 98304 -ctk q4_0 -ctv q4_0 --spec-type draft-mtp,ngram-mod --spec-draft-n-max 1 "
       "--cache-ram 2560 --temp 1.0 --top-k 20 --top-p 0.95 --min-p 0 --presence-penalty 0 --repeat-penalty 1.0 "
       "-ub 512 -fa on --ctx-checkpoints 8 --checkpoint-min-step 4096 --metrics")
# Names the glossary replaced: never in simple detail (docs/phase21/glossary.md, "Not this").
FORBIDDEN = [r"prompt cache", r"\bGENERATING\b", r"\bOFFLINE\b", r"(?<!tok)\bt/s\b", r"\d(\.\d)?G\b(?!i?B)",
             r"\d(\.\d)?M\b(?!i?B)", r"\b98\.3K\b", r"\bN/A\b", r"\bre-emit\b", r"draft tokens", r"ngram-mod",
             r"draft-mtp", r"\bKV cache\b", r"KV quant", r"\bOverview\b", r"\(exp\.\)", r"\bconversations?\b",
             r"\bnot here\b", r"\bslot\(s\)", r"\bfile\(s\)", r"●○|○○|●●"]


def repeated(line: str) -> str:
    """A word next to itself, or next to its plural ("fit fits"), with no punctuation between them; '' when
    none."""
    toks = line.split()
    for a, b in zip(toks, toks[1:]):
        if a[-1:] in ",:;.·/" or not re.fullmatch(r"[A-Za-z][A-Za-z'-]+", a):
            continue
        x, y = a.lower(), re.sub(r"[^A-Za-z'-]", "", b).lower()
        if x == y or y == x + "s":
            return f"{a} {b}"
    return ""


class ScreensTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        home = self.tmp.name
        opts = Options(host="127.0.0.1", port=8095, lines=6, interval=2.0, log=None, server_pid=None, console=None,
                       once=False, tab=0, expand=False, home=home, key_file=os.path.join(home, "key"))
        endpoint = Endpoint("127.0.0.1", 8095, "secretkey4f2a")
        collector = Collector(endpoint, True, opts.key_file, None, None, None, home, 16384)
        collector.gpu_limit = (25 * GIB, "test")
        self.ui = ui = UIState()
        self.store = store = FakeStore()
        os.makedirs(os.path.join(home, "carl"))
        store.config_file = os.path.join(home, "carl", "config.json")
        from monitor.store import ModelList
        models = ModelList(store, lambda msg: ui.toast(msg, 10))
        svc = SettingsService(models, Schema(net_choices([])), "192.168.42.1", lambda: store.limit, ram=32 * GIB)
        paths = Paths(repo=home, logs=os.path.join(home, "logs"), config_file=store.config_file)
        jobs = ServerJobs(ui, collector, svc, paths, "192.168.42.1")
        view = SettingsView(svc, store.config_file, home)
        self.app = App(opts, Machine(16384, 32 * GIB), collector, ui, svc, jobs, view, None, None)
        self.ctl = self.app.ctl

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def server(kind: str = "idle") -> ServerData:
        s0 = SlotInfo(0, False, 812, 98304, 41210, 40990, 220, 1310)
        s1 = SlotInfo(1, False, 799, 98304, 10437, 10437, 0, 695)
        d = ServerData(up=True, pid=4242, target_pid=4242, rss=int(17.9 * GIB), etime="01:12:09", awake=True,
                       cmd=CMD, shape=shape(), health_ms=3.0, slots=True, slot_list=[s0, s1], n_ctx=98304,
                       metrics={"prompt_tokens_total": 182340, "prompt_seconds_total": 341.2,
                                "tokens_predicted_total": 21877, "tokens_predicted_seconds_total": 512.0,
                                "spec_decode_num_draft_tokens_total": 9120,
                                "spec_decode_num_accepted_tokens_total": 6981, "spec_decode_num_drafts_total": 8410},
                       props={"model_alias": "big"},
                       system=SystemStats(wired=19 * GIB, active=6 * GIB, comp=2 * GIB, free=3 * GIB,
                                          pressure="normal", swap=(0.4 * GIB, 2 * GIB), gpu=38, power="AC power"))
        if kind == "writing":
            s0.busy, s0.prompt, s0.cached, s0.processed, s0.decoded = True, 52310, 41210, 11100, 412
            d.busy, d.prompt, d.cached, d.processed, d.decoded, d.tg_rate = True, 52310, 41210, 11100, 412, 38.4
        return d

    def frame(self, d: ServerData, cols: int, rows: int = 50) -> List[str]:
        self.ctl.data = d
        with mock.patch.object(shutil, "get_terminal_size", return_value=os.terminal_size((cols, rows))):
            self.app.frame(d)                       # the first frame sets the keys and the pending values
            return [ANSI.sub("", x) for x in self.app.frame(d)]

    def screens(self) -> Iterator[Tuple[str, ServerData, int]]:
        """(name, snapshot, width) of every tab and Settings panel, with and without a server."""
        for name, d in (("stopped", ServerData()), ("idle", self.server()), ("writing", self.server("writing"))):
            for tab in range(5):
                for sp in (range(6) if tab == 4 else range(2) if tab == 1 else [0]):
                    self.ui.tab, self.ui.sp, self.ui.connect_sp = tab, sp, sp
                    self.ui.pending = None
                    for cols in WIDTHS:
                        yield f"{name} tab {tab + 1} panel {sp} @{cols}", d, cols

    def check(self, detail: str) -> None:
        self.ui.detail = detail
        for name, d, cols in self.screens():
            lines = self.frame(d, cols)
            with self.subTest(screen=name, detail=detail):
                too_long = [x for x in lines if vlen(x) > cols]
                self.assertFalse(too_long, too_long[:2])
                rep = [(x, repeated(x)) for x in lines if repeated(x)]
                self.assertFalse(rep, rep[:2])
                footer = lines[-1]
                self.assertTrue(footer.rstrip().endswith("? all keys · q quit"), footer)
                if detail == "simple":
                    text = "\n".join(lines)
                    for pat in FORBIDDEN:
                        hit = re.search(pat, text)
                        self.assertIsNone(hit, f"{pat!r}: {text[max(hit.start() - 60, 0):hit.end() + 20]!r}"
                                          if hit else "")

    def test_every_screen_in_simple_detail(self) -> None:
        self.check("simple")

    def test_every_screen_in_full_detail(self) -> None:
        self.check("full")

    def test_the_header_says_the_state(self) -> None:
        self.assertIn("○ STOPPED", self.frame(ServerData(), 140)[0])
        self.assertIn("● IDLE", self.frame(self.server(), 140)[0])
        self.assertIn("● WRITING", self.frame(self.server("writing"), 140)[0])
        self.assertIn("up 1 h 12 min", self.frame(self.server(), 140)[0])
        tabs = self.frame(self.server(), 140)[1]
        self.assertIn("[1 Live]", tabs)
        self.assertIn("detail: simple (D)", tabs)

    def test_live_starts_with_the_state_and_the_next_action(self) -> None:
        text = "\n".join(self.frame(ServerData(), 140))
        self.assertIn("The server is not running.", text)
        self.assertIn("To start it: press a", text)
        self.assertIn("It fits:", text)
        self.assertIn("a start the server", self.frame(ServerData(), 140)[-1])
        lines = self.frame(self.server("writing"), 140)
        self.assertEqual(lines[3].strip(), "Writing an answer in slot 0 at 38.4 tok/s. Slot 1 is free. Memory is normal.")
        text = "\n".join(lines)
        for card in ("SLOTS", "SPEED", "MEMORY", "CONNECT", "HEALTH", "RECENT REQUESTS"):
            self.assertIn(f"╭─ {card}", text)
        self.assertIn("40.2K reused (79%)", text)

    def test_a_dialog_shows_its_own_keys(self) -> None:
        self.ui.quit = True
        footer = self.frame(self.server(), 100)[-1]
        self.assertIn("s stop the server and quit", footer)
        self.assertNotIn("1-5", footer)
        self.ui.quit = False
        self.ui.tab = 4
        self.ui.confirm2 = Confirm("DELETE THE MODEL?", ["Delete big?"], "mdelyes", "big", yes_label="Delete")
        lines = self.frame(self.server(), 100)
        self.assertIn("[ Delete (y) ]", "\n".join(lines))
        self.assertEqual(lines[-1].strip(), "y delete · n Esc cancel")

    def test_messages_have_their_own_line(self) -> None:
        self.ui.toast("The dashboard API cannot start: port 8096 is in use.", 600)
        lines = self.frame(self.server(), 100)
        self.assertIn("The dashboard API cannot start", lines[-3])
        self.assertTrue(lines[-1].rstrip().endswith("? all keys · q quit"))
        self.ui.help = True
        self.assertIn("Recent messages", "\n".join(self.frame(self.server(), 140)))

    def test_d_switches_the_detail_and_it_is_kept(self) -> None:
        prefs = uiprefs.path_for(self.store.config_file)
        self.assertEqual(self.app.prefs, prefs)
        self.assertNotIn("Server uses", "".join(x for x in self.frame(self.server(), 140) if "This Mac:" in x))
        self.ctl.handle_input("D")
        self.assertEqual(self.ui.detail, "full")
        self.assertEqual(uiprefs.load_detail(prefs), "full")
        self.assertIn("This Mac:", "\n".join(self.frame(self.server(), 140)))       # MEMORY's full lines
        self.assertIn("detail: full (D)", self.frame(self.server(), 140)[1])
        self.ctl.handle_input("D")
        self.assertEqual(uiprefs.load_detail(prefs), "simple")
        with open(self.store.config_file, "w") as f:
            f.write('{"schema": 1}\n')
        self.ctl.handle_input("D")
        with open(self.store.config_file) as f:
            self.assertEqual(f.read(), '{"schema": 1}\n')            # config.json is never touched
        self.assertEqual(uiprefs.load_detail(os.path.join(self.tmp.name, "nope", "dashboard.json")), "simple")
        with open(prefs, "w") as f:
            f.write("not json")
        self.assertEqual(uiprefs.load_detail(prefs), "simple")       # a broken file: simple

    def test_keys_typed_fast_are_each_handled(self) -> None:
        """"?3" in one read: the ? card opens and tab 3 shows (it was lost in the Settings panels)."""
        self.ui.tab = 4
        self.frame(self.server(), 140)
        self.ctl.handle_input("?3")
        self.assertEqual((self.ui.help, self.ui.tab), (True, 2))
        self.ui.tab, self.ui.sp, self.ui.help = 4, 1, False
        self.frame(self.server(), 140)
        self.ctl.handle_input("\x1b[B\x1b[B]")                       # two rows down, then the next panel
        self.assertEqual((self.ui.mrow, self.ui.sp), (2, 2))

    def test_the_first_start_shows_progress(self) -> None:
        from monitor.app import progress_lines
        text = "\n".join(ANSI.sub("", x) for x in progress_lines(1, 100, 40))
        self.assertIn("✓ Reading the server state", text)
        self.assertIn("… Reading the model list", text)


if __name__ == "__main__":
    unittest.main()
