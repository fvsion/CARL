"""The Python that runs the client's Python children and the sync service, and the one the setup records for them.

On macOS and Linux it is python3 on PATH, as the bash setup found it (`command -v python3`), so the launchd plist and
the systemd unit name the same file as before; without one, this Python. On Windows it is this Python (the py
launcher or a real python.exe found it: setup.cmd). The setup records it in client-sync.json ("python"), and the
plugins run carl-sync.py with it (client/shared/carl-proc.js syncPython).
"""
from __future__ import annotations

import shutil
import sys
from typing import Mapping, Optional


class Interpreter:
    """environ: the PATH to look in; executable and platform: this Python's (sys.executable, sys.platform)."""

    def __init__(self, environ: Mapping[str, str], executable: Optional[str] = None,
                 platform: Optional[str] = None) -> None:
        self.path = environ.get("PATH", "")
        self.executable = executable if executable is not None else (sys.executable or "")
        self.platform = platform if platform is not None else sys.platform

    def command(self) -> str:
        """The interpreter's path for a child or the service."""
        if self.platform == "win32":
            return self.executable
        return shutil.which("python3", path=self.path) or self.executable
