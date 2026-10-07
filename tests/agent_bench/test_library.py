"""The model swap: fetch (copy in from the library, SHA-256 checked) and drop (remove the local copy only with a
verified library copy, only catalogue files, never a file on the keep list). Temporary folders and a fake
catalogue only: nothing touches the real library or models folder."""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import unittest
from typing import Any, Dict, List

import _support  # noqa: F401  (the import path)

from agentbench import library, models


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


MODEL = b"model-weights" * 100
DRAFT = b"drafter" * 50
OTHER = b"another" * 10


def catalog() -> Dict[str, Any]:
    return {"models": [
        {"name": "gemma-x", "hf": {"file": "gemma-x-Q4_0.gguf", "sha256": sha(MODEL), "bytes": len(MODEL)},
         "draft": {"file": "mtp-gemma-x-Q4_0.gguf", "sha256": sha(DRAFT), "bytes": len(DRAFT)}},
        {"name": "mine", "hf": {"file": "Mine-Q4_K_M.gguf", "sha256": sha(OTHER), "bytes": len(OTHER)}},
        {"name": "broken", "hf": {"file": "../evil.gguf", "sha256": sha(OTHER), "bytes": 1}},
    ]}


class SwapCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="ab-lib-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.lib = os.path.join(self.tmp, "library")
        self.local = os.path.join(self.tmp, "models")
        os.makedirs(self.lib)
        os.makedirs(self.local)
        self.cat = catalog()
        self.said: List[str] = []

    def say(self, msg: str) -> None:
        self.said.append(msg)

    def put(self, folder: str, name: str, data: bytes) -> str:
        path = os.path.join(folder, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def sums(self, files: Dict[str, bytes]) -> None:
        with open(os.path.join(self.lib, "SHA256SUMS"), "w") as f:
            for name, data in files.items():
                f.write(f"{sha(data)}  {name}\n")

    def library_state(self) -> List[Any]:
        return sorted((n, os.path.getsize(os.path.join(self.lib, n)), os.path.getmtime(os.path.join(self.lib, n)))
                      for n in os.listdir(self.lib))


class CatalogueTest(unittest.TestCase):
    def test_files_of_a_model(self) -> None:
        files = models.model_files(catalog(), "gemma-x")
        self.assertEqual([(f.name, f.role) for f in files],
                         [("gemma-x-Q4_0.gguf", "model"), ("mtp-gemma-x-Q4_0.gguf", "drafter")])
        with self.assertRaises(ValueError):
            models.model_files(catalog(), "nope")
        with self.assertRaises(ValueError):
            models.model_files(catalog(), "broken")          # a path in the file name
        self.assertEqual(sorted(models.catalogue_files(catalog())),
                         ["Mine-Q4_K_M.gguf", "gemma-x-Q4_0.gguf", "mtp-gemma-x-Q4_0.gguf"])

    def test_the_real_catalogue(self) -> None:
        cat = library.load_catalog(models.CATALOG)
        for name in ("qwen3.6-35b-a3b", "qwen3.6-35b-a3b-iq3", "qwen3.8-27b", "orcarouter-27b", "heretic-35b-a3b-iq3",
                     "qwen3.8-9b", "gemma-4-e4b", "gemma-4-12b", "gemma-4-26b-a4b", "gemma-4-31b"):
            files = models.model_files(cat, name)
            self.assertEqual(files[0].role, "model", name)
            self.assertEqual(len(files), 2 if name.startswith("gemma") else 1, name)
        names = models.catalogue_files(cat)
        for keep in models.DEFAULT_KEEP:
            self.assertIn(keep, names)

    def test_sums(self) -> None:
        s = models.parse_sums(f"{'a' * 64}  x.gguf\n{'B' * 64} *dir/y.gguf\nbad line\n")
        self.assertEqual(s, {"x.gguf": "a" * 64, "y.gguf": "b" * 64})


class FetchTest(SwapCase):
    def test_copies_model_and_drafter(self) -> None:
        self.put(self.lib, "gemma-x-Q4_0.gguf", MODEL)
        self.put(self.lib, "mtp-gemma-x-Q4_0.gguf", DRAFT)
        before = self.library_state()
        got = library.fetch(self.cat, "gemma-x", self.lib, self.local, say=self.say, margin=0)
        self.assertEqual([os.path.basename(p) for p in got], ["gemma-x-Q4_0.gguf", "mtp-gemma-x-Q4_0.gguf"])
        with open(got[0], "rb") as f:
            self.assertEqual(f.read(), MODEL)
        self.assertEqual(sorted(os.listdir(self.local)), ["gemma-x-Q4_0.gguf", "mtp-gemma-x-Q4_0.gguf"])
        self.assertEqual(self.library_state(), before)       # the library is only read
        # again: the local copies are checked and kept
        library.fetch(self.cat, "gemma-x", self.lib, self.local, say=self.say, margin=0)
        self.assertIn("gemma-x-Q4_0.gguf: OK (local)", self.said)

    def test_a_bad_library_copy_is_refused(self) -> None:
        self.put(self.lib, "Mine-Q4_K_M.gguf", OTHER[:-1] + b"X")       # the right size, wrong bytes
        with self.assertRaises(library.FetchError):
            library.fetch(self.cat, "mine", self.lib, self.local, say=self.say, margin=0)
        self.assertEqual(os.listdir(self.local), [])                     # no .part left

    def test_missing_or_wrong_size(self) -> None:
        with self.assertRaises(library.FetchError):
            library.fetch(self.cat, "mine", self.lib, self.local, say=self.say, margin=0)
        self.put(self.lib, "Mine-Q4_K_M.gguf", OTHER + b"!")
        with self.assertRaises(library.FetchError):
            library.fetch(self.cat, "mine", self.lib, self.local, say=self.say, margin=0)

    def test_a_wrong_local_copy_is_not_replaced(self) -> None:
        self.put(self.lib, "Mine-Q4_K_M.gguf", OTHER)
        self.put(self.local, "Mine-Q4_K_M.gguf", OTHER[:-1] + b"X")
        with self.assertRaises(library.FetchError):
            library.fetch(self.cat, "mine", self.lib, self.local, say=self.say, margin=0)

    def test_disk_space(self) -> None:
        self.put(self.lib, "Mine-Q4_K_M.gguf", OTHER)
        with self.assertRaises(library.FetchError):
            library.fetch(self.cat, "mine", self.lib, self.local, say=self.say, margin=10 ** 18)


class DropTest(SwapCase):
    def both(self) -> None:
        for folder in (self.lib, self.local):
            self.put(folder, "gemma-x-Q4_0.gguf", MODEL)
            self.put(folder, "mtp-gemma-x-Q4_0.gguf", DRAFT)

    def test_drops_with_a_verified_library_copy(self) -> None:
        self.both()
        self.sums({"gemma-x-Q4_0.gguf": MODEL})                # a SHA256SUMS line for the model only
        self.put(self.local, "not-in-catalogue.gguf", b"x")   # never touched
        before = self.library_state()
        removed = library.drop(self.cat, "gemma-x", self.lib, self.local, [], say=self.say)
        self.assertIn("gemma-x-Q4_0.gguf: removed the local copy (the library has a verified copy)", self.said)
        # the drafter has no SHA256SUMS line: its library copy is hashed (and matches)
        self.assertTrue(any(s.startswith("mtp-gemma-x-Q4_0.gguf: the library has no SHA256SUMS line") for s in self.said))
        self.assertEqual(sorted(os.path.basename(p) for p in removed), ["gemma-x-Q4_0.gguf", "mtp-gemma-x-Q4_0.gguf"])
        self.assertEqual(os.listdir(self.local), ["not-in-catalogue.gguf"])
        self.assertEqual(self.library_state(), before)

    def test_dry_run(self) -> None:
        self.both()
        self.assertEqual(library.drop(self.cat, "gemma-x", self.lib, self.local, [], say=self.say, dry_run=True), [])
        self.assertEqual(len(os.listdir(self.local)), 2)

    def test_keep_list(self) -> None:
        self.put(self.lib, "Mine-Q4_K_M.gguf", OTHER)
        self.put(self.local, "Mine-Q4_K_M.gguf", OTHER)
        self.sums({"Mine-Q4_K_M.gguf": OTHER})
        self.assertEqual(library.drop(self.cat, "mine", self.lib, self.local, ["Mine-Q4_K_M.gguf"], say=self.say), [])
        self.assertTrue(os.path.exists(os.path.join(self.local, "Mine-Q4_K_M.gguf")))
        self.assertIn("keep list", " ".join(self.said))

    def test_default_keep_list(self) -> None:
        cat = library.load_catalog(models.CATALOG)
        for keep in models.DEFAULT_KEEP:
            name = next(m["name"] for m in cat["models"] if m.get("hf", {}).get("file") == keep)
            d = library.plan_drop(cat, name, self.lib, self.local, list(models.DEFAULT_KEEP))
            self.assertFalse(d[0].allowed)
            self.assertIn("keep list", d[0].reason)

    def test_no_library_copy(self) -> None:
        self.put(self.local, "Mine-Q4_K_M.gguf", OTHER)
        self.assertEqual(library.drop(self.cat, "mine", self.lib, self.local, [], say=self.say), [])
        self.assertTrue(os.path.exists(os.path.join(self.local, "Mine-Q4_K_M.gguf")))

    def test_bad_library_copies(self) -> None:
        self.put(self.local, "Mine-Q4_K_M.gguf", OTHER)
        self.put(self.lib, "Mine-Q4_K_M.gguf", OTHER + b"!")                 # wrong size
        self.assertEqual(library.drop(self.cat, "mine", self.lib, self.local, [], say=self.say), [])
        self.put(self.lib, "Mine-Q4_K_M.gguf", OTHER[:-1] + b"X")            # right size, wrong bytes, no SUMS
        self.assertEqual(library.drop(self.cat, "mine", self.lib, self.local, [], say=self.say), [])
        self.put(self.lib, "Mine-Q4_K_M.gguf", OTHER)
        with open(os.path.join(self.lib, "SHA256SUMS"), "w") as f:
            f.write(f"{'0' * 64}  Mine-Q4_K_M.gguf\n")                        # a SUMS line that is not the catalogue's
        self.assertEqual(library.drop(self.cat, "mine", self.lib, self.local, [], say=self.say), [])
        self.assertTrue(os.path.exists(os.path.join(self.local, "Mine-Q4_K_M.gguf")))

    def test_a_link_is_not_removed(self) -> None:
        self.put(self.lib, "Mine-Q4_K_M.gguf", OTHER)
        os.symlink(os.path.join(self.lib, "Mine-Q4_K_M.gguf"), os.path.join(self.local, "Mine-Q4_K_M.gguf"))
        self.assertEqual(library.drop(self.cat, "mine", self.lib, self.local, [], say=self.say), [])
        self.assertTrue(os.path.islink(os.path.join(self.local, "Mine-Q4_K_M.gguf")))

    def test_not_a_catalogue_model(self) -> None:
        self.put(self.local, "random.gguf", b"x")
        with self.assertRaises(ValueError):
            library.drop(self.cat, "random", self.lib, self.local, [], say=self.say)
        self.assertEqual(os.listdir(self.local), ["random.gguf"])

    def test_decision_rules(self) -> None:
        f = models.ModelFile("a.gguf", "a" * 64, 10, "model")
        self.assertFalse(models.drop_decision(f, ["a.gguf"], "file", 10, "a" * 64).allowed)
        self.assertFalse(models.drop_decision(f, [], "missing", 10, "a" * 64).allowed)
        self.assertFalse(models.drop_decision(f, [], "other", 10, "a" * 64).allowed)
        self.assertFalse(models.drop_decision(f, [], "file", None, None).allowed)
        self.assertFalse(models.drop_decision(f, [], "file", 9, "a" * 64).allowed)
        self.assertFalse(models.drop_decision(f, [], "file", 10, "b" * 64).allowed)
        d = models.drop_decision(f, [], "file", 10, None)
        self.assertTrue(d.allowed and d.needs_hash)
        d = models.drop_decision(f, [], "file", 10, "a" * 64)
        self.assertTrue(d.allowed and not d.needs_hash)


if __name__ == "__main__":
    unittest.main()
