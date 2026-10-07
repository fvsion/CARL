"""v5_new_file_gate: CARL's delegation hooks in gate mode (Phase 23, V5; variants/plugins/carl-delegate-hooks)."""
from __future__ import annotations

from typing import List

from agentbench import varlib

DESCRIPTION = "The main agent's first write of a NEW file in a turn is stopped: hand the task to the coder (edits of existing files pass)"


def apply(home: str) -> List[str]:
    return varlib.install_hooks(home, "gate")
