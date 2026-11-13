"""`carl verify [NAME...]`: the size and the SHA-256 of downloaded models and their MTP drafters (none named: every
downloaded model). Exit 1 when one fails. Contract C-405."""
from __future__ import annotations

from typing import List

from ...domain.shared.errors import ConfigError
from .base import Cli, unknown_model
from .models import known


def run(cli: Cli, argv: List[str]) -> int:
    api = cli.api()
    models = api.all_models()
    names = argv or [m.get("name", "") for m in models if m.get("status") == "downloaded"]
    unknown = [n for n in names if not api.find(n, models)]
    if unknown:
        raise ConfigError(unknown_model(unknown[0], cli.words))
    if not names:
        print("No model is downloaded, so there is nothing to check.")
    results = [api.verify(known(cli, n, models)) for n in names]
    return 0 if all(results) else 1
