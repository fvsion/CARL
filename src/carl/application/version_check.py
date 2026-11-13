"""The installed llama.cpp against the version CARL is tested with (the rules: domain/llama/version.py): the start
line that asks for an update when it is older.
"""
from __future__ import annotations

from typing import Optional

from ..domain.llama.version import TESTED, LlamaVersion, VersionCheck, check_version, start_line
from ..ports import LlamaCpp


def start_note(version_text: str, update: Optional[str] = None, tested: LlamaVersion = TESTED) -> str:
    """The launcher's line for `llama-server --version` output ("" when it is not older, or unknown). update: the
    command that updates llama.cpp on this computer (the domain's Mac command when None); tested: the version of
    this computer (domain/llama/version.py tested_for)."""
    c = check_version(version_text, tested)
    return start_line(c) if update is None else start_line(c, update)


class VersionCheckService:
    """The check over the LlamaCpp port (its version text and its update command). Contract C-111."""

    def __init__(self, llama: LlamaCpp, tested: LlamaVersion = TESTED) -> None:
        self.llama, self.tested = llama, tested

    def check(self) -> VersionCheck:
        return check_version(self.llama.version_text(), self.tested)

    def start_note(self) -> str:
        return start_line(self.check(), self.llama.update_hint())
