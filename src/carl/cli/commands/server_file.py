"""`carl server-file --port PORT --model NAME --swa full|window|- [--kv K] [--ctx N] [--slots N]` (hidden): the
clients' facts about a single-model start, server-PORT.json in the settings folder (C-205). Contract C-414."""
from __future__ import annotations

from typing import List

from .base import Cli, arg

USAGE = "server-file --port PORT --model NAME --swa full|window|- [--kv K] [--ctx N] [--slots N]"


def run(cli: Cli, a: List[str]) -> int:
    def opt(name: str, default: str = "") -> str:
        return arg(a, a.index(name) + 1, USAGE) if name in a else default
    port, model = opt("--port"), opt("--model")
    if not port or not model:
        arg([], 0, USAGE)
    cli.tools.server_file().write_single(int(port), model, opt("--swa", "-"), opt("--kv"),
                                         int(opt("--ctx", "0") or 0), int(opt("--slots", "0") or 0))
    return 0
