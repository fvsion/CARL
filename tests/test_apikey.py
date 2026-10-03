"""Where the server's API key is read from: API_KEY_FILE, the CARL path, the pre-1.2.0 path."""
from __future__ import annotations

import os
import stat
import tempfile
import unittest

from support import HOME  # also puts tools/ on the import path
from carl_core.adapters.api_key import has_key, key_file, read_key
from carl_core.domain.apikey import server_key_file

NEW = "/home/u/.config/llm-deploy/api-key"
OLD = "/home/u/.mtplx/api-key"


class ServerKeyFileTest(unittest.TestCase):
    def test_new_path_wins(self) -> None:
        self.assertEqual(server_key_file(HOME, {}, lambda p: True), NEW)

    def test_old_path_until_migrated(self) -> None:
        self.assertEqual(server_key_file(HOME, {}, lambda p: p == OLD), OLD)

    def test_neither_exists_means_the_new_path(self) -> None:
        self.assertEqual(server_key_file(HOME, {}, lambda p: False), NEW)

    def test_env_override_is_expanded_and_never_falls_back(self) -> None:
        self.assertEqual(server_key_file(HOME, {"API_KEY_FILE": "~/k"}, lambda p: p == OLD), "/home/u/k")
        self.assertEqual(server_key_file(HOME, {"API_KEY_FILE": "  "}, lambda p: False), NEW)


class KeyFileAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, rel: str, text: str) -> str:
        p = os.path.join(self.home, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
        return p

    def test_reads_the_old_key_then_the_new_one(self) -> None:
        old = self.write(".mtplx/api-key", "old-key\n")
        self.assertEqual(key_file({}, self.home), old)
        self.assertEqual(read_key(key_file({}, self.home)), "old-key")
        self.write(".config/llm-deploy/api-key", "")                 # empty: not a key yet
        self.assertEqual(key_file({}, self.home), old)
        new = self.write(".config/llm-deploy/api-key", "new-key")
        self.assertEqual(key_file({}, self.home), new)
        self.assertEqual(read_key(new), "new-key")

    def test_missing_files(self) -> None:
        self.assertFalse(has_key(os.path.join(self.home, "nope")))
        self.assertEqual(read_key(os.path.join(self.home, "nope")), "")


if __name__ == "__main__":
    unittest.main()
