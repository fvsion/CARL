#!/usr/bin/env python3
"""CARL: start the local model server and its dashboard. The one entry on every OS:

  python3 carl.py [COMMAND] ...     macOS and Linux, from the clone
  py carl.py [COMMAND] ...          Windows, from the clone
  carl [COMMAND] ...                after `uv tool install --editable <the CARL folder>` (pyproject.toml)

`python3 carl.py -h` shows every command. The bootstrap is src/carl/_boot.py, shared with the installed `carl`.
When this file is imported instead of run (`import carl` with the clone as the current folder), it replaces
itself with the package src/carl and runs nothing. It is written for old Pythons too, so that they can print
the bootstrap's version sentence.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _paths():
    """src/ and client/ first on sys.path; a module `carl` that is not the package leaves sys.modules."""
    for folder in (os.path.join(HERE, "client"), os.path.join(HERE, "src")):
        while folder in sys.path:
            sys.path.remove(folder)
        sys.path.insert(0, folder)
    m = sys.modules.get("carl")
    if m is not None and not hasattr(m, "__path__"):
        del sys.modules["carl"]


if __name__ == "__main__":
    _paths()
    from carl import _boot
    sys.exit(_boot.main(sys.argv[1:]))
else:
    # Imported: the import of this file ends with the package in its place (the import system returns what
    # sys.modules holds for the name when the module's code ends).
    sys.modules.pop(__name__, None)
    _paths()
    import importlib
    sys.modules[__name__] = importlib.import_module("carl")
