"""`carl delete NAME`: a downloaded model's file and its MTP drafter (and its Auto-tune results). Contract C-405."""
from __future__ import annotations

from typing import List

from ...domain.shared.errors import ConfigError
from .base import Cli, arg
from .models import known


def run(cli: Cli, argv: List[str]) -> int:
    m = known(cli, arg(argv, 0, "delete NAME"))
    if m.get("status") != "downloaded" and not m.get("draft_path"):
        raise ConfigError(f"{m.get('name')} is not downloaded, so there is nothing to delete.")
    cli.api().delete(m)
    print(f"CARL deleted {cli.tilde(m.get('path', ''))}"
          + (f" and its MTP drafter {cli.tilde(m.get('draft_path', ''))}." if m.get("draft_path") else "."))
    return 0
