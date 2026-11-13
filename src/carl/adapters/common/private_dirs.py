"""CARL's private folders (decision A48): made without a mode, then given the mode CARL asks for, and checked once on
Windows, where a refusal can be mended.

Why no mode in the call. Python 3.13 and later turn the mode 0o700 of os.mkdir and os.makedirs into an access list of
OWNER RIGHTS, SYSTEM and the Administrators group on Windows, with no entry for the user. A process that runs as an
administrator (an elevated terminal or an elevated ssh session) owns what it makes as the Administrators group; the
same user without elevation holds that group deny-only, so the folder answers "Access is denied" to the user who made
it (seen at the desk: models\\logs after an elevated ssh run). Python 3.12 and earlier ignored the mode on Windows, and
the folder inherited its parent's list, which in the user's profile is the user's, SYSTEM's and the Administrators
group's. So CARL makes the folder with the default mode and sets the private mode after, with os.chmod: on macOS and
Linux that is the mode as before; on Windows chmod reaches only the read-only flag, and the folder keeps its parent's
list, on every Python, usable by the user elevated or not. (The API key file keeps its own list:
adapters/windows/secrets.py.)

A folder that the old way made (an elevated run, Python 3.13 or later) is found when CARL next makes it: it is opened
once (a scan that reads nothing). When Windows refuses, CARL gives the user's access back with icacls if this process
may change the list (an elevated run, or the folder's owner), and otherwise raises FolderAccessError with the command
to run once in an administrator terminal, or to delete the folder (CARL makes it again). Without icacls (macOS, Linux)
nothing is checked: a refusal there is the caller's own PermissionError, as before.

An elevated run also gives the user who runs CARL (the user's SID, not only Administrators) full control of each
folder it makes or opens, once for each folder in a process (decision A52). An elevated process opens a folder that only
Administrators may open, so the check above finds nothing wrong there; the unelevated run of the same user would be the
one refused, and cannot change the list (the folder's owner is the Administrators group). So the elevated run, which
can, grants the user's SID on the folder and what is in it. That repairs a folder the old way made, and keeps a folder
this run makes usable. The grant keeps the existing entries. The run is elevated when `whoami /groups` lists the High
Mandatory Level (S-1-16-12288); the user is the SID of the process (an administrator who elevates as another account
is that account's SID: whoami says which).

The Windows calls are processes (whoami, icacls), so this module needs no Win32 binding; each is a parameter, so the
tests give their own.
"""
from __future__ import annotations

import errno
import getpass
import os
import shutil
import subprocess
from typing import Callable, List, Optional, Set

from ...domain.shared.errors import FolderAccessError

TIMEOUT = 15
ICACLS_GRANT = "(OI)(CI)F"                     # full control, inherited by the folder's files and folders
DENIED = ("Windows does not let you open {path}. A run as administrator made that folder, so only administrators may "
          "open it. Open an administrator terminal and run this once: icacls \"{path}\" /grant \"{who}:{grant}\" /T /C. "
          "Or delete the folder if you do not need what is in it: CARL makes it again.")

HIGH_LEVEL = "S-1-16-12288"                    # the mandatory level of an elevated process

Run = Callable[[List[str]], "subprocess.CompletedProcess[str]"]
_elevated: Optional[bool] = None               # this process, asked once
_granted: Set[str] = set()                     # the folders an elevated run gave the user, once each


def _run(argv: List[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=TIMEOUT)


def make_dirs(path: str, mode: int = 0o777) -> None:
    """os.makedirs(path, mode, exist_ok=True), without the mode in the call (see above): a folder made here and meant to
    be private (no group or other bits in mode) gets the mode after. A folder that exists is opened once."""
    made = not os.path.isdir(path)
    os.makedirs(path, exist_ok=True)
    if made and not mode & 0o077:
        os.chmod(path, mode)
    if made:
        give_user(path)
    else:
        ensure_usable(path)


def is_elevated(run: Run = _run) -> bool:
    """This process runs elevated (Windows; asked once). False without icacls (macOS, Linux) and when whoami fails."""
    global _elevated
    if _elevated is None:
        _elevated = False
        if shutil.which("icacls"):
            try:
                _elevated = HIGH_LEVEL in run(["whoami", "/groups", "/fo", "csv", "/nh"]).stdout
            except (OSError, subprocess.SubprocessError):
                pass
    return _elevated


def can_open(path: str) -> bool:
    """The folder may be opened (a scan of it; nothing is read)."""
    try:
        with os.scandir(path):
            return True
    except PermissionError:
        return False
    except OSError:
        return True                              # missing, not a folder: not an access problem


def user_who(run: Run = _run) -> str:
    """Whom the remedy names: the user's SID from whoami (*S-1-5-...: it reads the same in every language of Windows),
    else the user's name."""
    try:
        out = run(["whoami", "/user", "/fo", "csv", "/nh"]).stdout.strip()
        sid = out.rsplit(",", 1)[-1].strip().strip('"')
        if sid.startswith("S-1-"):
            return "*" + sid
    except (OSError, subprocess.SubprocessError):
        pass
    return getpass.getuser()


def grant_argv(path: str, who: str) -> List[str]:
    """The icacls command that gives who full control of the folder and what is in it (the existing entries stay)."""
    return ["icacls", path, "/grant", f"{who}:{ICACLS_GRANT}", "/T", "/C", "/Q"]


def repair(path: str, run: Run = _run) -> bool:
    """Give the user back the folder this process may change the list of (an elevated run, or the owner); True when it
    opens afterwards."""
    try:
        run(grant_argv(path, user_who(run)))
    except (OSError, subprocess.SubprocessError):
        return False
    return can_open(path)


def give_user(path: str, run: Run = _run) -> bool:
    """An elevated run gives the user's SID the folder and what is in it, once for each folder in this process (A52). True
    when it ran the grant. Nothing for a run that is not elevated, or a folder that is not there."""
    key = os.path.normcase(os.path.abspath(path))
    if key in _granted or not os.path.isdir(path) or not is_elevated(run):
        return False
    _granted.add(key)
    try:
        run(grant_argv(path, user_who(run)))
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def ensure_usable(path: str, opens: Callable[[str], bool] = can_open,
                  fix: Callable[[str], bool] = repair, who: Optional[Callable[[], str]] = None,
                  grant: Callable[[str], bool] = give_user) -> None:
    """Nothing when the folder opens (an elevated run first gives the user the folder, see above) or this computer has
    no icacls; else the repair; else FolderAccessError with the command to run."""
    if not shutil.which("icacls"):
        return
    if opens(path):
        grant(path)
        return
    if fix(path):
        return
    raise FolderAccessError(errno.EACCES, DENIED.format(path=path, who=(who or user_who)(), grant=ICACLS_GRANT), path)
