"""`carl config [show|path|get KEY|set KEY VALUE...|unset KEY]`: the user's settings (config.json). A wrong key or
value is one `error:` line and exit 1. Contract C-406."""
from __future__ import annotations

import json
import sys
from typing import List

from ...domain.config.settings import get_path, key_path, set_path, unset_path
from ...domain.shared.errors import ConfigError
from ...domain.shared.types import JsonValue
from ..text.config import config_lines
from .base import Cli, arg

USAGE = "config [show|path|get KEY|set KEY VALUE|unset KEY]"


def run(cli: Cli, argv: List[str]) -> int:
    sub = argv[0] if argv else "show"
    if sub == "path":
        print(cli.config_file)
        return 0
    api = cli.api()
    cfg = api.load_config()
    if sub == "show":
        for warn in api.config_warnings():
            print(f"warning: {warn}", file=sys.stderr)
        host = cli.plugins()
        keys, helps = (host.setting_keys(), host.setting_help()) if host is not None else (None, None)
        print("\n".join(config_lines(cfg, cli.tilde(cli.config_file), cli.words, cli.width(), cli.os, keys, helps)))
        return 0
    if sub not in ("get", "set", "unset"):
        raise ConfigError(f"usage: carl.py {USAGE}")
    key = arg(argv, 1, USAGE)
    parts = key_path(key)
    if sub == "get":
        print(json.dumps(get_path(cfg, parts)))
    elif sub == "set":
        value: JsonValue = arg(argv, 2, USAGE) if len(argv) == 3 else list(argv[2:])
        set_path(cfg, parts, value)
        print(f"{key} = {json.dumps(get_path(api.save_config(cfg), parts))}")
    else:
        unset_path(cfg, parts)
        api.save_config(cfg)
    return 0
