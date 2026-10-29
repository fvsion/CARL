"""Conversations stored as patches (tools/monitor/slotpack.py): a conversation is stored as a zstd patch against the
prompt file it starts with, made whole again byte for byte, copies and orphans are tidied, and the
disk cache counts and removes every form (diskcache.py). Synthetic files in llama.cpp's header format."""
from __future__ import annotations

import array
import json
import os
import tempfile
import time
import unittest

import mon_support  # noqa: F401  (puts tools/ on sys.path)
from monitor import diskcache, slotpack

PROMPT = "carl-prefix+m+build+abc123.bin"
OTHER = "carl-prefix+m+plan+def456.bin"
SESSION = "carl-session+m+0123456789+ses_1.bin"


def state(tokens: list, body: bytes) -> bytes:
    """A file with llama.cpp's header: magic, version, count, -1, 1, N, the N tokens, then the state."""
    head = array.array("I", [slotpack.MAGIC, 3, len(tokens) + 4, 0xFFFFFFFF, 1, len(tokens)])
    return head.tobytes() + array.array("i", tokens).tobytes() + body


@unittest.skipUnless(slotpack.zstd(), "zstd is not installed")
class PackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        shared = os.urandom(1_500_000)
        self.write(PROMPT, state(list(range(100)), shared))
        self.write(OTHER, state([7] * 50, os.urandom(500_000)))
        self.session = state(list(range(100)) + [5, 6, 7], shared + os.urandom(300_000))
        self.write(SESSION, self.session)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, name: str, data: bytes) -> None:
        with open(os.path.join(self.d, name), "wb") as f:
            f.write(data)

    def test_the_base_is_the_prompt_the_session_starts_with(self) -> None:
        self.assertEqual(slotpack.tokens(os.path.join(self.d, SESSION))[:3], [0, 1, 2])
        self.assertEqual(slotpack.base_for(self.d, SESSION), PROMPT)
        self.write("carl-session+m+0123456789+ses_2.bin", state([9, 9], b"x" * 100))
        self.assertIsNone(slotpack.base_for(self.d, "carl-session+m+0123456789+ses_2.bin"))
        self.write("carl-session+m+0123456789+ses_3.bin", b"not a state")
        self.assertIsNone(slotpack.base_for(self.d, "carl-session+m+0123456789+ses_3.bin"))

    def test_pack_unpack_is_exact_and_smaller(self) -> None:
        self.assertTrue(slotpack.pack(self.d, SESSION))
        self.assertFalse(os.path.exists(os.path.join(self.d, SESSION)))
        packed = os.path.getsize(os.path.join(self.d, SESSION + ".zst"))
        self.assertLess(packed, len(self.session) * 0.4)               # the shared 1.5 MB isn't stored again
        files = {f.name: f for f in diskcache.listing(self.d)}
        self.assertTrue(files[SESSION].packed)
        self.assertEqual((files[SESSION].whole, files[SESSION].base), (len(self.session), PROMPT))
        self.assertGreater(diskcache.shared_saving(list(files.values())), 1_000_000)
        self.assertTrue(slotpack.unpack(self.d, SESSION))
        with open(os.path.join(self.d, SESSION), "rb") as f:
            self.assertEqual(f.read(), self.session)
        t = slotpack.tidy(self.d, now=time.time() + 3600)              # the copy, read long ago, goes
        self.assertEqual((t.copies, os.path.exists(os.path.join(self.d, SESSION))), (1, False))

    def test_a_new_save_is_packed_once_settled(self) -> None:
        self.assertEqual(slotpack.tidy(self.d).packed, 0)               # just written
        self.assertEqual(slotpack.tidy(self.d, now=time.time() + 60).packed, 1)

    def test_removing_a_prompt_takes_its_patches(self) -> None:
        slotpack.pack(self.d, SESSION)
        files = diskcache.listing(self.d)
        gone = diskcache.over_budget(files, sum(f.bytes for f in files) - 1, keep=SESSION)
        if PROMPT not in gone:                                          # make the prompt go: a tiny limit
            gone = diskcache.over_budget(files, 1)
        self.assertIn(PROMPT, gone)
        self.assertIn(SESSION, gone)
        diskcache.remove(self.d, gone)
        self.assertEqual([n for n in os.listdir(self.d) if "ses_1" in n], [])

    def test_an_orphan_patch_is_removed(self) -> None:
        slotpack.pack(self.d, SESSION)
        os.remove(os.path.join(self.d, PROMPT))
        self.assertEqual(slotpack.tidy(self.d).orphans, 1)
        self.assertEqual([n for n in os.listdir(self.d) if "ses_1" in n], [])

    def test_a_meta_that_names_a_path_is_not_used(self) -> None:
        """unpack reads the base from the meta file: only a prompt file in the slots folder counts."""
        for base in ("../carl-prefix+m+a+b.bin", "/etc/passwd", "carl-session+m+k+s.bin"):
            with open(os.path.join(self.d, SESSION + slotpack.META), "w") as f:
                json.dump({"base": base, "size": 1, "packed_at": 1}, f)
            self.assertIsNone(slotpack.meta(self.d, SESSION), base)


if __name__ == "__main__":
    unittest.main()
