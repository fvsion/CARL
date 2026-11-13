"""One secret in the macOS Keychain (implements SecretStore, contract C-065), through the security command.

The secret never goes on a command line (a process list shows every argument): `security -i` reads its commands from
standard input, so the add command, with the secret in it, is a line on stdin. Reading uses `find-generic-password -w`,
which prints the secret on stdout. The secret is a generic password with a service and an account, in the login
Keychain unless `keychain` names another file (the tests use a Keychain of their own). A write is read back before it
counts, because `security -i` answers 0 when its command failed. Nothing the command prints is passed on.
"""
from __future__ import annotations

import subprocess
from typing import Callable, List, Optional, Tuple

from ...domain.shared.errors import SecretStoreError

SECURITY = "/usr/bin/security"
NOT_FOUND = 44                                  # the exit code of "The specified item could not be found"
TIMEOUT = 30

Run = Callable[[List[str], str], Tuple[int, str]]


def run_security(argv: List[str], stdin: str) -> Tuple[int, str]:
    """A security command: its exit code and its output. stdin: what the command reads ("" for none)."""
    done = subprocess.run(argv, input=stdin.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          timeout=TIMEOUT)
    return done.returncode, done.stdout.decode("utf-8", errors="replace")


def quoted(text: str) -> str:
    """text as one word of an interactive security command line."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Keychain:
    """The secret of `service` and `account`. keychain: a Keychain file ("" the login Keychain); run: the command
    runner (the tests give their own)."""

    def __init__(self, service: str, account: str, keychain: str = "", run: Run = run_security) -> None:
        self.service, self.account, self.keychain, self.run = service, account, keychain, run

    def _item(self) -> List[str]:
        return ["-s", self.service, "-a", self.account]

    def _file(self) -> List[str]:
        return [self.keychain] if self.keychain else []

    def read(self) -> str:
        try:
            code, out = self.run([SECURITY, "find-generic-password", *self._item(), "-w", *self._file()], "")
        except (OSError, subprocess.SubprocessError):
            return ""
        return out.strip() if code == 0 else ""

    def write(self, secret: str) -> None:
        line = " ".join(["add-generic-password", "-U", "-s", quoted(self.service), "-a", quoted(self.account), "-w",
                         quoted(secret), *[quoted(k) for k in self._file()]])
        try:
            self.run([SECURITY, "-i"], line + "\n")
        except (OSError, subprocess.SubprocessError):
            raise SecretStoreError("CARL could not run the security command to use the macOS Keychain.") from None
        if self.read() != secret:
            raise SecretStoreError("CARL could not keep the token in the macOS Keychain. Unlock the Keychain "
                                   "(Keychain Access) and try again.")

    def remove(self) -> None:
        try:
            code, _ = self.run([SECURITY, "delete-generic-password", *self._item(), *self._file()], "")
        except (OSError, subprocess.SubprocessError):
            raise SecretStoreError("CARL could not run the security command to use the macOS Keychain.") from None
        if code not in (0, NOT_FOUND):
            raise SecretStoreError("CARL could not remove the token from the macOS Keychain.")

    def where(self) -> str:
        return "the macOS Keychain"
