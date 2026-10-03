"""Edit mode for a custom model's card (Settings → Models panel → e), driven by keys and clicks
through the controller with a FakeStore (whose save_card checks with the real domain rules)."""
from __future__ import annotations

import os
import tempfile
import unittest

from mon_support import GIB, FakeStore, custom
from monitor.api import Endpoint
from monitor.app import App, Machine
from monitor.arrange import FILTERS, SORTS
from monitor.card_form import CardForm, form_items
from monitor.card_view import draw_form
from monitor.cli import Options
from monitor.collector import Collector
from monitor.fmt import ANSI
from monitor.jobs import Paths, ServerJobs
from monitor.settings import Schema, SettingsService, net_choices
from monitor.settings_view import SettingsView
from monitor.state import UIState
from monitor.store import ModelList

DOWN, UP, RIGHT, ESC = "\x1b[B", "\x1b[A", "\x1b[C", "\x1b"
ROLE, AGENT, UNCENSORED, WHY, RANK = 1, 2, 5, 6, 13          # rows of the form (one per field, tag, entry)


class CardEditTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        home = self.tmp.name
        opts = Options(host="127.0.0.1", port=8095, lines=6, interval=2.0, log=None, server_pid=None, console=None,
                       once=True, tab=0, expand=False, home=home, key_file=os.path.join(home, "key"))
        collector = Collector(Endpoint("127.0.0.1", 8095, ""), True, opts.key_file, None, None, None, home, 16384)
        self.ui = ui = UIState()
        self.store = store = FakeStore()
        store.models.append(custom("mine"))
        models = ModelList(store, lambda msg: ui.toast(msg, 10))
        svc = SettingsService(models, Schema(net_choices([])), "192.168.42.1", lambda: store.limit)
        jobs = ServerJobs(ui, collector, svc, Paths(home, os.path.join(home, "logs"), store.config_file), "192.168.42.1")
        self.app = App(opts, Machine(16384, 32 * GIB), collector, ui, svc, jobs, SettingsView(svc, store.config_file, home),
                       None, None)
        self.ctl = self.app.ctl
        self.keys("5", "]")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def keys(self, *chunks: str) -> None:
        for c in chunks:
            self.ctl.handle_input(c)
            self.app.frame(self.ctl.data)

    def screen(self) -> str:
        return ANSI.sub("", "\n".join(self.app.frame(self.ctl.data)))

    def select(self, name: str) -> None:
        self.ui.mrow = [m["name"] for m in self.ctl.visible()].index(name)

    def form(self) -> CardForm:
        f = self.ui.card
        assert f is not None
        return f

    def open_mine(self) -> CardForm:
        self.select("mine")
        self.keys("e")
        return self.form()

    def go(self, row: int) -> None:
        f = self.form()
        step = DOWN if row > f.row else UP
        self.keys(*[step] * abs(row - f.row))
        self.assertEqual(f.row, row)

    def test_catalogue_models_are_read_only(self) -> None:
        self.select("big")
        self.keys("e")
        self.assertIsNone(self.ui.card)
        self.assertIn("catalogue models are read-only", self.ui.toast_msg[0])
        self.assertNotIn("Edit card (e)", self.screen())
        self.select("mine")
        self.assertIn("Edit card (e)", self.screen())

    def test_open_edit_toggle_save_and_every_reader_sees_it(self) -> None:
        f = self.open_mine()
        self.assertEqual(f.values, {"arch": "moe", "quant": "Q4_K_M"})            # from the GGUF header
        self.assertIn("EDIT CARD", self.screen())
        self.go(ROLE)
        self.keys("\r", "Fast local coder", "\r")
        self.assertEqual(f.values["role"], "Fast local coder")
        self.go(AGENT)
        self.keys(" ")                                                           # tick "agent coding"
        self.go(RANK)
        self.keys("\r", "1", "\r")
        self.keys("s")
        self.assertIsNone(self.ui.card)
        self.assertEqual(self.store.cards["mine"], {"arch": "moe", "quant": "Q4_K_M", "role": "Fast local coder",
                                                    "good_for": ["agent coding"], "rank": 1})
        self.assertIn("card saved for mine", self.ui.toast_msg[0])
        self.assertIn("Fast local coder [agent coding]", self.screen())         # the list's role and tags
        self.ctl.set_arrangement("filter", FILTERS.index("agent coding"))
        self.assertEqual([m["name"] for m in self.ctl.visible()], ["mine"])     # the fakes have no tags
        self.ctl.set_arrangement("filter", 0)
        self.ctl.set_arrangement("sort", SORTS.index("quality"))
        self.assertEqual(self.ctl.visible()[0]["name"], "mine")                 # rank 1 first
        self.assertEqual(self.ctl.visible()[self.ui.mrow]["name"], "mine")      # still selected
        self.keys("\r")                                                          # use it: the Server panel's MODEL card
        text = self.screen()
        self.assertIn("Fast local coder", text)
        self.assertIn("custom · your card", text)
        self.keys("]", "e")                                                      # the saved card opens again
        self.assertEqual(self.form().values["role"], "Fast local coder")

    def test_invalid_card_shows_the_error_in_the_form(self) -> None:
        f = self.open_mine()
        self.go(UNCENSORED)
        self.keys(" ", "s")
        self.assertIs(self.ui.card, f)                                           # still open
        self.assertEqual(f.error, "only abliterated models may be tagged uncensored")
        self.assertIn("only abliterated models may be tagged uncensored", self.screen())
        self.assertEqual(self.store.cards, {})
        self.go(RANK)
        self.keys("\r", "0", "\r")                                               # not a rank: typing goes on
        self.assertEqual(f.typing, "0")
        self.assertIn("a whole number", f.error)

    def test_esc_drops_the_text_then_cancels_the_card(self) -> None:
        f = self.open_mine()
        self.go(ROLE)
        self.keys("\r", "abc", ESC)
        self.assertIsNone(f.typing)
        self.assertNotIn("role", f.values)
        self.go(WHY)
        self.keys("\r", "kept", "\r", ESC)
        self.assertIsNone(self.ui.card)
        self.assertEqual(self.store.cards, {})
        self.assertIn("nothing saved", self.ui.toast_msg[0])

    def test_typing_takes_every_key_and_a_paste(self) -> None:
        f = self.open_mine()
        self.go(WHY)
        self.keys("\r", "q2[", "\x1b[D", "x\x7f", "e\x03")                      # q, digits, [ and x are text here
        self.assertEqual(f.typing, "q2[e")
        self.assertFalse(self.ui.quit)
        self.assertEqual((self.ui.tab, self.ui.sp), (4, 1))
        self.keys("\x1b[200~line one\nline two\x1b[201~")                        # a bracketed paste: one line
        self.assertEqual(f.typing, "q2[eline one line two")
        self.keys("\r")
        self.assertEqual(f.values["why_use"], "q2[eline one line two")

    def test_keys_outside_typing(self) -> None:
        f = self.open_mine()
        self.go(11)                                                              # arch: a choice
        self.keys(RIGHT)
        self.assertNotIn("arch", f.values)                                       # moe -> (none)
        self.keys(RIGHT)
        self.assertEqual(f.values["arch"], "dense")
        self.keys("x")
        self.assertNotIn("arch", f.values)
        self.keys("]")
        self.assertEqual(self.ui.sp, 1)                                          # stays: save or cancel first
        self.assertIn("press s to save", self.ui.toast_msg[0])
        self.keys("2")                                                           # tabs still switch
        self.assertEqual(self.ui.tab, 1)

    def test_pick_instead_from_a_model_picker(self) -> None:
        f = self.open_mine()
        self.go(len(form_items(f.values)) - 1)                                   # "+ add a model"
        self.keys("\r")
        pk = self.ui.picker
        assert pk is not None
        self.assertEqual(pk.on_pick, "pickcard")
        self.assertNotIn("mine", [v for v, _ in pk.items])
        self.keys(ESC)                                                           # back to the form
        self.assertIsNone(self.ui.picker)
        self.assertIs(self.ui.card, f)
        self.keys("\r")
        pk = self.ui.picker
        assert pk is not None
        i = [v for v, _ in pk.items].index("big")
        self.keys(*[DOWN] * i, "\r", "harder code", "\r")
        self.assertEqual(f.values["pick_instead"], [{"model": "big", "when": "harder code"}])
        self.keys("s")
        self.assertEqual(self.store.cards["mine"]["pick_instead"], [{"model": "big", "when": "harder code"}])

    def test_buttons_click(self) -> None:
        f = self.open_mine()
        self.go(ROLE)
        self.keys("\r", "clicked")
        save = next(r for r in self.ctl.regions if r.action == "cardsave")
        self.keys(f"\x1b[<0;{save.x0};{save.y}M\x1b[<0;{save.x0};{save.y}m")    # Save keeps the typed text
        self.assertIsNone(self.ui.card)
        self.assertEqual(self.store.cards["mine"]["role"], "clicked")
        self.assertEqual(f.values["role"], "clicked")

    def test_the_selected_row_stays_in_view(self) -> None:
        f = CardForm("mine", {"why_use": "word " * 200})
        f.row = len(form_items(f.values)) - 1
        text = ANSI.sub("", "\n".join(t for t, _ in draw_form(f, 80, 24)))
        self.assertIn("+ add a model", text)
        self.assertIn("more line(s) above", text)
        self.assertLessEqual(len(draw_form(f, 80, 24)), 24)


if __name__ == "__main__":
    unittest.main()
