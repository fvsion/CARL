"""The JavaScript tests (tests/js: client/shared/carl-cache.js) through node's test runner; skipped
without node (the clients need it anyway: OpenCode and Pi are node / Bun programs)."""
from __future__ import annotations

import os
import shutil
import subprocess
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JS_TESTS = os.path.join(REPO, "tests", "js")


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class JsTest(unittest.TestCase):
    def test_node_suite(self) -> None:
        files = sorted(os.path.join(JS_TESTS, n) for n in os.listdir(JS_TESTS) if n.endswith(".test.mjs"))
        p = subprocess.run(["node", "--test", *files], capture_output=True, text=True, timeout=300, cwd=REPO)
        self.assertEqual(p.returncode, 0, p.stdout[-4000:] + p.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
