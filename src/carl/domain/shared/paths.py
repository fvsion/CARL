"""PathPolicy: paths as text, the same on every OS (plan 3.1). The domain never asks the OS (rule R1), so it reads
a path's flavour from the path itself: a drive (C:) or a backslash is a Windows path, anything else a POSIX one.
The adapters and composition still use os.path for the files they open.

- expand_home: ~, ~/x and ~\\x under the home folder (models.json and the settings may hold such paths).
- join, dirname, basename: posixpath or ntpath, as the path is written.
- same: the case-aware compare. A Windows path compares without case and with either separator; a POSIX path
  compares exactly (as before: a Mac's folders are compared as written).

This module is the only domain module that imports posixpath and ntpath (rule R1).
"""
from __future__ import annotations

import ntpath
import posixpath
import re
from typing import Any

_DRIVE = re.compile(r"^[A-Za-z]:")


class PathPolicy:
    """One path flavour: POSIX (macOS, Linux) or WINDOWS."""

    def __init__(self, flavour: Any, windows: bool) -> None:
        self._p = flavour
        self.windows = windows
        self.sep = flavour.sep

    def join(self, base: str, *parts: str) -> str:
        return str(self._p.join(base, *parts))

    def dirname(self, path: str) -> str:
        return str(self._p.dirname(path))

    def basename(self, path: str) -> str:
        return str(self._p.basename(path))

    def same(self, a: str, b: str) -> bool:
        """The same path: without case and with either separator on Windows, exactly on POSIX."""
        if self.windows:
            return bool(ntpath.normcase(a) == ntpath.normcase(b))
        return a == b

    def expand_home(self, path: str, home: str) -> str:
        return expand_home(path, home)


POSIX = PathPolicy(posixpath, windows=False)
WINDOWS = PathPolicy(ntpath, windows=True)


def is_windows_path(path: str) -> bool:
    """A drive (C:, C:\\x, C:/x), a UNC path (\\\\server\\share) or a path with backslashes and no slash."""
    return bool(_DRIVE.match(path)) or path.startswith("\\\\") or ("\\" in path and "/" not in path)


def policy_for(path: str) -> PathPolicy:
    """The flavour a path is written in (see is_windows_path)."""
    return WINDOWS if is_windows_path(path) else POSIX


def expand_home(path: str, home: str) -> str:
    """~, ~/x and ~\\x -> HOME and HOME/x (the separator after ~ is kept); any other path as it is."""
    return home + path[1:] if path == "~" or path.startswith(("~/", "~\\")) else path


def basename_any(path: str) -> str:
    """The last part of a path written with / or \\ (a process's command line on any OS)."""
    return re.split(r"[/\\]", path)[-1]
