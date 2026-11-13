"""`carl package [--anyway] [--out DIR]`: the client package, a zip of client/ with the server's address and key for
the setup on another computer (application/package.py). Exit 1 when CARL makes no package. Contract C-411."""
from __future__ import annotations

import sys
from typing import List, Optional

from ...domain.shared.errors import ConfigError
from ...domain.shared.units import file_size
from ...ui.layout import wrap
from .base import Cli, arg

USAGE = "package [--anyway] [--out DIR]"


def run(cli: Cli, argv: List[str]) -> int:
    out_dir: Optional[str] = None
    anyway = False
    i = 0
    while i < len(argv):
        if argv[i] == "--anyway":
            anyway = True
        elif argv[i] == "--out":
            out_dir = arg(argv, i + 1, USAGE)
            i += 1
        else:
            raise ConfigError(f"package does not know '{argv[i]}'. Usage: {cli.words.command} {USAGE}")
        i += 1
    res = cli.tools.make_package(out_dir, anyway)
    w = cli.width()

    def para(note: str) -> str:
        text, *commands = note.split("\n")                 # the commands: as they are, to copy
        return "\n".join(wrap(text, w) + [f"  {c}" for c in commands])
    if res.error:
        print("\n".join([para(res.error), *map(para, res.notes)]), file=sys.stderr)
        return 1
    print(f"CARL made the client package ({res.files} files, {file_size(res.size)}):")
    print(f"  {res.path}")
    for n in res.notes:
        print()
        print(para(n))
    return 0
