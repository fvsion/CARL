"""The llama.cpp version that CARL is tested with, and the check of the installed one.

CARL keeps one tested version (TESTED). The launcher and the dashboard read `llama-server --version`
("version: 0.6.0 (build 11429, commit d81235049)"; builds before 0.x said "version: 6789 (abc1234)", the
build number only) and compare it with TESTED: by the build number when both have one, else by the dotted
version. An older llama.cpp gets a warning (the start lines, the HEALTH card); a newer one a quiet note (the
HEALTH card at full detail). The M3 Pro ran 0.4.1 for weeks unnoticed (Phase 23.4.2). Pure.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional, Tuple

UPDATE = "brew upgrade llama.cpp"
State = Literal["older", "same", "newer", "unknown"]


@dataclass(frozen=True)
class LlamaVersion:
    """A llama.cpp version: the dotted version (() when not known) and the build number (None when not known)."""
    version: Tuple[int, ...] = ()
    build: Optional[int] = None

    def text(self, inner: bool = False) -> str:
        """0.6.0 (build 11429), 0.6.0, or build 6789; inner (inside parentheses): 0.6.0, build 11429."""
        dotted = ".".join(str(x) for x in self.version)
        if dotted and self.build is not None:
            return f"{dotted}, build {self.build}" if inner else f"{dotted} (build {self.build})"
        return dotted or (f"build {self.build}" if self.build is not None else "unknown")


# The tested version: on both test Macs (M2 Max, M3 Pro) since 2026-10-09. Change it here only.
TESTED = LlamaVersion((0, 6, 0), 11429)

_VERSION = re.compile(r"^\s*version:\s*(\S+)(?:\s*\(([^)]*)\))?", re.I)
_DOTTED = re.compile(r"\d+(?:\.\d+)+")
_BUILD_ONLY = re.compile(r"b?(\d+)")
_BUILD_IN = re.compile(r"\bbuild\s+(\d+)", re.I)


def parse_version(text: str) -> Optional[LlamaVersion]:
    """The version in `llama-server --version` output (its first "version:" line), or None."""
    for line in text.splitlines():
        m = _VERSION.match(line)
        if not m:
            continue
        word, inside = m.group(1), m.group(2) or ""
        b = _BUILD_IN.search(inside)
        build = int(b.group(1)) if b else None
        if _DOTTED.fullmatch(word):
            return LlamaVersion(tuple(int(x) for x in word.split(".")), build)
        only = _BUILD_ONLY.fullmatch(word)
        if only:                                    # before 0.x: the build number is the version
            return LlamaVersion((), int(only.group(1)))
        return None
    return None


def compare(a: LlamaVersion, b: LlamaVersion) -> Optional[int]:
    """-1, 0 or 1 (a older, the same, newer than b): by the build numbers when both have one, else by the dotted
    versions; None when neither can be compared."""
    if a.build is not None and b.build is not None:
        return (a.build > b.build) - (a.build < b.build)
    if a.version and b.version:
        n = max(len(a.version), len(b.version))
        x, y = a.version + (0,) * (n - len(a.version)), b.version + (0,) * (n - len(b.version))
        return (x > y) - (x < y)
    return None


@dataclass(frozen=True)
class VersionCheck:
    """The installed llama.cpp against the tested one."""
    state: State
    installed: Optional[LlamaVersion] = None
    tested: LlamaVersion = TESTED


def check_version(output: str, tested: LlamaVersion = TESTED) -> VersionCheck:
    """The check of `llama-server --version` output ("" or "?" when it could not run: unknown)."""
    found = parse_version(output or "")
    c = compare(found, tested) if found else None
    state: State = "unknown" if c is None else "older" if c < 0 else "newer" if c > 0 else "same"
    return VersionCheck(state, found, tested)


def start_line(c: VersionCheck) -> str:
    """The launcher's start line: one sentence when the installed llama.cpp is older than the tested one, else ""."""
    if c.state != "older" or c.installed is None:
        return ""
    return (f"llama.cpp {c.installed.text()} is older than the version that CARL is tested with "
            f"({c.tested.text(inner=True)}): update it with {UPDATE}.")


def health_rows(c: VersionCheck, full: bool) -> Tuple[Tuple[str, str, bool], ...]:
    """The HEALTH card's llama.cpp rows: (label, sentence, warning). Older: a warning and how to update, at every
    detail level; newer or the same: one quiet row at full detail only; unknown: none."""
    if c.installed is None or c.state == "unknown":
        return ()
    have, tested = c.installed.text(), c.tested.text()
    if c.state == "older":
        return (("version", f"llama.cpp {have} is older than the tested version, {tested}.", True),
                ("to update", f"Run {UPDATE}, then restart the server.", False))
    if not full:
        return ()
    if c.state == "newer":
        return (("version", f"llama.cpp {have} is newer than the tested version, {tested}.", False),)
    return (("version", f"llama.cpp {have}, the tested version.", False),)
