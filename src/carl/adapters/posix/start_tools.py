"""What a start and the dispatch do on macOS and Linux that no port covers yet (application/start_server.py
StartTools, application/first_run.py FirstRunTools, cli/main.py DispatchTools): the terminal's lines and prompts, the
folders and the log's link, and CARL's own commands that a start runs as children, as the bash launcher ran its
tools: the memory plan and check (`carl.py fit --plan|--check`), the disk cache's trim (`carl.py cache trim`), the
first run's pick and download (`carl.py fit --pick-default`, `carl.py download NAME`); and the programs that replace
this process: the client setup and the dashboard. Each runs as the bash launcher ran it: python3 from PATH, stdout
captured as $(...) does (the trailing newlines dropped), stderr passed on unless it is quiet. Those that replace this
process keep its PID. The help and the other commands run in the dispatch's own process (W4.1).
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import List, Optional, Sequence, Tuple

from ..common.private_dirs import make_dirs
from .process import default_signals

PYTHON = "python3"                                   # from PATH, as the bash launcher called it
CARL_PY = "carl.py"                                  # the entry, in the clone
LATEST = "llama-server-latest.log"


def out(line: str) -> None:
    print(line, flush=True)


def err(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


def ask(prompt: str) -> Optional[str]:
    """bash's `read -r -p PROMPT`: the prompt on stderr, then one line from stdin without its newline; None at the
    end of the input (read fails there, and the launcher ended)."""
    sys.stdout.flush()
    sys.stderr.write(prompt)
    sys.stderr.flush()
    line = sys.stdin.readline()
    return line[:-1] if line.endswith("\n") else None


def is_tty(fd: int) -> bool:
    return os.isatty(fd)


def make_dir(path: str) -> None:
    make_dirs(path)


def prepare_log(log: str) -> None:
    """The log's folder, and llama-server-latest.log beside it as a link to the log (ln -sfn)."""
    folder = os.path.dirname(log) or "."
    make_dirs(folder)
    latest = os.path.join(folder, LATEST)
    if os.path.lexists(latest) and (os.path.islink(latest) or not os.path.isdir(latest)):
        os.remove(latest)
    os.symlink(log, latest)


class CarlPrograms:
    """CARL's programs in one clone (repo). A tool is a path in the clone (carl.py, client/carl_setup.py). python: the
    program that runs them (python3 from PATH; on Windows the composition gives this Python, as there is no python3
    there)."""

    def __init__(self, repo: str, python: str = PYTHON) -> None:
        self.repo, self.python = repo, python

    def _run(self, tool: str, args: Sequence[str], quiet: bool, capture: bool = True) -> Tuple[int, str]:
        sys.stdout.flush()
        sys.stderr.flush()
        try:
            p = subprocess.run([self.python, os.path.join(self.repo, tool), *args],
                               stdout=subprocess.PIPE if capture else None,
                               stderr=subprocess.DEVNULL if quiet else None, text=True, encoding="utf-8",
                               errors="replace")
        except OSError:
            return 127, ""
        return p.returncode, (p.stdout or "").rstrip("\n")

    def fit(self, args: Sequence[str], quiet: bool) -> Tuple[int, str]:
        """carl.py fit ARGS (the hidden --plan or --check): quiet (the plan) captures stdout and drops stderr; else
        (the check) both go to the terminal."""
        return self._run(CARL_PY, ["fit", *args], quiet, capture=quiet)

    def capture(self, tool: str, args: Sequence[str]) -> Tuple[int, str]:
        """python3 TOOL ARGS with stdout captured as $(...) does; stderr goes to the terminal."""
        return self._run(tool, args, False)

    def run(self, tool: str, args: Sequence[str]) -> int:
        """python3 TOOL ARGS in this terminal; its exit code (127 when python3 cannot run)."""
        return self._run(tool, args, False, capture=False)[0]

    def cache_trim(self) -> None:
        """carl.py cache trim, silent: a trim that fails never stops a start."""
        sys.stdout.flush()
        sys.stderr.flush()
        try:
            subprocess.run([self.python, os.path.join(self.repo, CARL_PY), "cache", "trim"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        except OSError:
            pass

    def exec_tool(self, tool: str, args: Sequence[str]) -> int:
        """python3 TOOL ARGS in place of this process (the client setup)."""
        return self._exec([PYTHON, os.path.join(self.repo, tool), *args])

    def dashboard_argv(self, args: Sequence[str]) -> List[str]:
        """The dashboard's command: python3 and the dashboard's own script in the clone (src/carl/dashboard/__main__.py)."""
        return [PYTHON, os.path.join(self.repo, "src", "carl", "dashboard", "__main__.py"), *args]

    def dashboard(self, args: Sequence[str]) -> int:
        return self._exec(self.dashboard_argv(args))

    @staticmethod
    def _exec(argv: List[str]) -> int:
        sys.stdout.flush()
        sys.stderr.flush()
        default_signals()
        try:
            os.execvp(argv[0], argv)
        except OSError as e:
            err(f"error: CARL cannot run {argv[0]}: {e.strerror or e}.")
        return 127
