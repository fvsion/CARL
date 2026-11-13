"""The server's API key file (its paths: domain/config/key_paths.py): read it, and make it at a start."""
from __future__ import annotations

import os
import secrets
import stat
import string
from typing import List, Mapping, Optional

from ...domain.config.key_paths import server_key_file
from ...domain.shared.contracts import ensure
from .private_dirs import make_dirs


def has_key(path: str) -> bool:
    """path is a file with something in it."""
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


def key_file(env: Mapping[str, str], home: Optional[str] = None) -> str:
    """The key file the server uses (API_KEY_FILE, the CARL path or the pre-1.2.0 one). env: the process' environment
    (the composition reads it)."""
    return server_key_file(home or os.path.expanduser("~"), env, has_key)


def read_key(path: str) -> str:
    """The key in path (whitespace stripped), "" when it can't be read."""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except (OSError, UnicodeDecodeError):
        return ""


KEY_CHARS = string.ascii_letters + string.digits
KEY_LENGTH = 40                                       # 40 letters and digits (C-203)


def private_dir(folder: str) -> None:
    """The key's folder, made when missing, readable by its owner only (0700)."""
    make_dirs(folder)
    os.chmod(folder, 0o700)


class KeyFile:
    """One key file, as a start uses it (implements KeyStore, contract C-026): the file the start names
    (API_KEY_FILE, else CARL's), made with a new key when it has none. The key copies from earlier places are the
    macOS migrations' (adapters/macos/mtplx_migration.py, llm_deploy_migration.py), wired only on macOS."""

    def __init__(self, file: str) -> None:
        self.file = file

    def path(self) -> str:
        return self.file

    def read(self) -> str:
        return read_key(self.file) if has_key(self.file) else ""

    def ensure(self) -> str:
        """A file with a key keeps it (its group and other bits cleared); else a new random key, mode 0600, in a
        folder of mode 0700."""
        if has_key(self.file):
            try:
                os.chmod(self.file, stat.S_IMODE(os.stat(self.file).st_mode) & ~0o077)
            except OSError:
                pass
            return self.file
        private_dir(os.path.dirname(self.file) or ".")
        key = "".join(secrets.choice(KEY_CHARS) for _ in range(KEY_LENGTH))
        fd = os.open(self.file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(key)                                # no newline at the end, as the launcher's
        os.chmod(self.file, 0o600)
        ensure(has_key(self.file), "C-026", "the key file holds a key")
        return self.file


def tilde(path: str, home: str) -> str:
    """A path under the home folder as ~/... in a message."""
    return "~" + path[len(home):] if path.startswith(home) else path


def copy_private(source: str, target: str) -> None:
    """source's bytes into target, readable by its owner only (the launcher's `umask 077; cp`)."""
    with open(source, "rb") as src:
        data = src.read()
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.chmod(target, 0o600)


class KeyMigration:
    """A key from an earlier place (source) copied once to CARL's key file (target) when the target has none
    (implements ConfDirMigration, contract C-027). The source stays. Its places are the macOS adapters'
    (macos/llm_deploy_migration.py, macos/mtplx_migration.py), wired only on macOS (decision 13). The message
    names the target as it is and the source with ~, as the launcher said it."""

    def __init__(self, home: str, source: str, target: str) -> None:
        self.home, self.source, self.target = home, source, target

    def pending(self) -> bool:
        return not has_key(self.target) and has_key(self.source)

    def migrate(self) -> List[str]:
        if not self.pending():
            return []
        private_dir(os.path.dirname(self.target) or ".")
        copy_private(self.source, self.target)
        return [f"moved the API key to {self.target} (copied from {tilde(self.source, self.home)}; clients keep "
                "working)"]

