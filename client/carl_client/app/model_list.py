"""The installed models with this package's thinking values (data/thinking.json): the model-entry functions of
carl_client.domain.models with the rules passed in. The setup, the sync and the dashboard (through
client/carl_models.py) use these."""
from __future__ import annotations

import json
from typing import Any, Dict, List

from ..adapters import package_data
from ..domain import models
from ..domain.models import LIST_FILE, ModelList, ModelRules


def rules() -> ModelRules:
    return package_data.thinking()


def parse_list(doc: Any, where: str = LIST_FILE) -> ModelList:
    """installed-models.json, checked (models.parse_list); ValueError naming the problem."""
    return models.parse_list(doc, rules(), where)


def load_list(path: str) -> ModelList:
    with open(path, encoding="utf-8") as f:
        return parse_list(json.load(f), path)


def from_server_ids(ids: List[str], ctx: int) -> ModelList:
    """The fallback without a list file: the ids /v1/models gave, as generic on / off entries."""
    return models.from_server_ids(ids, ctx, rules())


def parse_overrides(text: str) -> Dict[str, str]:
    """CODER_THINKING ("MODEL:VALUE,MODEL:VALUE") as {model: value} (models.parse_overrides)."""
    return models.parse_overrides(text, rules())
