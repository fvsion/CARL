"""c1_v2_v5_v7: the confirmation candidate (Phase 23, 2026-10-06): V2 (the per-turn reminder), V5 (the new-file
gate) and V7 (the coder's two modes) together."""
from __future__ import annotations

from typing import List

from agentbench import varlib

DESCRIPTION = "V2 + V5 + V7: the per-turn reminder, the new-file gate and the coder's two modes together"


def apply(home: str) -> List[str]:
    return (varlib.install_hooks(home, "remind,gate") + varlib.set_coder(home, varlib.text("v7_coder.md"))
            + varlib.set_rule(home, varlib.text("v7_delegation.md")))
