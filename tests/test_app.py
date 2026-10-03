"""Application services with fake ports: config migration, launch resolution, default
choice, downloads (no traversal), verify and delete."""
from __future__ import annotations

import unittest

from support import GIB, MDIR, SHA_A, FakeHub, FakeLegacy, FakeShapes, World, catalog, entry, shape
from carl_core.domain.errors import ConfigError
from carl_core.domain.settings import Config
from carl_core.domain.types import JsonValue, ModelInfo

BIG = entry("big", "Big-Q4.gguf", 20 * GIB)
SMALL = entry("small", "Small-IQ3.gguf", 10 * GIB)
SMALL_PATH = f"{MDIR}/Small-IQ3.gguf"


class ConfigTest(unittest.TestCase):
    def test_migrates_llama_env_once(self) -> None:
        w = World(catalog(BIG, SMALL), legacy=FakeLegacy({"MODEL_NAME": "small", "NET": "vm", "CTX": "64k"}))
        cfg = w.carl.load_config()
        self.assertEqual(cfg.llama, {"model": "small", "net": "vm"})
        self.assertEqual(len(w.config.saved), 1)
        self.assertEqual(w.carl.load_config(), cfg)          # read back from config.json, not migrated again
        self.assertEqual(len(w.config.saved), 1)

    def test_pre_1_2_config_with_mtplx_loads(self) -> None:
        """A config.json written while CARL had MTPLX: it loads, says what it ignores, and
        the next save drops the removed keys."""
        old = {"schema": 1, "backend": "mtplx", "llama": {"net": "vm"},
               "mtplx": {"preset": "pocket", "context": 49152, "kv_quant": "off"}}
        w = World(catalog(BIG, SMALL), config=old)
        cfg = w.carl.load_config()
        self.assertEqual(cfg.llama, {"net": "vm"})
        self.assertEqual(len(w.carl.config_warnings()), 1)
        self.assertIn("MTPLX support was removed", w.carl.config_warnings()[0])
        w.carl.save_config(cfg)
        self.assertEqual({k for k in w.config.doc} if isinstance(w.config.doc, dict) else set(),
                         {"schema", "_comment", "llama"})
        self.assertEqual(w.carl.config_warnings(), [])

    def test_no_config_at_all(self) -> None:
        w = World(catalog(BIG, SMALL))
        self.assertEqual(w.carl.load_config(), Config())
        self.assertEqual(w.config.saved, [])

    def test_bad_config_is_an_error(self) -> None:
        w = World(catalog(BIG, SMALL), config={"llama": {"net": "moon"}})
        with self.assertRaisesRegex(ConfigError, "llama.net"):
            w.carl.load_config()

    def test_models_dir_from_env_and_config(self) -> None:
        w = World(catalog(BIG), config={"paths": {"models_dir": "~/other"}})
        self.assertEqual(w.carl.models_dir(w.carl.load_config()), "/home/u/other")
        w = World(catalog(BIG), env_models_dir="/env/gguf")
        self.assertEqual(w.carl.models_dir(Config()), "/env/gguf")


class LaunchTest(unittest.TestCase):
    def world(self, config: object = None, gpu: int = 24 * GIB) -> World:
        return World(catalog(BIG, SMALL), files={SMALL_PATH: 10 * GIB}, config=config, gpu=gpu,
                     shapes=FakeShapes(local={SMALL_PATH: shape()}, remote={"Big-Q4.gguf": shape(kv_elems=8192)}))

    def test_default_not_downloaded_falls_back_with_a_note(self) -> None:
        w = self.world(gpu=48 * GIB)
        m, _, note = w.carl.resolve_launch(None, w.carl.load_config())
        self.assertEqual(m["name"], "small")
        self.assertEqual(note, "default model big is not downloaded; using small (downloaded)")

    def test_default_small_when_the_big_one_does_not_fit(self) -> None:
        w = self.world(gpu=16 * GIB)
        models = w.carl.all_models(Config())
        self.assertEqual(w.carl.pick_default(models), "small")
        self.assertEqual(w.carl.pick_default(models, limit=48 * GIB), "big")

    def test_offline_keeps_the_default(self) -> None:
        w = World(catalog(BIG, SMALL), gpu=1)
        self.assertEqual(w.carl.pick_default(w.carl.all_models(Config())), "big")

    def test_launch_env_uses_config(self) -> None:
        w = self.world(config={"llama": {"model": "small", "net": "local"}, "models": {"small": {"ctx": "128k"}}})
        env, note = w.carl.launch_env(None, use_config=True)
        self.assertIsNone(note)
        self.assertEqual((env["MODEL"], env["CTX"], env["NET"], env["SPEC_N"]), (SMALL_PATH, 131072, "local", 2))
        self.assertTrue(str(env["CARL_SOURCES"]).startswith("ctx:config kv:catalogue"))
        env, _ = w.carl.launch_env("small", use_config=False)
        self.assertEqual((env["CTX"], "NET" in env), (98304, False))

    def test_launch_env_unknown_model(self) -> None:
        with self.assertRaisesRegex(ConfigError, "unknown model 'nope'"):
            self.world().carl.launch_env("nope", use_config=True)


