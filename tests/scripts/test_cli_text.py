"""The CLI's text (Phase 21): every command has a help page that fits the terminal (COLUMNS, else 80),
the output uses the glossary's names (docs/phase21/glossary.md) and one unit per kind of number
(GB for files, GiB for memory, K = 1024 tokens), and the launcher's start lines say where each
setting comes from. Runs ./carl.sh with a throw-away home, settings folder, catalogue and models
folder; the launcher with a fake llama-server (never the real one, never port 8080)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest

from _paths import REPO
from test_shell import ServeLlama, gguf_header

COMMANDS = ("llama", "dashboard", "monitor", "install", "models", "fit", "download", "verify", "delete", "card",
            "tune", "config", "cache", "push")
TOPICS = COMMANDS + ("env", "tuning")
# Names the glossary replaced (its "Not this" column) that must not come back in the CLI's text.
FORBIDDEN = (r"\bprompt cache\b", r"\bKV cache\b", r"\bmax ctx\b", r"\bauto-fit\b", r"\bautofit\b", r"\bt/s\b",
             r"\bre-emit", r"\bdrain", r"\bpushed\b", r"\bconversations?\b", r"\bGPU limit\b", r"\(s\)",
             r"\bthe monitor\b", r"\blive monitor\b", r"\bwindow-only\b", r"\bdraft tokens\b", r"\d+(\.\d)?G\b",
             r"\b98304 tokens\b", r"\b98\.3K\b", r"\bcatalog\b(?!\.json)", r"\bspec_n\b(?!:)", r"→")


def plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class CliCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        d = self._tmp.name
        self.conf, self.models = os.path.join(d, "conf"), os.path.join(d, "models")
        os.makedirs(self.models)
        with open(os.path.join(self.models, "Small-Q4_K_M.gguf"), "wb") as f:
            f.write(gguf_header("qwen3"))
        cat = {"schema": 1, "default": "small", "models": [{
            "name": "small", "label": "Small", "summary": "A small test model.", "arch": "dense", "rank": 1,
            "hf": {"repo": "o/r", "revision": "0" * 40, "file": "Small-Q4_K_M.gguf", "sha256": "a" * 64,
                   "bytes": os.path.getsize(os.path.join(self.models, "Small-Q4_K_M.gguf"))},
            "tune": {"kv": "q4_0", "ctx": 98304, "slots": "auto", "spec": "ngram-mod", "spec_n": 2}}]}
        self.catalog = os.path.join(d, "catalog.json")
        with open(self.catalog, "w", encoding="utf-8") as f:
            json.dump(cat, f)
        self.env = {**{k: v for k, v in os.environ.items() if k not in ("API_KEY_FILE", "COLUMNS")}, "HOME": d,
                    "CARL_CONF_DIR": self.conf, "MODELS_DIR": self.models, "CARL_CMD": "./carl.sh",
                    "CARL_CATALOG": self.catalog}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def carl(self, *args: str, cols: int | None = 80) -> subprocess.CompletedProcess[str]:
        env = dict(self.env, **({"COLUMNS": str(cols)} if cols else {}))
        return subprocess.run([os.path.join(REPO, "carl.sh"), *args], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, env=env, timeout=120)

    def assert_fits(self, text: str, cols: int, what: str) -> None:
        long = [ln for ln in plain(text).splitlines() if len(ln) > cols]
        self.assertEqual(long, [], f"{what}: lines over {cols} columns")

    def assert_glossary(self, text: str, what: str) -> None:
        for pat in FORBIDDEN:
            m = re.search(pat, plain(text))
            self.assertIsNone(m, f"{what}: {pat!r} matches {m.group(0) if m else ''!r}")


class HelpTest(CliCase):
    def test_every_command_has_help_that_fits_80_columns(self) -> None:
        """X2: <command> --help and -h work for every command (they printed 'no help topic' for six);
        X3: the help wraps to COLUMNS."""
        for cmd in COMMANDS:
            for args in ((cmd, "--help"), (cmd, "-h"), ("help", cmd)):
                with self.subTest(args=args):
                    p = self.carl(*args)
                    self.assertEqual(p.returncode, 0, p.stderr)
                    self.assertEqual(p.stderr, "")
                    self.assertGreater(len(p.stdout.splitlines()), 3)
                    self.assert_fits(p.stdout, 80, " ".join(args))
                    self.assert_glossary(p.stdout, " ".join(args))

    def test_main_help_and_topics(self) -> None:
        for args in (("-h",), ("--help",), ("help",), *(("help", t) for t in TOPICS)):
            with self.subTest(args=args):
                p = self.carl(*args)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assert_fits(p.stdout, 80, " ".join(args))
                self.assert_glossary(p.stdout, " ".join(args))
        main = self.carl("-h").stdout
        for cmd in COMMANDS:
            self.assertRegex(main, rf"\n  {cmd}\b", cmd)                 # every command is on the main page

    def test_help_wraps_to_the_terminal(self) -> None:
        for cols in (60, 100, 140):
            p = self.carl("help", "llama", cols=cols)
            self.assert_fits(p.stdout, cols, f"help llama at {cols}")
        wide = self.carl("help", "llama", cols=140).stdout
        self.assertLess(len(wide.splitlines()), len(self.carl("help", "llama", cols=60).stdout.splitlines()))
        self.assert_fits(self.carl("help", "fit", cols=None).stdout, 80, "no COLUMNS, not a terminal")

    def test_tools_show_the_same_help(self) -> None:
        for tool, topic in (("llama-fit.py", "fit"), ("carl-tune.py", "tune")):
            p = subprocess.run(["python3", os.path.join(REPO, "tools", tool), "--help"], capture_output=True,
                               text=True, env=dict(self.env, COLUMNS="80"))
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(p.stdout, self.carl("help", topic).stdout)

    def test_unknown_command_and_topic_are_short(self) -> None:
        """X17: an unknown command says so in one line (not the 129-line help on stderr)."""
        p = self.carl("frobnicate")
        self.assertEqual((p.returncode, p.stdout), (2, ""))
        self.assertEqual(p.stderr.strip(), "error: unknown command 'frobnicate'. Run ./carl.sh -h to see the commands.")
        for args in (("help", "nope"), ("nope", "--help")):
            p = self.carl(*args)
            self.assertEqual(p.returncode, 2)
            self.assertEqual(len(p.stderr.splitlines()), 1, p.stderr)
            self.assertTrue(p.stderr.startswith("error: "), p.stderr)


class OutputTest(CliCase):
    def test_models_list(self) -> None:
        """X8: the glossary's words (not downloaded, catalogue), GB for files, wrapped to the width."""
        for cols in (80, 140):
            p = self.carl("models", cols=cols)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assert_fits(p.stdout, cols, f"models at {cols}")
            self.assert_glossary(p.stdout, "models")
            self.assertRegex(p.stdout, r"\nsmall +\d+ kB +downloaded +catalogue")
            self.assertNotIn("missing", p.stdout)

    def test_fit_sentences_units_and_legend(self) -> None:
        """X9-X14: memory in GiB, sentences, a legend for every mark, no "max ctx" or "*" mark."""
        p = self.carl("fit", "--ram", "24")
        self.assertEqual(p.returncode, 0, p.stderr)
        out = plain(p.stdout)
        self.assert_fits(out, 80, "fit")
        self.assert_glossary(out, "fit")
        self.assertIn("A model can use", out)
        self.assertIn("Goal everyday (your goal): small with 2 slots × 96K tokens (q4). It needs", out)
        self.assertRegex(out, r"\n★ small +1 +\d+\.\d GiB")
        for mark in ("★", "rank", "weights", "largest context"):
            self.assertRegex(out, rf"\n  {re.escape(mark)} +[A-Z]", mark)    # the legend explains it
        self.assertNotRegex(out, r"(?m)^ \*")

    def test_card_and_unknown_models(self) -> None:
        """X15, X18: labels and wrapped values; verify, download and card name an unknown model alike."""
        p = self.carl("card", "small")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assert_fits(p.stdout, 80, "card")
        self.assertIn("quality rank", p.stdout)
        for cmd in ("verify", "download", "card", "delete"):
            p = self.carl(cmd, "nope")
            self.assertEqual(p.returncode, 1, cmd)
            self.assertEqual(p.stderr.strip(), "error: unknown model 'nope'. ./carl.sh models lists the models.", cmd)

    def test_config_show_explains_every_key(self) -> None:
        """X16: one line per key with what it does; no "intauto" or Python quoting."""
        p = self.carl("config", "show")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assert_fits(p.stdout, 80, "config show")
        self.assertNotIn("intauto", p.stdout)
        self.assertNotIn("'auto'", p.stdout)
        for key in ("llama.cache_ram", "cache.swa", "models.NAME.spec_n"):
            self.assertRegex(p.stdout, rf"\n  {re.escape(key)} +[A-Z]", key)

    def test_cache_show(self) -> None:
        """X19: GB, no "(s)", the glossary's saved prompts and saved sessions."""
        p = self.carl("cache", "show")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assert_glossary(p.stdout, "cache show")
        self.assertIn("0 saved prompts and 0 saved sessions", " ".join(p.stdout.split()))


