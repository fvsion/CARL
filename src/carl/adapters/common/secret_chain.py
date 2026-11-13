"""Secret stores one after the other (implements SecretStore, contract C-065): the system's store first, and a
private file when the system's refuses (a locked Keychain, a Linux session with no Secret Service)."""
from __future__ import annotations

from typing import List, Sequence

from ...domain.shared.errors import SecretStoreError
from ...ports import SecretStore


class StoreChain:
    """A read gives the first secret any store has. A write goes to the first store that takes it and removes the
    secret from the other stores (an older copy must not shadow the new one). A remove goes to every store."""

    def __init__(self, stores: Sequence[SecretStore]) -> None:
        self.stores = list(stores)
        self.last = self.stores[0]

    def read(self) -> str:
        for s in self.stores:
            secret = s.read()
            if secret:
                self.last = s
                return secret
        return ""

    def write(self, secret: str) -> None:
        problems: List[str] = []
        for s in self.stores:
            try:
                s.write(secret)
            except SecretStoreError as e:
                problems.append(str(e))
                continue
            self.last = s
            for other in self.stores:
                if other is not s:
                    try:
                        other.remove()
                    except SecretStoreError:
                        pass
            return
        raise SecretStoreError(" ".join(problems))

    def remove(self) -> None:
        problems: List[str] = []
        for s in self.stores:
            try:
                s.remove()
            except SecretStoreError as e:
                problems.append(str(e))
        if problems:
            raise SecretStoreError(" ".join(problems))

    def where(self) -> str:
        return self.last.where()
