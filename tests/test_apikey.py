"""Where CARL's files are: the API key (API_KEY_FILE, the CARL path, then the earlier
ones) and the settings folder (CARL_CONF_DIR, ~/.config/carl, the folder's old name)."""
from __future__ import annotations

import os
import stat
import tempfile
import unittest

from support import HOME  # also puts tools/ on the import path
from carl_core.adapters.api_key import has_key, key_file, read_key
from carl_core.domain.apikey import server_key_file
from carl_core.domain.confdir import conf_dir
from carl_core.wiring import CarlPaths

NEW = "/home/u/.config/carl/api-key"
RENAMED = "/home/u/.config/llm-deploy/api-key"      # before the rename (1.2.0)
OLD = "/home/u/.mtplx/api-key"                      # before 1.2.0 (MTPLX)


class ServerKeyFileTest(unittest.TestCase):
    def test_new_path_wins(self) -> None:
        self.assertEqual(server_key_file(HOME, {}, lambda p: True), NEW)

    def test_old_path_until_migrated(self) -> None:
        self.assertEqual(server_key_file(HOME, {}, lambda p: p == OLD), OLD)

    def test_chain_newest_first(self) -> None:
        self.assertEqual(server_key_file(HOME, {}, lambda p: p in (RENAMED, OLD)), RENAMED)
        self.assertEqual(server_key_file(HOME, {}, lambda p: p in (NEW, RENAMED, OLD)), NEW)

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
        self.write(".config/carl/api-key", "")                        # empty: not a key yet
        self.assertEqual(key_file({}, self.home), old)
        renamed = self.write(".config/llm-deploy/api-key", "renamed-key")
        self.assertEqual(key_file({}, self.home), renamed)
        new = self.write(".config/carl/api-key", "new-key")
        self.assertEqual(key_file({}, self.home), new)
        self.assertEqual(read_key(new), "new-key")

    def test_missing_files(self) -> None:
        self.assertFalse(has_key(os.path.join(self.home, "nope")))
        self.assertEqual(read_key(os.path.join(self.home, "nope")), "")


class ConfDirTest(unittest.TestCase):
    CARL, LEGACY = "/home/u/.config/carl", "/home/u/.config/llm-deploy"

    def test_new_folder_wins(self) -> None:
        self.assertEqual(conf_dir(HOME, {}, lambda p: True), self.CARL)

    def test_old_folder_until_moved(self) -> None:
        self.assertEqual(conf_dir(HOME, {}, lambda p: p == self.LEGACY), self.LEGACY)

    def test_neither_means_the_new_folder(self) -> None:
        self.assertEqual(conf_dir(HOME, {}, lambda p: False), self.CARL)

    def test_env_override(self) -> None:
        self.assertEqual(conf_dir(HOME, {"CARL_CONF_DIR": "~/conf"}, lambda p: p == self.LEGACY), "/home/u/conf")
        self.assertEqual(conf_dir(HOME, {"CARL_CONF_DIR": " "}, lambda p: False), self.CARL)

    def test_paths_from_env(self) -> None:
        paths = CarlPaths.from_env({}, HOME, lambda p: p == self.LEGACY)
        self.assertEqual((paths.config, paths.local, paths.legacy_llama),
                         (self.LEGACY + "/config.json", self.LEGACY + "/models.json", self.LEGACY + "/llama.env"))
        self.assertEqual(CarlPaths.from_env({}, HOME, lambda p: True).config, self.CARL + "/config.json")


if __name__ == "__main__":
    unittest.main()
