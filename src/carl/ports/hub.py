"""Ports of Hugging Face: its API and the model downloads."""
from __future__ import annotations

from typing import Protocol

from ..domain.shared.types import JsonValue


class HubClient(Protocol):
    """The Hugging Face API (JSON answers, not yet validated). Contract C-007."""

    def get(self, api_path: str) -> JsonValue: ...


class Downloader(Protocol):
    """A resumable download into a folder. Contract C-008."""

    def fetch(self, url: str, directory: str, file_name: str) -> int:
        """Download (resuming a part file) into directory/file_name; the exit code."""
        ...
