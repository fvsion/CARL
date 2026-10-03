"""host/gguf-chat-template.py on synthetic GGUF headers (format: GGUF v3 metadata)."""
from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile
import unittest

from _paths import HOST, load_script

SCRIPT = os.path.join(HOST, "gguf-chat-template.py")
gct = load_script(SCRIPT, "gguf_chat_template")


def gstr(s: str) -> bytes:
    b = s.encode()
    return struct.pack("<Q", len(b)) + b


def gguf(template: str | None) -> bytes:
    """A GGUF header with a few metadata types in front of the template."""
    kv = [
        gstr("general.architecture") + struct.pack("<I", 8) + gstr("qwen3"),
        gstr("general.file_type") + struct.pack("<I", 4) + struct.pack("<I", 15),
        gstr("x.float") + struct.pack("<I", 6) + struct.pack("<f", 1.5),
        gstr("x.u64") + struct.pack("<I", 10) + struct.pack("<Q", 7),
        gstr("tokenizer.ggml.tokens") + struct.pack("<I", 9) + struct.pack("<I", 8) + struct.pack("<Q", 2)
        + gstr("a") + gstr("bc"),
        gstr("tokenizer.ggml.token_type") + struct.pack("<I", 9) + struct.pack("<I", 5) + struct.pack("<Q", 3)
        + struct.pack("<3i", 1, 1, 3),
    ]
    if template is not None:
        kv.append(gstr("tokenizer.chat_template") + struct.pack("<I", 8) + gstr(template))
    return b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", len(kv)) + b"".join(kv)


class ChatTemplateTests(unittest.TestCase):
    def test_finds_template_after_arrays(self) -> None:
        self.assertEqual(gct.chat_template(gguf("{{ enable_thinking }}")), "{{ enable_thinking }}")

    def test_no_template(self) -> None:
        self.assertIsNone(gct.chat_template(gguf(None)))

    def test_not_gguf(self) -> None:
        with self.assertRaises(gct.GGUFError):
            gct.chat_template(b"PK\x03\x04 not a model")

    def test_truncated(self) -> None:
        with self.assertRaises(gct.GGUFError):
            gct.chat_template(gguf("{{ enable_thinking }}")[:60])

    def test_patch(self) -> None:
        out = gct.patched("{% if enable_thinking %}x{% endif %}")
        self.assertIsNotNone(out)
        assert out is not None
        self.assertTrue(out.startswith(gct.PATCH))
        self.assertIsNone(gct.patched("no switch here"))


class CliTests(unittest.TestCase):
    def run_script(self, data: bytes) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as d:
            model, out = os.path.join(d, "m.gguf"), os.path.join(d, "t.jinja")
            with open(model, "wb") as f:
                f.write(data)
            p = subprocess.run([sys.executable, SCRIPT, model, out], capture_output=True, text=True)
            text = ""
            if os.path.exists(out):
                with open(out, encoding="utf-8") as f:
                    text = f.read()
            return p.returncode, text, p.stderr

    def test_writes_patched_template(self) -> None:
        rc, text, _ = self.run_script(gguf("{%- if enable_thinking %}think{% endif %}"))
        self.assertEqual(rc, 0)
        self.assertTrue(text.startswith(gct.PATCH) and text.endswith("think{% endif %}"))

    def test_exit_3_without_switch(self) -> None:
        rc, text, _ = self.run_script(gguf("plain"))
        self.assertEqual((rc, text), (3, ""))

    def test_not_gguf_message(self) -> None:
        rc, _, err = self.run_script(b"hello")
        self.assertEqual(rc, 1)
        self.assertIn("not a GGUF file", err)


if __name__ == "__main__":
    unittest.main()