class DownloadTest(unittest.TestCase):
    TREE: JsonValue = [{"type": "file", "path": "sub/Custom-Q4.gguf", "size": 1000, "lfs": {"oid": SHA_A}}]

    def test_hf_download_pins_records_and_verifies(self) -> None:
        hub = FakeHub({"models/o/r/revision/main": {"sha": "c" * 40},
                       f"models/o/r/tree/{'c' * 40}?recursive=true": self.TREE})
        w = World(catalog(BIG), hub=hub, download_size=1000)
        self.assertTrue(w.carl.download_hf("hf:o/r/sub/Custom-Q4.gguf"))
        url, directory, name = w.downloader.calls[0]
        self.assertEqual((url, directory, name),
                         (f"https://huggingface.co/o/r/resolve/{'c' * 40}/sub/Custom-Q4.gguf", MDIR, "Custom-Q4.gguf"))
        saved = w.carl.load_local()["models"]["custom-q4"]
        self.assertEqual(saved.get("path"), f"{MDIR}/Custom-Q4.gguf")
        self.assertEqual(saved.get("hf", {}).get("revision"), "c" * 40)
        self.assertEqual(saved.get("added"), "2026-10-03")
        self.assertIn("  custom-q4: OK " + SHA_A, w.console.lines)

    def test_traversal_never_reaches_the_downloader(self) -> None:
        w = World(catalog(BIG))
        bad: ModelInfo = {"name": "x", "hf": {"repo": "o/r", "file": "../../.ssh/authorized_keys.gguf", "bytes": 1}}
        with self.assertRaises(ConfigError):
            w.carl.download(bad, MDIR)
        with self.assertRaises(ConfigError):
            w.carl.download_hf("hf:o/r/../x.gguf")
        self.assertEqual(w.downloader.calls, [])

    def test_not_enough_disk(self) -> None:
        w = World(catalog(BIG))
        w.folder.free = 10 * GIB
        m = w.carl.all_models(Config())[0]
        self.assertFalse(w.carl.download(m, MDIR))
        self.assertIn("needs 21.5 GB + 5 GB headroom", w.console.errors[0])

    def test_bad_checksum_moves_the_file_aside(self) -> None:
        w = World(catalog(SMALL), download_size=10 * GIB)
        w.folder.hashes[SMALL_PATH] = "b" * 64
        self.assertFalse(w.carl.download(w.carl.all_models(Config())[0], MDIR))
        self.assertIn(SMALL_PATH + ".bad", w.folder.files)
        self.assertIn("MISMATCH", w.console.errors[0])

    def test_repo_listing_without_a_file(self) -> None:
        hub = FakeHub({"models/o/r/tree/main?recursive=true": self.TREE})
        w = World(catalog(BIG), hub=hub)
        self.assertFalse(w.carl.download_hf("hf:o/r"))
        self.assertIn("sub/Custom-Q4.gguf", w.console.lines[1])


class DeleteVerifyTest(unittest.TestCase):
    def test_delete_custom_forgets_it(self) -> None:
        path = f"{MDIR}/Mine.gguf"
        w = World(catalog(BIG), files={path: 5, path + ".aria2": 1},
                  local={"schema": 1, "models": {"mine": {"path": path, "source": "hf"}}})
        m = w.carl.find("mine", w.carl.all_models(Config()))
        assert m is not None
        w.carl.delete(m)
        self.assertEqual(sorted(w.folder.removed), [path, path + ".aria2"])
        self.assertEqual(w.carl.load_local()["models"], {})

    def test_delete_refuses_non_gguf_paths(self) -> None:
        w = World(catalog(BIG), files={"/etc/passwd": 1})
        with self.assertRaisesRegex(ConfigError, "refusing to delete"):
            w.carl.delete({"name": "x", "path": "/etc/passwd", "custom": True})
        self.assertEqual(w.folder.removed, [])

    def test_verify(self) -> None:
        w = World(catalog(SMALL), files={SMALL_PATH: 10 * GIB, f"{MDIR}/local.gguf": 1})
        ms = {m["name"]: m for m in w.carl.all_models(Config())}
        self.assertTrue(w.carl.verify(ms["small"]))
        self.assertTrue(w.carl.verify(ms["local"]))                 # no checksum known: skipped
        self.assertFalse(w.carl.verify({"name": "gone", "status": "missing", "path": ""}))
        self.assertIn("  local: no checksum known (a local file): skipped", w.console.lines)


if __name__ == "__main__":
    unittest.main()
