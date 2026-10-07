"""c2_v2_v7: the confirmation candidate after 2026-10-06: V2 (the per-turn reminder) and V7 (the coder's two modes);
V5 (the new-file gate) is off by default (the user)."""
from __future__ import annotations

from typing import List

from agentbench import varlib

DESCRIPTION = "V2 + V7: the per-turn reminder and the coder's two modes (CARL's default; the gate is off)"


def apply(home: str) -> List[str]:
    return (varlib.install_hooks(home, "remind") + varlib.set_coder(home, varlib.text("v7_coder.md"))
            + varlib.set_rule(home, varlib.text("v7_delegation.md")))
