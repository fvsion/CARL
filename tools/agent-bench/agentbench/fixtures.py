"""The fixture projects: a fresh copy for every run (its own folder, `git init`, one commit), and the hidden
tests of a full run (copied in only after the run, then run)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Mapping, Sequence

from . import BENCH_DIR
from .proc import run, tail

FIXTURES_DIR = os.path.join(BENCH_DIR, "fixtures")
HIDDEN_DIR = os.path.join(BENCH_DIR, "hidden")
# The folder name of each fixture's copy: the project's own name (the agent sees it as its working folder).
PROJECT_NAMES: Dict[str, str] = {"pycli": "notes", "pylib": "textstats", "webapp": "tiny-todo", "empty": "newpkg",
                                 "pyrules": "notes"}           # pycli with an AGENTS.md (Phase 23.1)
_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".DS_Store", ".mypy_cache")


def git_env() -> Dict[str, str]:
    """git with no user or system config (no hooks, no templates, no signing from the user's setup)."""
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "TMPDIR")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, HOME=tempfile.gettempdir(),
               GIT_AUTHOR_NAME="agent-bench", GIT_AUTHOR_EMAIL="agent-bench@localhost",
               GIT_COMMITTER_NAME="agent-bench", GIT_COMMITTER_EMAIL="agent-bench@localhost")
    return env


def make_fresh(fixture: str, parent: str, source_dir: str = FIXTURES_DIR) -> str:
    """A new copy of the fixture under parent (in a new folder), with git and one commit. Returns its path."""
    if fixture not in PROJECT_NAMES:
        raise ValueError(f"unknown fixture {fixture!r}")
    src = os.path.join(source_dir, fixture)
    os.makedirs(parent, exist_ok=True)
    box = tempfile.mkdtemp(prefix=f"{fixture}-", dir=parent)
    dst = os.path.join(box, PROJECT_NAMES[fixture])
    shutil.copytree(src, dst, ignore=_IGNORE)
    env = git_env()
    for argv in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
                 ["git", "commit", "-q", "--no-verify", "-m", "Initial commit"]):
        subprocess.run(argv, cwd=dst, env=env, check=True, capture_output=True, timeout=60)
    return dst


def changed_files(repo: str) -> List[str]:
    """The files that differ from the first commit (new, changed, deleted), as git shows them."""
    code, out = run(["git", "status", "--porcelain", "--untracked-files=all"], repo, git_env(), 60)
    if code != 0:
        return []
    return sorted(line[3:].strip() for line in out.splitlines() if len(line) > 3
                  and "__pycache__" not in line and ".pytest_cache" not in line)


@dataclass
class CheckResult:
    passed: bool
    output: str
    command: str


def has_pytest(python: str = sys.executable) -> bool:
    code, _ = run([python, "-c", "import pytest"], tempfile.gettempdir(), dict(os.environ), 60)
    return code == 0


def test_command(files: Sequence[str], python: str = sys.executable, pytest: bool = True) -> List[str]:
    """pytest when it is installed (it also runs unittest classes), else unittest. files: paths under tests/,
    or none for the whole suite."""
    if pytest:
        return [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", *files]
    if files:
        mods = [f[:-3].replace("/", ".") for f in files]
        return [python, "-m", "unittest", *mods]
    return [python, "-m", "unittest", "discover", "-s", "tests", "-t", "."]


def _test_env() -> Dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "TMPDIR", "HOME")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_suite(repo: str, timeout: float = 300, python: str = sys.executable) -> CheckResult:
    """The project's own tests (before a full run: what failed already)."""
    argv = test_command([], python, has_pytest(python))
    code, text = run(argv, repo, _test_env(), timeout)
    return CheckResult(code == 0, tail(text, 25), " ".join(argv[1:]))


def check_hidden(repo: str, fixture: str, hidden: Sequence[str], timeout: float = 300,
                 hidden_dir: str = HIDDEN_DIR, python: str = sys.executable) -> Mapping[str, CheckResult]:
    """Copy the hidden tests into repo/tests, run them, then run the whole suite. {"hidden": .., "suite": ..}"""
    tests = os.path.join(repo, "tests")
    os.makedirs(tests, exist_ok=True)
    init = os.path.join(tests, "__init__.py")
    if not os.path.exists(init):
        open(init, "w").close()
    rels = []
    for name in hidden:
        shutil.copy2(os.path.join(hidden_dir, fixture, name), os.path.join(tests, name))
        if name.startswith("test_"):                    # a helper (_rules.py) is copied, not run
            rels.append(f"tests/{name}")
    py = has_pytest(python)
    env = _test_env()
    out: Dict[str, CheckResult] = {}
    for label, files in (("hidden", rels), ("suite", [])):
        argv = test_command(files, python, py)
        code, text = run(argv, repo, env, timeout)
        out[label] = CheckResult(code == 0, tail(text, 25), " ".join(argv[1:]))
    return out
