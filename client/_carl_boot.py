"""The bootstrap of the client's Python entries: carl_setup.py (the setup) and carl-sync.py (the sync). Each entry
calls start() first, in this order:

  1. Below Python 3.9: one sentence and exit 1. On Windows the sentence names python.org, and the Store's python
     (an install under WindowsApps, or its alias) is named as such: setup.cmd looks for `py -3` or a real
     python.exe first.
  2. stdout and stderr write UTF-8 and replace what they cannot write, so a Windows console without PYTHONUTF8 shows
     every text. stdout is line buffered: a program the setup starts writes after the lines before it.
  3. No bytecode files: the client folder is a package that is unzipped over again (no __pycache__ in it).
  4. The client folder first on sys.path, so `import carl_client` is the package next to the entry.

This module is written for old Pythons too (no f-strings, no annotations), so that they can print step 1's sentence.
The entries that import it are written the same way up to the call of start().
"""
import os
import sys

MIN_PYTHON = (3, 9)
HERE = os.path.dirname(os.path.abspath(__file__))
STORE = "\\windowsapps\\"                  # the Microsoft Store's python (and its alias) live under this folder


def is_store_python(executable):
    """True for the Microsoft Store's python, or its alias that opens the Store (both under WindowsApps)."""
    return STORE in (executable or "").lower()


def python_error(version, platform, executable):
    """The sentence for a Python below MIN_PYTHON, or None. VERSION is sys.version_info."""
    if tuple(version[:2]) >= MIN_PYTHON:
        return None
    have = "%d.%d" % (version[0], version[1])
    if platform == "win32":
        if is_store_python(executable):
            return ("error: CARL needs Python 3.9 or newer, and this python (%s) is the Store's: install Python from "
                    "python.org, then run setup.cmd again." % have)
        return ("error: CARL needs Python 3.9 or newer, and this is Python %s: install Python from python.org, then "
                "run setup.cmd again." % have)
    return ("error: CARL needs Python 3.9 or newer, and this is Python %s. On macOS, run xcode-select --install. On "
            "Linux, install the package python3." % have)


def utf8_output():
    """stdout and stderr write UTF-8 and replace what they cannot write; stdout is line buffered."""
    for stream, lines in ((sys.stdout, True), (sys.stderr, False)):
        try:
            if lines:
                stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass                                        # not a text stream (a test's StringIO, a closed stream)


def client_path(folder=HERE, path=None):
    """The client folder first on sys.path (once)."""
    path = sys.path if path is None else path
    while folder in path:
        path.remove(folder)
    path.insert(0, folder)


def start():
    """Steps 1 to 4 above. Returns 0, or the exit code after step 1's sentence."""
    text = python_error(sys.version_info, sys.platform, sys.executable or "")
    if text:
        sys.stderr.write(text + "\n")
        return 1
    utf8_output()
    sys.dont_write_bytecode = True
    client_path()
    return 0
