"""Port of the system's secret store: one secret that the user gave CARL (today the Hugging Face access token)."""
from __future__ import annotations

from typing import Protocol


class SecretStore(Protocol):
    """One secret in the place the system keeps secrets (the macOS Keychain, the Windows Credential Manager, the
    Secret Service through secret-tool, or a private file). Contract C-065."""

    def read(self) -> str:
        """The secret, "" when there is none or it cannot be read."""
        ...

    def write(self, secret: str) -> None:
        """Keep the secret (it replaces one that is there). Raises SecretStoreError when it cannot be kept."""
        ...

    def remove(self) -> None:
        """Remove the secret (no error when there is none). Raises SecretStoreError when it cannot be removed."""
        ...

    def where(self) -> str:
        """Where the secret is kept, in words for a message ("the macOS Keychain")."""
        ...
