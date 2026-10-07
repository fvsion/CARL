"""v7_coder_modes: the coder works in one of two exclusive modes, writing code or writing tests (Phase 23, V7)."""
from __future__ import annotations

from typing import List

from agentbench import varlib

DESCRIPTION = "The coder's two modes (Mode: code / Mode: test, exclusive); the rule says which to pick"


def apply(home: str) -> List[str]:
    return (varlib.remove_hooks(home) + varlib.set_coder(home, varlib.text("v7_coder.md"))
            + varlib.set_rule(home, varlib.text("v7_delegation.md")))
