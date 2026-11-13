"""The Homebrew tools CARL needs before a start (implements Dependencies, contract C-024): llama-server (llama.cpp),
aria2c, ansifilter and zstd (the disk cache's conversations stored as patches). host/common.sh ensure_deps, text for
text: a missing one is installed with Homebrew after a yes on a terminal, or listed with the command to run.
Homebrew itself is not installed (it needs the user's password). Only llama.cpp is required; the others are noted.

Homebrew's bin folder goes first on PATH when brew is there and PATH lacks it (what `brew shellenv` adds), in the
start's own environment: llama-server, and the server's environment, then find it.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from typing import Callable, List, MutableMapping, Optional, Tuple

BREW = "/opt/homebrew/bin/brew"
BREW_PATH = ("/opt/homebrew/bin", "/opt/homebrew/sbin")
# (the program a start runs, its Homebrew formula); the first is required
TOOLS: Tuple[Tuple[str, str], ...] = (("llama-server", "llama.cpp"), ("aria2c", "aria2"), ("ansifilter", "ansifilter"),
                                      ("zstd", "zstd"))
REQUIRED = TOOLS[0][1]
INSTALL_HOMEBREW = '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
_NO = re.compile(r"[Nn]")


class HomebrewDeps:
    """env: the start's environment (its PATH is changed in place); out, err: stdout and stderr lines; ask: a prompt
    on stderr and the line typed (None at the end of the input); brew: where Homebrew's brew is when installed."""

    def __init__(self, env: MutableMapping[str, str], out: Callable[[str], None], err: Callable[[str], None],
                 ask: Callable[[str], Optional[str]], brew: str = BREW) -> None:
        self.env, self.out, self.err, self.ask, self.brew = env, out, err, ask, brew

    def _path(self) -> None:
        path = self.env.get("PATH", "")
        if os.access(self.brew, os.X_OK) and f":{BREW_PATH[0]}:" not in f":{path}:":
            self.env["PATH"] = ":".join(BREW_PATH + (path,))

    def _which(self, name: str) -> bool:
        return shutil.which(name, path=self.env.get("PATH", "")) is not None

    def missing(self) -> List[str]:
        self._path()
        return [formula for program, formula in TOOLS if not self._which(program)]

    def offer(self, interactive: bool) -> bool:
        missing = self.missing()
        if not missing:
            return True
        names = " ".join(missing)
        self.out(f"CARL needs these programs, and they are not installed: {names}.")
        if not self._which("brew"):
            self.err("Install Homebrew first (https://brew.sh), then run this command again:")
            self.err(INSTALL_HOMEBREW)
            return False
        if interactive:
            answer = self.ask(f"Install them now with Homebrew (brew install {names})? [Y/n] ")
            if answer is None:
                return False                    # the end of the input: the launcher's read failed
            if not _NO.match(answer):
                return self._install(missing, names)
        if REQUIRED in missing:
            self.err(f"error: CARL cannot find llama-server. Run: brew install {names}")
            return False
        self.err("note: some optional programs are not installed. Downloads and logs work without them. To install "
                 f"them: brew install {names}")
        return True

    def _install(self, missing: List[str], names: str) -> bool:
        sys.stdout.flush()
        sys.stderr.flush()
        brew = shutil.which("brew", path=self.env.get("PATH", "")) or "brew"
        try:
            code = subprocess.run([brew, "install", *missing], env=dict(self.env)).returncode
        except OSError:
            code = 127
        if code != 0:
            self.err(f"error: brew install failed (its output is above). Correct the cause, or run: brew install "
                     f"{names}")
            return False
        if not self._which(TOOLS[0][0]):
            self.err("error: CARL cannot find llama-server after brew install. Open a new terminal, then run this "
                     "command again.")
            return False
        return True
