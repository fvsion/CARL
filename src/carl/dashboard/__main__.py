"""The dashboard as a program of its own: `python3 src/carl/dashboard/__main__.py [OPTIONS]`.

The start and the commands `dashboard` and `monitor` run it in place of the command process (the dispatch,
adapters/posix/start_tools.py), as the client setup runs client/carl_setup.py. Run as a script it takes the options,
help text and exit codes of the dashboard (dashboard/options.py). It is not imported.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLONE = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))


def _paths() -> None:
    """src/ and client/ first on sys.path and this folder off it (its modules, such as keys or state, must not shadow
    other names); a module `carl` that is not the package leaves sys.modules."""
    while HERE in sys.path:
        sys.path.remove(HERE)
    for folder in (os.path.join(CLONE, "client"), os.path.join(CLONE, "src")):
        while folder in sys.path:
            sys.path.remove(folder)
        sys.path.insert(0, folder)
    m = sys.modules.get("carl")
    if m is not None and not hasattr(m, "__path__"):
        del sys.modules["carl"]


if __name__ == "__main__":
    sys.dont_write_bytecode = True                # keep the shared folder free of __pycache__
    _paths()
    from carl.dashboard.app import main
    from carl.dashboard.options import help_text
    main(None, help_text())                       # its models and settings: the dashboard entry's API
