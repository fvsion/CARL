"""Hidden check for the prompt medium-command (copied in only after the run)."""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class HiddenSearchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.file = os.path.join(self.dir.name, "notes.json")
        for args in (("Buy milk", "--tag", "home"), ("Call Bob",), ("Milk the goat", "--tag", "farm"),
                     ("Oat MILK latte", "--tag", "home")):
            self.assertEqual(self.run_cli("add", *args).returncode, 0)
        self.assertEqual(self.run_cli("done", "4").returncode, 0)

    def tearDown(self) -> None:
        self.dir.cleanup()

    def run_cli(self, *args: str) -> "subprocess.CompletedProcess[str]":
        env = {**os.environ, "NOTES_FILE": self.file}
        return subprocess.run([sys.executable, "-m", "notes", *args], cwd=ROOT, env=env, capture_output=True,
                              text=True, timeout=60)

    def search(self, *args: str) -> "tuple[list[str], int]":
        p = self.run_cli("search", *args)
        self.assertNotIn("Traceback", p.stderr)
        return p.stdout.strip().splitlines(), p.returncode

    def test_open_notes_ignore_case(self) -> None:
        lines, code = self.search("MILK")
        self.assertEqual(code, 0)
        self.assertEqual(lines[-1], "2 found")
        listed = self.run_cli("list").stdout.splitlines()       # the same format as `notes list`
        self.assertEqual(lines[:-1], [ln for ln in listed if "milk" in ln.lower()])

    def test_all(self) -> None:
        lines, code = self.search("milk", "--all")
        self.assertEqual(code, 0)
        self.assertEqual(lines[-1], "3 found")
        self.assertTrue(any("Oat MILK latte" in ln and "[x]" in ln for ln in lines[:-1]), lines)

    def test_tag(self) -> None:
        lines, code = self.search("milk", "--tag", "home")
        self.assertEqual(code, 0)
        self.assertEqual(lines[-1], "1 found")
        self.assertIn("Buy milk", lines[0])
        lines, _ = self.search("milk", "--tag", "home", "--all")
        self.assertEqual(lines[-1], "2 found")

    def test_no_match(self) -> None:
        lines, code = self.search("tractor")
        self.assertEqual(code, 1)
        self.assertEqual(lines, ["0 found"])
