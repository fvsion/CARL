"""v1_short_rule: the delegation rule shorter and the last instruction (Phase 23, V1)."""
from __future__ import annotations

from typing import List

from agentbench import varlib

DESCRIPTION = "The delegation rule shorter (one sort before the first tool call) and placed last"


def apply(home: str) -> List[str]:
    return varlib.remove_hooks(home) + varlib.set_rule(home, varlib.text("v1_delegation.md"), last=True)
