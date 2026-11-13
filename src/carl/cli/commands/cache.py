"""`carl cache [show|trim|clear]`: the disk cache of prompt states (application/disk_cache.py): what it holds, a trim
to its limit (cache.disk_gb), or a clear of every file. Contract C-408."""
from __future__ import annotations

from typing import List

from ...domain.shared.errors import ConfigError
from ..text.cache import clear_line, show_lines, trim_lines
from .base import Cli


def run(cli: Cli, argv: List[str]) -> int:
    sub = argv[0] if argv else "show"
    cache = cli.tools.disk_cache()
    folder = cli.tilde(cache.folder)
    cfg = cli.api().load_config()                   # read first, as before: a broken config.json stops every sub
    if sub == "trim":
        print("\n".join(trim_lines(cache.trim(cfg))))
        return 0
    if sub == "clear":
        print(clear_line(cache.clear(), folder))
        return 0
    if sub != "show":
        raise ConfigError(f"cache takes show, trim or clear. {cli.words.command} cache --help tells more.")
    report = cache.show(cfg)
    print("\n".join(show_lines(report, folder, cache.saving() if report.packed else 0, cli.width())))
    return 0
