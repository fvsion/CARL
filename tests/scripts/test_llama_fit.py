"""tools/llama-fit.py's launcher options: the MTP drafter's weights count with the model's; --plan counts
the speculation and -ub, and drops MTP before a slot (Phase 23.4.4)."""
from __future__ import annotations

import contextlib
import io
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



class PlanTest(unittest.TestCase):
    """--plan prints "SLOTS CACHE_MIB SWA SPEC": MTP goes (n-gram stays) before the second slot."""

    def plan(self, model: str, limit: int, *extra: str) -> str:
        saved = fit.sysctl_int, fit.vm_network_up
        fit.sysctl_int, fit.vm_network_up = (lambda _key: 64 * 2 ** 30), (lambda: False)
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                fit.cmd_plan(fit.parse_args(["--plan", model, "--ctx", "96k", *extra]), limit)
            return out.getvalue().strip()
        finally:
            fit.sysctl_int, fit.vm_network_up = saved

    def test_mtp_goes_before_a_slot(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            model = os.path.join(d, "q.gguf")
            with open(model, "wb") as f:
                f.write(gguf_header("qwen35", nextn=1))
                f.truncate(8 * 2 ** 30)                            # sparse: no disk used
            shape, w = fit.local_shape(model)
            mtp = fit.Spec("draft-mtp,ngram-mod", 1)
            with_mtp = fit.need_bytes(shape, w, 98304, 2, "q4_0", True, mtp)
            ngram = fit.need_bytes(shape, w, 98304, 2, "q4_0", True, mtp.without_mtp())
            spec = ["--spec", "draft-mtp,ngram-mod", "--spec-n", "1"]
            self.assertRegex(self.plan(model, int(with_mtp) + 1, *spec), r"^2 \d+ - draft-mtp,ngram-mod$")
            self.assertRegex(self.plan(model, int((with_mtp + ngram) / 2), *spec), r"^2 \d+ - ngram-mod$")
            self.assertRegex(self.plan(model, int(ngram) - 1, *spec), r"^1 \d+ - (ngram-mod|draft-mtp,ngram-mod)$")
            self.assertRegex(self.plan(model, int(ngram) + 1), r"^2 \d+ - none$")   # no --spec: none
            # a smaller -ub needs smaller compute buffers
            self.assertLess(fit.need_bytes(shape, w, 98304, 2, "q4_0", True, mtp, 256), with_mtp)


if __name__ == "__main__":
    unittest.main()
