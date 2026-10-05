"""The dashboard with a model that has a separate MTP drafter (Gemma 4): the speculation colour,
fit counting the drafter, the Models panel (the drafter line, Download for a missing drafter,
the delete question) and the download progress over both files. No subprocess is started."""
from __future__ import annotations

import os
import tempfile
import unittest
from typing import List, Optional
from unittest import mock

from mon_support import GIB, FakeStore, model, model_list, shape
from carl_core.domain.fit import max_ctx
from monitor.api import Endpoint
from monitor.app import App, Machine
from monitor.cli import Options
from monitor.collector import Collector
from monitor.fmt import ANSI, RED
from monitor.jobs import Paths, ServerJobs
from monitor.model import ModelInfo, draft_bytes, drafter_missing
from monitor.settings import Schema, SettingsService, fit_sentence, net_choices
from monitor.settings_panels.models import drafter_line
from monitor.settings_view import SettingsView
from monitor.state import SP_MODELS, Download, UIState

SCHEMA = Schema(net_choices([]))
DRAFT = 60 * 2 ** 20


def gem(draft_status: str = "downloaded", status: str = "downloaded") -> ModelInfo:
    """A Gemma 4 model: no MTP head in its file (the header says mtp False), a drafter beside it."""
    m = model("gem", status=status, mtp=False, size=4 * GIB, tune={"spec": "draft-mtp", "spec_n": 2})
    m.update(draft={"repo": "o/r", "file": "mtp-gem.gguf", "bytes": DRAFT}, draft_path="/m/mtp-gem.gguf",
             draft_status=draft_status)
    return m


class FakeProc:
    def __init__(self, code: Optional[int] = None) -> None:
        self.pid, self.code = 999999, code
        self.returncode: Optional[int] = None

    def poll(self) -> Optional[int]:
        self.returncode = self.code
        return self.code


class ServiceTest(unittest.TestCase):
    def test_helpers(self) -> None:
        self.assertEqual((draft_bytes(gem()), draft_bytes(model("big"))), (DRAFT, 0))
        self.assertEqual((drafter_missing(gem()), drafter_missing(gem("missing")), drafter_missing(model("big"))),
                         (False, True, False))

    def test_mtp_colour_with_a_drafter(self) -> None:
        store = FakeStore(models=[gem(), model("nomtp", mtp=False)])
        svc = SettingsService(model_list(store), SCHEMA, "192.168.42.1", lambda: store.limit)
        p = dict(SCHEMA.defaults(), model="gem", ctx=65536, spec="draft-mtp", specn="2", kv="q4_0")
        self.assertNotEqual(svc.value_color("spec", p), RED)                   # the drafter gives MTP
        self.assertEqual(svc.value_color("spec", dict(p, model="nomtp")), RED)  # no head, no drafter
        store.models[0] = gem("missing")
        svc = SettingsService(model_list(store), SCHEMA, "192.168.42.1", lambda: store.limit)
        self.assertEqual(svc.value_color("spec", p), RED)                       # the start would use n-gram
        store.models[0] = gem("missing", status="missing")                      # a download fetches both
        store.models[0]["mtp"] = True
        svc = SettingsService(model_list(store), SCHEMA, "192.168.42.1", lambda: store.limit)
        self.assertNotEqual(svc.value_color("spec", p), RED)

    def test_fit_counts_the_drafter(self) -> None:
        store = FakeStore(models=[gem()], limit=8 * GIB)
        svc = SettingsService(model_list(store), SCHEMA, "192.168.42.1", lambda: store.limit)
        m = store.models[0]
        self.assertEqual(svc.weights(m), 4 * GIB + DRAFT)
        self.assertEqual(svc.max_ctx(m), max_ctx(shape(), 4 * GIB + DRAFT, 8 * GIB, 1, "q4_0", False))
        p = dict(SCHEMA.defaults(), model="gem", ctx=65536, spec="draft-mtp", specn="2", kv="q4_0", slots="1")
        f = svc.fit_line(p)
        self.assertEqual((f.weights, f.drafter), (4 * GIB, DRAFT))           # the need counts both
        self.assertIn("needs", ANSI.sub("", fit_sentence(f)))


class DashboardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        home = self.tmp.name
        opts = Options(host="127.0.0.1", port=8095, lines=6, interval=2.0, log=None, server_pid=None, console=None,
                       once=True, tab=0, expand=False, home=home, key_file=os.path.join(home, "key"))
        collector = Collector(Endpoint("127.0.0.1", 8095, ""), True, opts.key_file, None, None, None, home, 16384)
        self.ui = ui = UIState()
        self.store = store = FakeStore(models=[gem("missing")])
        models = model_list(store)
        svc = SettingsService(models, SCHEMA, "192.168.42.1", lambda: store.limit)
        self.paths = Paths(repo=home, logs=os.path.join(home, "logs"), config_file=store.config_file)
        self.jobs = ServerJobs(ui, collector, svc, self.paths, "192.168.42.1")
        self.app = App(opts, Machine(16384, 32 * GIB), collector, ui, svc, self.jobs,
                       SettingsView(svc, store.config_file, home), None, None)
        self.ctl = self.app.ctl
        self.app.frame(self.ctl.data)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def keys(self, *chunks: str) -> None:
        for c in chunks:
            self.ctl.handle_input(c)
            self.app.frame(self.ctl.data)

    def text(self) -> str:
        return " ".join(ANSI.sub("", "\n".join(self.app.frame(self.ctl.data))).split())

    def test_models_panel_shows_a_missing_drafter_and_offers_download(self) -> None:
        self.keys("5", "]")
        self.assertEqual(self.ui.sp, SP_MODELS)
        text = self.text()
        self.assertIn("drafter", text)
        self.assertIn("not downloaded", text)
        self.assertIn("Download (d)", text)
        self.assertIn("Press d to download its MTP drafter", text)
        self.assertIn("speculation uses n-gram only", drafter_line(self.store.models[0], "/home/u", False))

    def test_download_of_only_the_drafter(self) -> None:
        with mock.patch("monitor.jobs.start_tool", return_value=FakeProc()) as start:
            self.jobs.start_download("gem")
        self.assertEqual(start.call_args.args[0][1:], ["download", "gem"])
        dl = self.ui.dl
        assert dl is not None
        self.assertEqual((dl.name, dl.path, dl.total, dl.more), ("gem MTP drafter", "/m/mtp-gem.gguf", DRAFT, []))

    def test_download_of_the_model_and_its_drafter(self) -> None:
        self.store.models[0] = gem("missing", status="missing")
        self.app.svc.models.get(refresh=True)
        with mock.patch("monitor.jobs.start_tool", return_value=FakeProc()):
            self.jobs.start_download("gem")
        dl = self.ui.dl
        assert dl is not None
        self.assertEqual((dl.path, dl.total, dl.more), ("/m/gem.gguf", 4 * GIB + DRAFT, ["/m/mtp-gem.gguf"]))

    def test_progress_counts_every_file(self) -> None:
        files: List[str] = []
        for name, n in (("a.gguf", 300), ("mtp-a.gguf", 50)):
            files.append(os.path.join(self.tmp.name, name))
            with open(files[-1], "wb") as f:
                f.write(b"x" * n)
        log = os.path.join(self.tmp.name, "dl.out")
        with open(log, "w") as f:
            f.write("downloading\n")
        self.ui.dl = Download(name="a", path=files[0], total=400, proc=FakeProc(), log=log, more=files[1:])
        self.jobs.poll()
        self.assertEqual(self.ui.dl.have, 350)

    def test_delete_question_names_the_drafter(self) -> None:
        self.store.models[0] = gem()
        self.app.svc.models.get(refresh=True)
        self.keys("5", "]", "x")
        confirm = self.ui.confirm2
        assert confirm is not None
        self.assertIn("Its MTP drafter goes too", " ".join(confirm.lines))


if __name__ == "__main__":
    unittest.main()
