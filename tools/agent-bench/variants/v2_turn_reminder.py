"""v2_turn_reminder: one stable reminder line at the end of every user message (Phase 23, V2, rough: measured for the
v2 carl-hooks design only, never shipped; variants/plugins/carl-delegate-hooks in remind mode)."""
from __future__ import annotations

from typing import List

from agentbench import varlib

DESCRIPTION = "Every user message ends with the same one-line delegation reminder (rough, for v2 carl-hooks)"


def apply(home: str) -> List[str]:
    return varlib.install_hooks(home, "remind")
