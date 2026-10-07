"""Hidden check for the prompt large-cli (copied in only after the run)."""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEXT = "The cat sat. The cat ran! A dog sat?"


class HiddenCliTest(unittest.TestCase):
    def run_cli(self, *args: str) -> "subprocess.CompletedProcess[str]":
        return subprocess.run([sys.executable, "-m", "textstats", *args], cwd=ROOT, capture_output=True,
                              text=True, timeout=60)

    def setUp(self) -> None:
        fd, self.path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w") as f:
            f.write(TEXT)

    def tearDown(self) -> None:
        os.unlink(self.path)

    def test_count(self) -> None:
        p = self.run_cli("count", self.path)
        self.assertEqual(p.returncode, 0, p.stderr)
        lines = p.stdout.split("\n")
        self.assertIn("words: 9", lines)
        self.assertIn("sentences: 3", lines)
        self.assertIn(f"characters: {len(TEXT)}", lines)

    def test_top(self) -> None:
        p = self.run_cli("top", self.path, "-n", "2")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual([ln.split() for ln in p.stdout.strip().splitlines()], [["cat", "2"], ["sat", "2"]])

    def test_sentences(self) -> None:
        p = self.run_cli("sentences", self.path)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip().splitlines()[0], "1: The cat sat.")
        self.assertEqual(len(p.stdout.strip().splitlines()), 3)

    def test_missing_file(self) -> None:
        p = self.run_cli("count", self.path + ".missing")
        self.assertEqual(p.returncode, 2)
        self.assertNotIn("Traceback", p.stderr)
