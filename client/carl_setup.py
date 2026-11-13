#!/usr/bin/env python3
"""CARL client setup (carl_client.app.setup): the one setup on every computer. The user runs ./setup (macOS and
Linux), setup.command (a double click on a Mac) or setup.cmd (Windows); each runs this file. ./setup --help shows
the options.

  carl_setup.py [OPTION...]              the setup (./setup)
  carl_setup.py install [OPTION...]      the config step (carl_client.app.install)
  carl_setup.py clients [WHICH]          OpenCode and Pi from npm (both, opencode or pi)

The bootstrap is _carl_boot.py, shared with carl-sync.py. This file is written for old Pythons too, so that they
can print the bootstrap's version sentence.
"""
import os
import sys

sys.dont_write_bytecode = True                    # before the first import: no __pycache__ in the client folder
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                          # _carl_boot and carl_client, next to this file

import _carl_boot  # noqa: E402


def main(argv):
    code = _carl_boot.start()
    if code:
        return code
    from carl_client.app import setup
    return setup.main(argv, setup.build(HERE, os.environ))


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(130)
