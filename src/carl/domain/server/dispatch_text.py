"""The dispatch's own texts: what the command line says before any command runs (host/serve.sh's, byte for byte, with
the command word and the computer's name from PlatformWords). The version line, the removed and the unknown
commands, the first run's lines (the no-argument start with no model yet) and the head of the llama-server flags.
Pure. Contract C-401, C-120.
"""
from __future__ import annotations

import re
from typing import Dict, Tuple

from ..shared.platform_words import PlatformWords

_NUMBER = re.compile(r"\s*([0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
LOOKING = "CARL looks for the downloaded models."
DOWNLOAD_PROMPT = "Download it now? [Y/n] "
# `carl engine help`: these lines, then llama-server's own help.
ENGINE_HELP_HEAD: Tuple[str, ...] = (
    "# The flags of llama-server (llama.cpp). CARL sets some of them. CARL gives every",
    "# other flag on its command line to llama-server. CARL does not use some features,",
    "# for example -hf (a download from Hugging Face), multimodal input and LoRA.",
    "")


# The command words that named an engine, removed outright by decision A39 (WCLI): each word and the command that
# replaces it. No alias. llama-install gives its error only where it ran (off the Mac): on a Mac it stays an unknown
# command.
HELP_ADV = "--help-adv"
REMOVED_ENGINE_WORDS: Dict[str, str] = {
    "llama": "start", "llama-install": "engine install", "llama-version": "engine version", HELP_ADV: "engine help"}
OFF_MAC_ONLY = ("llama-install",)


def removed_command(word: str, words: PlatformWords) -> str:
    """The MTPLX presets (grant, pocket) were removed in CARL 1.2.0."""
    return (f"error: '{word}' (an MTPLX preset) was removed in CARL 1.2.0. CARL runs llama.cpp only. "
            f"Use {words.command} start.")


def removed_engine_command(word: str, words: PlatformWords) -> str:
    """The error line for a command word that named an engine: it names the command in its place."""
    return f"error: '{word}' was removed. Use {words.command} {REMOVED_ENGINE_WORDS[word]}."


def is_removed_engine_word(word: str, os_name: str) -> bool:
    """Whether the word is one of the removed engine words on this OS (os_name: macos, windows or linux)."""
    return word in REMOVED_ENGINE_WORDS and not (word in OFF_MAC_ONLY and os_name == "macos")


def version_line(version: str) -> str:
    """`carl --version` (WFIN): CARL's version, the newest CHANGELOG entry (VersionSource, C-029)."""
    return f"CARL {version}"


def unknown_command(word: str, words: PlatformWords) -> str:
    return f"error: unknown command '{word}'. Run {words.command} -h to see the commands."


# The commands that do not run on Windows yet: the dispatch answers them with not_on_windows. None today: install
# runs the client setup there too (W1.6).
NOT_ON_WINDOWS: Tuple[str, ...] = ()


def not_on_windows(word: str, words: PlatformWords) -> str:
    return f"error: '{word}' is not available on Windows yet. {words.command} -h shows the commands."


def best_model(words: PlatformWords) -> str:
    return (f"No model is downloaded. CARL looks for the best model for {words.machine}. This can take 1 min.")


def no_fit(words: PlatformWords) -> str:
    return (f"No model fits {words.machine}. The dashboard opens without a server. "
            f"{words.command} fit shows what fits.")


def chosen(name: str, size: str, words: PlatformWords) -> str:
    """Auto fit's pick; size: the download's size as gb_text gives it."""
    return f"Auto fit chooses {name} for {words.machine} ({size} to download). {words.command} fit tells why."


def offline(words: PlatformWords) -> str:
    return (f"The dashboard opens without a server. To download the model later: "
            f"{words.command} download default")


def starting(words: PlatformWords) -> str:
    return f"CARL starts the server with your saved settings ({words.command} config show). Help: {words.command} -h"


def gb_text(size: str) -> str:
    """A size in bytes (the text a model's card gives, "" when it has none) as 12.3 GB (awk's printf "%.1f GB",
    $1/1e9: a text that does not start with a number counts as 0)."""
    m = _NUMBER.match(size)
    value = float(m.group(1)) if m else 0.0
    return f"{value / 1e9:.1f} GB"
