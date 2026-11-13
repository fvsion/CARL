"""OpenCode's tool switches in the user's shell (the UserEnvironment port on macOS and Linux): the env file
~/.config/carl/opencode.env and a marked 3-line block that sources it, appended to ~/.zshrc and ~/.bashrc when they
exist (never created, nothing else in them changes, a backup first), and taken out again when nothing is switched on.
OpenCode reads these switches only from the environment. With neither file, the report says which line to add.
Every file goes through the Files port, and each change through the installer's ConfigFiles (its backups).
"""
from __future__ import annotations

import os
from typing import Any, Optional

from ..domain.merge_opencode import ENV_FILE, PROFILE_LINE, PROFILES, profile_text


class ProfileEnvironment:
    """The env file and the shell profiles' block under `home`. cf: the installer's ConfigFiles (save_text with its
    backups, short for the report); fs: the Files port."""

    def __init__(self, cf: Any, fs: Any, home: str) -> None:
        self.cf = cf
        self.fs = fs
        self.home = home

    @property
    def env_path(self) -> str:
        return os.path.join(self.home, ".config/carl", ENV_FILE)

    def apply(self, text: Optional[str], summary: str, profile: bool, report: Any) -> None:
        env = self.env_path
        if text is not None:
            if self.fs.read(env) != text:
                report.add("updated" if self.fs.exists(env) else "added",
                           f"OpenCode tools ({self.cf.short(env)}): {summary}")
            self.cf.save_text(env, text, mode=0o644, backup=False)
        elif self.fs.exists(env):
            self.fs.remove(env)
            report.add("removed", f"OpenCode tools ({self.cf.short(env)}): all off")
        self.profiles(text is not None, profile, report)

    def profiles(self, want: bool, profile: bool, report: Any) -> None:
        """The pointer to the env file in ~/.zshrc and ~/.bashrc (profile False: NO_PROFILE=1, only the line to add)."""
        if not profile:
            if want:
                report.add("kept", f"your shell profile (NO_PROFILE=1). For the tool switches of OpenCode, "
                                   f"add this line to it:\n{PROFILE_LINE}")
            return
        found = False
        for name in PROFILES:
            path = os.path.join(self.home, name)
            cur = self.fs.read(path)
            if cur is None:
                continue
            found = True
            new = profile_text(cur, want)
            if new != cur:
                self.cf.save_text(path, new, mode=0o644)
                where = (f" (in {self.cf.short(self.fs.realpath(path))}, its link target)" if self.fs.islink(path)
                         else "")
                report.add("updated" if want else "removed",
                           f"~/{name}{where}: {'sources' if want else 'no longer sources'} "
                           f"~/.config/carl/{ENV_FILE} (open a new terminal)")
        if want and not found:
            report.add("kept", f"no ~/.zshrc or ~/.bashrc. For the tool switches of OpenCode, add this line "
                               f"to your shell profile:\n{PROFILE_LINE}")
