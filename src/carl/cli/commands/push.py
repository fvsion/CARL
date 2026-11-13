"""`carl push`: publish the client config (the installed models) for the sync of other computers
(application/client_sync.py); the dashboard's API serves it. Contract C-410."""
from __future__ import annotations

from typing import List

from ...ui.layout import wrap
from .base import Cli


def run(cli: Cli, argv: List[str]) -> int:
    version = cli.tools.client_sync().push(cli.api().client_models())
    print("\n".join(wrap(f"CARL is ready to send the client config to other computers (version {version}). "
                         "The dashboard sends it while it runs. OpenCode and Pi apply it at their next start.",
                         cli.width())))
    return 0
