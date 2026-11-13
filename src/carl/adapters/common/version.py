"""CARL's version (implements VersionSource, contract C-029): the newest "## X.Y.Z" heading of the clone's
CHANGELOG.md, else `git describe --tags` without a leading v, else "unknown" (the rules: domain/clients/package.py,
which the client package reads too). The start writes it into the client package's remote.json.
"""
from __future__ import annotations

import os
import subprocess

from ...domain.clients.package import version_from_changelog, version_from_describe

UNKNOWN = "unknown"


class ChangelogVersion:
    """The version of one clone (root: the folder of CHANGELOG.md)."""

    def __init__(self, root: str) -> None:
        self.root = root

    def version(self) -> str:
        try:
            with open(os.path.join(self.root, "CHANGELOG.md"), encoding="utf-8", errors="replace") as f:
                v = version_from_changelog(f.read())
            if v:
                return v
        except OSError:
            pass
        try:
            p = subprocess.run(["git", "-C", self.root, "describe", "--tags"], stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                               errors="replace", timeout=30)
        except (OSError, subprocess.SubprocessError):
            return UNKNOWN
        return (version_from_describe(p.stdout) if p.returncode == 0 else None) or UNKNOWN
