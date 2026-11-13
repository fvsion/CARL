"""`carl paths --json` (hidden): the product's paths as one JSON object, for the programs that use CARL from outside
(the benches): the clone, the entry, the catalogue, the client folder, the settings folder and its files, the models
folder, the logs and the templates. Contract C-419."""
from __future__ import annotations

import json
from typing import List

from ...domain.shared.errors import ConfigError
from .base import Cli


def run(cli: Cli, argv: List[str]) -> int:
    if argv != ["--json"]:
        raise ConfigError(f"usage: {cli.words.command} paths --json")
    print(json.dumps(cli.tools.paths(), indent=2, sort_keys=True))
    return 0