class LauncherTextTest(ServeLlama):
    """N1-N3: the start lines in sentences, tokens with K = 1024, each setting with its source; a flag
    is a source (--slots 4 said "slots:auto-tune"); llama.cpp's own memory fitting is off (O31)."""

    def test_start_lines(self) -> None:
        p, argv, _ = self.run_serve("--slots", "3", "--ctx", "64k")
        self.assertEqual(p.returncode, 0, p.stderr)
        out = p.stdout
        self.assertIn("Slots: 3 (from your option). Context: 64K tokens per slot (from your option).", out)
        self.assertRegex(out, r"Context memory type: q4 \(.+\)\. RAM cache: \d+\.\d GiB\.")
        self.assertNotRegex(out, r"\b\d{5,} tokens")
        self.assertNotIn("settings from:", out)
        self.assertEqual(argv[argv.index("--fit") + 1], "off")

    def test_auto_slots_say_what_auto_chose(self) -> None:
        p, _, _ = self.run_serve(env={"SLOTS": "auto"})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertRegex(p.stdout, r"Slots: [12] \(auto: two slots (do not )?fit\)\.")
        p, _, _ = self.run_serve(env={"SLOTS": "2"})
        self.assertIn("Slots: 2 (from the environment).", p.stdout)

    def test_errors_start_with_error(self) -> None:
        """The dashboard shows the launcher's "error:" lines (Z3): every refusal has one."""
        for args, env in ((("--ctx", "1m"), {}), (("--kv", "q5"), {}), (("--slots", "9"), {}),
                          ((), {"FIT_CHECK": "1"}), (("--model", "/nope.gguf"), {})):
            with self.subTest(args=args):
                p, argv, _ = self.run_serve(*args, env=env, model_bytes=4096 * 2 ** 30 if env else 0)
                self.assertNotEqual(p.returncode, 0)
                self.assertEqual(argv, [])
                self.assertRegex(p.stderr, r"(?m)^error: \S.*\.$")


if __name__ == "__main__":
    unittest.main()
