"""The first-run offer of llama.cpp on Windows and Linux (implements Dependencies, contract C-024): when no
llama-server is found, offer to install the pinned build (`carl engine install`). The Mac's twin is HomebrewDeps.

  on a terminal   say what is missing, ask with the build and its download size, install after a yes;
  off a terminal  say what is missing and print the command to run, and the offer is not taken.

The installed build's folder goes first on PATH in the start's own environment, as the Homebrew check does for
Homebrew's: the start that follows (and its children) finds llama-server in it. The settings folder's llama.path
is recorded by the install itself. Only llama.cpp is required.

A named folder (CARL_LLAMA_DIR, or the setting llama.path) that holds no llama-server is no case for the offer: the
install could not help (CARL_LLAMA_DIR wins) or would overwrite the user's setting. It is one error naming the folder.
"""
from __future__ import annotations

import os
import re
from typing import Callable, List, MutableMapping, Optional

from ...domain.shared.errors import ConfigError

NAME = "llama.cpp"
_NO = re.compile(r"[Nn]")


class ReleaseOffer:
    """found: whether a llama-server is found; describe: the build the install would take, in words ("llama.cpp
    b11738, cuda-13, 552 MB"; ConfigError when the release has none for this computer); install: installs it and
    gives its folder (ConfigError when it fails); command: what to run by hand (carl engine install); env: the start's
    environment (its PATH changes in place); out, err: lines on stdout and stderr; ask: a prompt on stderr and the
    line typed (None at the end of the input); named: what names a folder that holds no llama-server ("CARL_LLAMA_DIR
    (/x)"), or "" when CARL_LLAMA_DIR and llama.path are both unset (then the offer is made)."""

    def __init__(self, found: Callable[[], bool], describe: Callable[[], str], install: Callable[[], str],
                 command: str, env: MutableMapping[str, str], out: Callable[[str], None],
                 err: Callable[[str], None], ask: Callable[[str], Optional[str]],
                 named: Callable[[], str] = lambda: "") -> None:
        self.found, self.describe, self.install, self.command = found, describe, install, command
        self.env, self.out, self.err, self.ask, self.named = env, out, err, ask, named

    def missing(self) -> List[str]:
        return [] if self.found() else [NAME]

    def offer(self, interactive: bool) -> bool:
        if self.found():
            return True
        where = self.named()
        if where:
            self.err(f"error: CARL cannot find llama-server in {where}. Fix it, or unset it to have CARL offer to "
                     f"install llama.cpp.")
            return False
        self.out("CARL needs llama.cpp, and it is not installed.")
        if interactive:
            try:
                plan = self.describe()
            except ConfigError as e:
                self.err(f"error: {e}")
                return False
            answer = self.ask(f"Download and install {plan} now? [Y/n] ")
            if answer is None:
                return False                    # the end of the input: the launcher's read failed
            if not _NO.match(answer):
                return self._install()
        self.err(f"error: CARL cannot find llama-server. Run: {self.command}")
        return False

    def _install(self) -> bool:
        try:
            folder = self.install()
        except ConfigError as e:
            self.err(f"error: {e}")
            return False
        self.env["PATH"] = folder + os.pathsep + self.env.get("PATH", "")
        if not self.found():
            self.err("error: CARL installed llama.cpp, but still cannot find llama-server.")
            return False
        return True
