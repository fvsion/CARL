"""v4_read_nudge: CARL's delegation hooks in nudge mode (Phase 23, V4; variants/plugins/carl-delegate-hooks)."""
from __future__ import annotations

from typing import List

from agentbench import varlib

DESCRIPTION = "The main agent's 2nd look in a turn, before it decides, gets one line: a large request or a failed fix goes to the coder now"


def apply(home: str) -> List[str]:
    return varlib.install_hooks(home, "nudge")
