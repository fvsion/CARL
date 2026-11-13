"""`carl card NAME [set FIELD VALUE... | unset FIELD]`: a model's card; a custom model's card can change (a catalogue
model's is refused with exit 1). Contract C-407."""
from __future__ import annotations

from typing import List

from ...domain.shared.errors import ConfigError
from ..text.card import card_lines
from .base import Cli, arg
from .models import known

USAGE = "card NAME [set FIELD VALUE... | unset FIELD]"


def run(cli: Cli, argv: List[str]) -> int:
    name = arg(argv, 0, USAGE)
    sub = argv[1] if len(argv) > 1 else "show"
    api = cli.api()
    if sub == "set":
        if len(argv) < 4:
            raise ConfigError(f"usage: carl.py {USAGE}")
        api.set_card_field(name, argv[2], argv[3:])
    elif sub == "unset":
        if len(argv) != 3:
            raise ConfigError(f"usage: carl.py {USAGE}")
        api.unset_card_field(name, argv[2])
    elif sub != "show" or len(argv) > 2:
        raise ConfigError(f"usage: carl.py {USAGE}")
    print("\n".join(card_lines(known(cli, name), cli.words, cli.width())))
    return 0
