"""One secret in the Secret Service (GNOME Keyring, KWallet) through secret-tool (implements SecretStore, contract
C-065). The standard library has no D-Bus, so CARL asks the program. The secret goes to `secret-tool store` on standard
input and comes back on the standard output of `lookup`; it is never an argument. Where secret-tool is not installed,
or the session has no Secret Service, a write is refused and the chain (adapters/common/secret_chain.py) keeps the
secret in a private file instead.
"""
from __future__ import annotations

import subprocess
from typing import Callable, List, Tuple

from ...domain.shared.errors import SecretStoreError

TIMEOUT = 30

Run = Callable[[List[str], str], Tuple[int, str]]


def run_program(argv: List[str], stdin: str) -> Tuple[int, str]:
    """A command: its exit code and its output. stdin: what it reads ("" for none)."""
    done = subprocess.run(argv, input=stdin.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          timeout=TIMEOUT)
    return done.returncode, done.stdout.decode("utf-8", errors="replace")


class SecretTool:
    """The secret with the attributes `service` and `account`. program: the path of secret-tool; label: the name
    the keyring shows; run: the command runner (the tests give their own)."""

    def __init__(self, program: str, service: str, account: str, label: str, run: Run = run_program) -> None:
        self.program, self.service, self.account, self.label, self.run = program, service, account, label, run

    def _attributes(self) -> List[str]:
        return ["service", self.service, "account", self.account]

    def read(self) -> str:
        try:
            code, out = self.run([self.program, "lookup", *self._attributes()], "")
        except (OSError, subprocess.SubprocessError):
            return ""
        return out.strip() if code == 0 else ""

    def write(self, secret: str) -> None:
        try:
            code, _ = self.run([self.program, "store", f"--label={self.label}", *self._attributes()], secret)
        except (OSError, subprocess.SubprocessError):
            raise SecretStoreError("CARL could not run secret-tool.") from None
        if code != 0 or self.read() != secret:
            raise SecretStoreError("CARL could not keep the token with secret-tool (no Secret Service in this "
                                   "session).")

    def remove(self) -> None:
        try:
            self.run([self.program, "clear", *self._attributes()], "")
        except (OSError, subprocess.SubprocessError):
            raise SecretStoreError("CARL could not run secret-tool.") from None

    def where(self) -> str:
        return "the Secret Service (secret-tool)"
