"""The client config for the sync of other computers (`carl push`): the installed models, as the client configs list
them, published on purpose. Over the ClientConfigStore port (contract C-037), so the command line no longer reaches
into the dashboard's modules (W4.1); the dashboard's API serves what this publishes. Contract C-122.
"""
from __future__ import annotations

from ..domain.shared.types import JsonObject
from ..ports.cache import ClientConfigStore


class ClientSync:
    """Publish the client config of one settings folder."""

    def __init__(self, store: ClientConfigStore) -> None:
        self.store = store

    def push(self, models: JsonObject) -> str:
        """Publish models (client-models' document); its version. The same content gives the same version, so the
        clients do not apply it again."""
        return self.store.publish(models)
