"""Hidden check for the prompt large-feature (copied in only after the run)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class HiddenDueTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.file = os.path.join(self.dir.name, "notes.json")

    def tearDown(self) -> None:
        self.dir.cleanup()

    def run_cli(self, *args: str) -> "subprocess.CompletedProcess[str]":
        env = {**os.environ, "NOTES_FILE": self.file}
        return subprocess.run([sys.executable, "-m", "notes", *args], cwd=ROOT, env=env, capture_output=True,
                              text=True, timeout=60)

    def test_due_dates(self) -> None:
        self.assertEqual(self.run_cli("add", "Alpha", "--due", "2000-01-01").returncode, 0)
        self.assertEqual(self.run_cli("add", "Beta", "--due", "2999-01-01").returncode, 0)
        self.assertEqual(self.run_cli("add", "Gamma").returncode, 0)
        out = self.run_cli("list").stdout
        self.assertIn("(due 2000-01-01)", out)
        self.assertIn("Gamma", out)
        over = self.run_cli("list", "--overdue").stdout
        self.assertIn("Alpha", over)
        self.assertNotIn("Beta", over)
        self.assertNotIn("Gamma", over)
        due = self.run_cli("due").stdout
        self.assertLess(due.index("Alpha"), due.index("Beta"))
        self.assertNotIn("Gamma", due)

    def test_bad_date(self) -> None:
        self.assertEqual(self.run_cli("add", "X", "--due", "2026-13-45").returncode, 2)

    def test_old_file_without_due(self) -> None:
        with open(self.file, "w") as f:
            json.dump({"version": 2, "notes": [{"id": 1, "text": "Old", "tags": [], "done": False}]}, f)
        p = self.run_cli("list")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("Old", p.stdout)
