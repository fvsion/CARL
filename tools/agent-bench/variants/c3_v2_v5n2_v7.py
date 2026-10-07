"""c3_v2_v5n2_v7: V2 + V7 with the gate turned on at N = 2 (the first new file of a turn passes): the option a user
can turn on (2026-10-06)."""
from __future__ import annotations

from typing import List

from agentbench import varlib

DESCRIPTION = "V2 + V7 + the gate from the 2nd new file of a turn (the user's option)"


def apply(home: str) -> List[str]:
    return (varlib.install_hooks(home, "remind,gate:2") + varlib.set_coder(home, varlib.text("v7_coder.md"))
            + varlib.set_rule(home, varlib.text("v7_delegation.md")))
