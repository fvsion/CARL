"""The sync service on macOS (the ServiceManager port): a launchd agent, dev.carl.sync, that runs carl-sync.py watch
at login and keeps it running. The plist text is the bash setup's, byte for byte. launchctl is called by its name (the
test suites put a recording fake first on PATH; plan 7.3)."""
from __future__ import annotations

import os
import posixpath
import subprocess
from typing import Callable, List, Optional

LABEL = "dev.carl.sync"


def xml(text: str) -> str:
    """Text in the plist (as the bash setup's xml() did)."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Launchd:
    """home: the user's home folder (the plist goes to ~/Library/LaunchAgents); label: the agent's name (tests use a
    test label); uid: the user's id (default: this process's); run: subprocess.run (tests pass a recorder)."""

    def __init__(self, home: str, label: str = LABEL, uid: Optional[int] = None,
                 run: Callable[..., "subprocess.CompletedProcess[bytes]"] = subprocess.run) -> None:
        self.home = home
        self.label = label
        self.uid = uid
        self.run = run

    @property
    def title(self) -> str:
        return f"launchd {self.label}"

    @property
    def plist(self) -> str:
        return os.path.join(self.home, "Library", "LaunchAgents", f"{self.label}.plist")

    def domain(self) -> str:
        uid = self.uid if self.uid is not None else os.getuid()     # type: ignore[attr-defined]  (POSIX)
        return f"gui/{uid}"

    def unit_text(self, python: str, script: str) -> str:
        err = posixpath.join(self.home, ".config", "carl", "client-sync.err")      # (the bash setup's $HOME/.config/...)
        return ('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0"><dict>\n'
                f'  <key>Label</key><string>{self.label}</string>\n'
                f'  <key>ProgramArguments</key><array><string>{xml(python)}</string><string>{xml(script)}</string>'
                '<string>watch</string></array>\n'
                '  <key>RunAtLoad</key><true/>\n'
                '  <key>KeepAlive</key><true/>\n'
                f'  <key>StandardErrorPath</key><string>{xml(err)}</string>\n'
                '</dict></plist>\n')

    def launchctl(self, args: List[str], quiet: bool) -> int:
        """launchctl ARGS; quiet: its errors go nowhere (as the bash setup's 2>/dev/null)."""
        return self.run(["launchctl", *args], stdin=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL if quiet else None).returncode

    def install(self, python: str, script: str) -> bool:
        """Write the plist and load it again (a running agent restarts with the new one). False when launchd
        refused it."""
        os.makedirs(os.path.dirname(self.plist), exist_ok=True)
        with open(self.plist, "w", encoding="utf-8", newline="\n") as f:
            f.write(self.unit_text(python, script))
        self.launchctl(["bootout", self.domain(), self.plist], quiet=True)
        return self.launchctl(["bootstrap", self.domain(), self.plist], quiet=False) == 0

    def remove(self) -> bool:
        if not os.path.isfile(self.plist):
            return False
        self.launchctl(["bootout", self.domain(), self.plist], quiet=True)
        os.remove(self.plist)
        return True

    def available(self) -> bool:
        """launchd runs on every Mac (install says whether it took the agent)."""
        return True

    def installed(self) -> bool:
        return os.path.isfile(self.plist)
