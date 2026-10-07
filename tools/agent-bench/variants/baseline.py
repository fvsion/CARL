"""baseline: the configs as CARL's client setup wrote them."""
from __future__ import annotations

from typing import List

from agentbench import varlib

DESCRIPTION = "The configs of CARL's client setup, with no change (a hook variant's plugin out)."


def apply(home: str) -> List[str]:
    """Change nothing in CARL's configs; only take out the delegation hooks a hook variant left (CARL's setup keeps
    plugins it does not own)."""
    return varlib.remove_hooks(home)
