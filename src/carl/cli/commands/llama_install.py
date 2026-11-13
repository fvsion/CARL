"""`carl engine install [--backend BUILD] [--list]`: download the llama.cpp build that CARL is tested with, check it,
unpack it into the settings folder (llama.cpp/bNNNNN-BACKEND) and record it as llama.path. The previous build stays.
BUILD is auto (the build for this computer's GPU), cuda, cpu, or cuda-13 / cuda-12. A build the release
lacks for this computer falls back to the one it has, with a sentence, or the command says to set llama.path to a build
of one's own. Windows and Linux only. Contract C-422.
"""
from __future__ import annotations

import os
from typing import List

from ...application.llama_install import CHOICES
from ...domain.shared.errors import ConfigError
from ...domain.shared.units import file_size
from .base import Cli

USAGE = f"engine install [--backend {'|'.join(('auto',) + CHOICES)}] [--list]"


def parse(argv: List[str]) -> tuple:
    """(the wanted build, whether to list) of the options."""
    want, listing, args = "", False, list(argv)
    while args:
        a = args.pop(0)
        if a == "--list":
            listing = True
        elif a == "--backend" and args:
            want = args.pop(0)
        elif a.startswith("--backend="):
            want = a.split("=", 1)[1]
        else:
            raise ConfigError(f"usage: carl.py {USAGE}")
    return want, listing


def run(cli: Cli, argv: List[str]) -> int:
    want, listing = parse(argv)
    install = cli.tools.llama_install()
    if listing:
        builds = install.installed()
        configured = cli.api().load_config().get("llama", {})
        path = configured.get("path") if isinstance(configured, dict) else None
        print(f"llama.path: {cli.tilde(str(path)) if path else 'not set'}")
        print("Builds that CARL installed (the newest first):" if builds else "CARL has installed no build.")
        for b in builds:
            print(f"  {cli.tilde(b)}")
        return 0
    plan = install.plan(want)
    print(f"llama.cpp {plan.tag} (version {plan.version}): the {plan.backend} build for this computer "
          f"({plan.why}).")
    if plan.note:
        print(plan.note)
    count = len(plan.build.files)
    print(f"Download: {count} file{'s' if count != 1 else ''}, {file_size(plan.build.size)}.")
    done = install.install(plan)
    others = [b for b in install.installed() if os.path.normcase(b) != os.path.normcase(done.folder)]
    print(f"llama.cpp {plan.tag} ({plan.backend}) is installed in {cli.tilde(done.folder)}.")
    print(f"The setting llama.path is {cli.tilde(done.folder)}.")
    if others:
        print(f"The previous build stays: {cli.tilde(others[0])}.")
    return 0
