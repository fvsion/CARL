"""`carl launch-env [--model NAME|PATH] [--no-config]` (hidden): the KEY=value lines of a start's model and settings
(domain/server/launch_plan.py), its note on stderr. Every value is from a safe character set (C-106). Contract
C-414."""
from __future__ import annotations

import sys
from typing import List

from ...domain.server.launch_plan import shell_lines
from .base import Cli, arg


def run(cli: Cli, a: List[str]) -> int:
    model = arg(a, a.index("--model") + 1, "launch-env [--model NAME|PATH] [--no-config]") if "--model" in a else None
    env, note = cli.api().launch_env(model, use_config="--no-config" not in a)
    if note:
        print(note, file=sys.stderr)
    print(shell_lines(env))
    return 0
