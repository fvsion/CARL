"""Where llama.cpp's programs are on a Mac (implements LlamaCpp, contract C-022): Homebrew's llama.cpp on PATH, or
the folder CARL_LLAMA_DIR names. It is installed and updated with Homebrew (HomebrewDeps asks for it before a start).
"""
from __future__ import annotations

import shutil
from typing import Callable, Optional

from .locator import BATCHED_BENCH, FIT_PARAMS, SERVER, find
from .version_probe import version_text


class HomebrewLlama:
    """folder: CARL_LLAMA_DIR (None: PATH); update: the command that updates llama.cpp (brew upgrade llama.cpp)."""

    def __init__(self, folder: Optional[str], update: str,
                 which: Callable[[str], Optional[str]] = shutil.which) -> None:
        self.folder, self.update, self.which = folder, update, which

    def server(self) -> Optional[str]:
        return find(SERVER, self.folder, "", self.which)

    def batched_bench(self) -> Optional[str]:
        return find(BATCHED_BENCH, self.folder, "", self.which)

    def fit_params(self) -> Optional[str]:
        return find(FIT_PARAMS, self.folder, "", self.which)

    def version_text(self) -> str:
        return version_text(self.server())

    def update_hint(self) -> str:
        return self.update
