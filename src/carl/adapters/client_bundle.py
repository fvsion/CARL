"""The one bridge from CARL to its client package (client/carl_client, which ships alone in the zip; rule R8): what
the package's own data says, read through the package. CARL may import carl_client.domain (the shared kernel, decision
12) anywhere; everything else of the package comes through here, and the composition hands it on.

  model_rules   the model entries' rules and values of the client configs (carl_client/data/thinking.json), the
                same the installer and the sync use, so a pasted config and an installed one list the same entries
"""
from __future__ import annotations

from carl_client.app import model_list as client_models
from carl_client.domain.models import ModelRules


def model_rules() -> ModelRules:
    """The rules the client configs' model entries follow (read once per process)."""
    return client_models.rules()
