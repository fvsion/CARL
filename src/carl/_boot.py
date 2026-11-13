"""The bootstrap of CARL's two entries: the root carl.py (`python3 carl.py`, `py carl.py`) and the installed
command `carl` (pyproject.toml). Both run main() below, in this order:

  1. Below Python 3.9: one sentence and exit 1. On Windows it advises `py` (the Store's python is an alias).
  2. stdout and stderr write UTF-8 (errors are replaced), so a direct `py carl.py` works without PYTHONUTF8.
     (VT mode on a Windows console needs ctypes.windll, which R4 keeps in the Windows adapters: the composition
     turns it on once they exist.)
  3. No bytecode files: the repo folder is shared into VMs.
  4. A module `carl` that is not this package leaves sys.modules. (carl.py puts src/ and client/ first on
     sys.path; the installed command needs nothing, the install puts the package on its path.)
  5. The command word: the serve entry's platform words give it (composition/entries.py build_serve): carl on
     every OS, or CARL_CMD when it is set. It goes into CARL_CMD, so the launcher and every child names it too.
  6. The clone: the folder two levels above this package. The installed command is editable (uv tool install
     --editable), so it finds the clone from its own path; when it cannot, one sentence and exit 1.
  7. The command line: cli/main.py, the dispatch (W3.4). The start (llama or a leading flag), the hidden
     log-filter and the commands (cli/commands, W4.1) run in this process, so the launcher's PID stays the server's
     (MONITOR=0); the client setup and the dashboard run their program in place of it. On Windows the start runs
     in this process too (W6.4); the dispatch answers what does not run there yet (W4.2).

The command word (step 5) and the dispatch (step 7) are the composition's (composition/entries.py). The other steps
read sys.platform themselves: steps 1 to 4 run before the package is known to import, and step 6 only picks a
file. This module is written for old Pythons too (no f-strings, no annotations), so that they can print step 1's
sentence.
"""
import os
import sys

MIN_PYTHON = (3, 9)
PACKAGE = os.path.dirname(os.path.abspath(__file__))
CLONE = os.path.dirname(os.path.dirname(PACKAGE))        # <clone>/src/carl -> <clone>
# What the clone must hold: the entry itself (the same file on every OS since W4.2).
LAUNCHER = "carl.py"


def python_error(version, platform, executable):
    """The sentence for a Python below MIN_PYTHON, or None. VERSION is sys.version_info."""
    if tuple(version[:2]) >= MIN_PYTHON:
        return None
    have = "%d.%d" % (version[0], version[1])
    if platform == "win32":
        if "\\windowsapps\\" in executable.lower():
            return ("error: CARL needs Python 3.9 or newer, and this python (%s) is the Store's alias: install "
                    "Python from python.org and run py carl.py." % have)
        return "error: CARL needs Python 3.9 or newer, and this is Python %s: run py carl.py." % have
    return "error: CARL needs Python 3.9 or newer, and this is Python %s." % have


def utf8_output():
    """stdout and stderr write UTF-8 and replace what they cannot write."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass                                        # not a text stream (a test's StringIO, a closed stream)


def drop_other_carl(modules=None):
    """Remove a module `carl` that is not a package (the root carl.py) from sys.modules."""
    modules = sys.modules if modules is None else modules
    m = modules.get("carl")
    if m is not None and not hasattr(m, "__path__"):
        del modules["carl"]


def launcher(platform):
    """The file the clone must hold, relative to it (PLATFORM is kept for the callers; the file is the same)."""
    return LAUNCHER


def clone_error(clone, platform):
    """The sentence when CLONE is not a CARL clone (an install that is not editable, a moved clone), or None."""
    if os.path.isfile(os.path.join(clone, launcher(platform))):
        return None
    return ("error: carl cannot find its CARL folder (%s has no %s): install it again with "
            "uv tool install --editable <the CARL folder>." % (clone, launcher(platform).replace(os.sep, "/")))


def command_word(platform, environ):
    """Step 5: the command word of the texts (CARL_CMD for the dispatch and every child): the platform words'."""
    from carl.composition.entries import build_serve
    return build_serve(environ, platform).words.command


def main(argv=None):
    """Steps 1 to 7 above. ARGV is the command's arguments (default: sys.argv[1:]). Returns the exit code; on
    macOS and Linux a start does not return (exec)."""
    argv = sys.argv[1:] if argv is None else list(argv)
    text = python_error(sys.version_info, sys.platform, sys.executable or "")
    if text:
        sys.stderr.write(text + "\n")
        return 1
    utf8_output()
    sys.dont_write_bytecode = True
    drop_other_carl()
    text = clone_error(CLONE, sys.platform)
    if text:
        sys.stderr.write(text + "\n")
        return 1
    os.environ["CARL_CMD"] = command_word(sys.platform, os.environ)
    from carl.cli import main as command_line
    return command_line.main(argv)
