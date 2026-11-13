"""`carl engine install|version|help` (WCLI, decision A39): the engine that serves the models, which is llama.cpp today.

  install [--backend BUILD] [--list]  download, check and install the engine CARL is tested with (llama_install.py).
                                      Off the Mac only: the Mac keeps Homebrew's llama.cpp (the hint says how to update).
  version                             the engine's version, and a line to update it when it is older than the version
                                      CARL is tested with (it replaces the hidden llama-version).
  help                                the engine's own help after CARL's head (it replaces --help-adv).

`install` runs the llama.cpp install; version and help ask the engine adapter (cli.tools.engine). Both run on every OS.
Contracts C-422 (install), C-423 (version, help).
"""
from __future__ import annotations

import sys
from typing import List

from ...application import version_check
from ...domain.llama.version import check_version, tested_for
from ...domain.shared.errors import ConfigError
from . import llama_install
from .base import EXIT_ERROR, Cli

SUBCOMMANDS = ("install", "version", "help")
EXIT_USAGE = 2


def installs(cli: Cli) -> bool:
    """Whether `engine install` runs on this computer (off the Mac)."""
    return cli.os != "macos"


def usage(cli: Cli) -> str:
    """The usage line: install only where it runs."""
    words = SUBCOMMANDS if installs(cli) else SUBCOMMANDS[1:]
    return f"usage: {cli.words.command} engine {'|'.join(words)}"


def run(cli: Cli, argv: List[str]) -> int:
    sub, rest = (argv[0] if argv else ""), argv[1:]
    if sub == "install":
        if not installs(cli):
            print(f"error: engine install does not run on {cli.words.machine}. llama.cpp comes from Homebrew: "
                  f"{cli.words.update_hint()}.", file=sys.stderr)
            return EXIT_USAGE
        return llama_install.run(cli, rest)
    if sub == "version" and not rest:
        return version(cli)
    if sub == "help" and not rest:
        return cli.tools.engine().show_help()
    raise ConfigError(usage(cli))


def version(cli: Cli) -> int:
    """The engine's version; with the update line after it when it is older than the tested version. Exit 1 when the
    version cannot be read."""
    engine = cli.tools.engine()
    text = engine.version_text()
    found = check_version(text, tested_for(cli.os)).installed
    if found is None:
        print(f"{engine.engine_name()}: CARL cannot read its version. Install or update it with "
              f"{engine.update_hint()}.", file=sys.stderr)
        return EXIT_ERROR
    print(f"{engine.engine_name()} {found.text()}")
    line = version_check.start_note(text, engine.update_hint(), tested_for(cli.os))
    if line:
        print(line)
    return 0
