"""`carl carl-id` (hidden): this CARL's ID, made once in the settings folder: the dashboard API's /carl/ping answers
it, and a client sends its key only to an API that does. Contract C-414."""
from __future__ import annotations

from typing import List

from .base import Cli


def run(cli: Cli, argv: List[str]) -> int:
    print(cli.tools.carl_id())
    return 0
