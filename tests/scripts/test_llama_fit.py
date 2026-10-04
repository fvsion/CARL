"""tools/llama-fit.py's launcher options: the MTP drafter's weights count with the model's."""
from __future__ import annotations

import os
import tempfile
import unittest

from _paths import TOOLS, load_script
from test_shell import gguf_header

fit = load_script(os.path.join(TOOLS, "llama-fit.py"), "llama_fit")


class DraftWeightsTest(unittest.TestCase):
    def test_draft_adds_its_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            model, draft = os.path.join(d, "m.gguf"), os.path.join(d, "mtp-m.gguf")
            with open(model, "wb") as f:
                f.write(gguf_header("gemma4"))
            with open(draft, "wb") as f:
                f.write(b"GGUF" + b"\0" * 996)
            shape, alone = fit.local_shape(model)
            _, both = fit.local_shape(model, draft)
            self.assertEqual((shape["nextn"], both - alone), (0, 1000))
            args = fit.parse_args(["--check", model, "--draft", draft])
            self.assertEqual(args.draft, draft)


if __name__ == "__main__":
    unittest.main()
