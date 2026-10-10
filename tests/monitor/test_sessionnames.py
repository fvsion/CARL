"""The sessions' names next to their IDs (Phase 23.5 item 4): OpenCode's database and Pi's session files, made up."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest

import mon_support  # noqa: F401  (puts tools/ on sys.path)
from monitor import sessionnames as S


class NamesTest(unittest.TestCase):
    def test_opencode_and_pi(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            db = os.path.join(home, ".local", "share", "opencode", "opencode.db")
            os.makedirs(os.path.dirname(db))
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE session (id text PRIMARY KEY, title text NOT NULL)")
            con.executemany("INSERT INTO session VALUES (?, ?)", [("ses_a1", "Fix the date test"), ("ses_b2", "Other")])
            con.commit()
            con.close()
            folder = os.path.join(home, ".pi", "agent", "sessions", "--Users-u-app--")
            os.makedirs(folder)
            lines = [{"type": "session", "version": 3, "id": "u-1"},
                     {"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": "Add a --json flag\nto the CLI"}]}},
                     {"type": "session_info", "name": "First name"}, {"type": "session_info", "name": "Renamed"}]
            with open(os.path.join(folder, "2026-10-10T10-00-00_u-1.jsonl"), "w") as f:
                f.write("\n".join(json.dumps(x) for x in lines) + "\n")
            unnamed = [{"type": "session", "version": 3, "id": "u-2"},
                       {"type": "message", "message": {"role": "user", "content": "x" * 60}}]
            with open(os.path.join(folder, "2026-10-10T11-00-00_u-2.jsonl"), "w") as f:
                f.write("\n".join(json.dumps(x) for x in unnamed) + "\n")
            got = S.names(home, ["ses_a1", "u-1", "u-2", "ses_zz", "u-9", ""])
            self.assertEqual(got, {"ses_a1": "Fix the date test", "u-1": "Renamed", "u-2": "x" * 39 + "…"})

    def test_no_clients_no_names(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            self.assertEqual(S.names(home, ["ses_a1", "u-1"]), {})

    def test_session_id_of_a_file(self) -> None:
        self.assertEqual(S.session_id("carl-session+gemma-4-e4b+deac08ac00+ses_ed95.bin"), "ses_ed95")
        self.assertEqual(S.session_id("carl-prefix+gemma-4-e4b+build+541d0a5c9073.bin"), "")



class PanelTest(unittest.TestCase):
    def test_the_caching_panel_shows_names_and_the_window_row(self) -> None:
        import time
        from monitor.diskcache import CacheConfig, CacheFile
        from monitor.fmt import ANSI
        from monitor.settings_panels.caching import caching_panel, file_rows
        from monitor.state import UIState
        now = time.time()
        files = [CacheFile("carl-session+gemma-4-e4b+deac08ac00+ses_eecda5c9cffeI9FfRq.bin", 144 << 20, now - 18),
                 CacheFile("carl-prefix+gemma-4-e4b+build+541d0a5c9073.bin", 123 << 20, now - 30)]
        text = "\n".join(ANSI.sub("", str(x)) for x in file_rows(files, 5, False, {"ses_eecda5c9cffeI9FfRq": "Fix the date test"}))
        self.assertIn("Fix the date test  ses_eecda5c9…", text)
        self.assertIn("build", text)
        rows = caching_panel(UIState(), CacheConfig(), files, "/tmp/slots", 130, "/home/u", "", 40, {}, "gemma-4-e4b")
        page = "\n".join(ANSI.sub("", r[0] if isinstance(r, tuple) else str(r)) for r in rows)
        self.assertIn("gemma-4-e4b runs with the window cache: OpenCode and Pi save and restore nothing for it.", page)


if __name__ == "__main__":
    unittest.main()
