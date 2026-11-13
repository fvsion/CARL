"""A secret in a private file (implements SecretStore, contract C-065): the store of a computer that has no system
store CARL can reach (Linux without secret-tool; the file also takes over when the system store refuses). The file is
in the settings folder, mode 0600, in a folder of mode 0700; it is never config.json. Windows has no file modes, so
there it is not used."""
from __future__ import annotations

import os
import stat

from ...domain.shared.errors import SecretStoreError
from .key_store import private_dir


class PrivateFile:
    """One secret in the file `path`; label: its place in words ("a private file in ~/.config/carl")."""

    def __init__(self, path: str, label: str = "") -> None:
        self.path = path
        self.label = label or "a private file in " + os.path.dirname(path)

    def read(self) -> str:
        try:
            with open(self.path, encoding="utf-8") as f:
                return f.read().strip()
        except (OSError, UnicodeDecodeError):
            return ""

    def write(self, secret: str) -> None:
        tmp = f"{self.path}.{os.getpid()}.tmp"
        try:
            private_dir(os.path.dirname(self.path) or ".")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(secret + "\n")
            os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
            os.replace(tmp, self.path)
        except OSError as e:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise SecretStoreError(f"CARL could not write the file {self.path}: {e.strerror or 'error'}.") from None

    def remove(self) -> None:
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass
        except OSError as e:
            raise SecretStoreError(f"CARL could not remove the file {self.path}: {e.strerror or 'error'}.") from None

    def where(self) -> str:
        return self.label
