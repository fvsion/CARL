import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from notes import cli


class CliTest(unittest.TestCase):
    def run_cli(self, *args: str) -> str:
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(list(args)), 0)
        return out.getvalue()

    def test_add_list_done(self) -> None:
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"NOTES_FILE": os.path.join(d, "n.json")}):
            self.assertEqual(self.run_cli("add", "Buy milk", "--tag", "home"), "added 1\n")
            self.run_cli("add", "Call Bob")
            self.assertIn("Buy milk  [home]", self.run_cli("list"))
            self.assertEqual(self.run_cli("list", "--tag", "home").count("\n"), 1)
            self.run_cli("done", "1")
            self.assertNotIn("Buy milk", self.run_cli("list"))
            self.assertIn("[x]   1  Buy milk", self.run_cli("list", "--all"))
