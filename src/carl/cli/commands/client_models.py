"""`carl client-models [--running NAME] [--router]` (hidden): the models for the OpenCode and Pi configs, as JSON
(the client setup writes it to client/installed-models.json). Single-model mode: only the model the server runs
(NAME, else the model a start loads); --router: every installed model. Contract C-414."""
from __future__ import annotations

import json
from typing import List

from .base import Cli, arg


def run(cli: Cli, a: List[str]) -> int:
    running = arg(a, a.index("--running") + 1, "client-models [--running NAME] [--router]") if "--running" in a \
        else None
    print(json.dumps(cli.api().client_models(None, running or None, True if "--router" in a else None), indent=2))
    return 0
