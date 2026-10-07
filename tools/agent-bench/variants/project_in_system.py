"""project_in_system: carl-cache does not move the project part (AGENTS.md, OpenCode's <env>, Pi's project context)
to the user message: it stays in the system prompt as the client sends it (Phase 23.1: the rules' role)."""
from __future__ import annotations

from typing import List

from agentbench import varlib

DESCRIPTION = "The project part (AGENTS.md, <env>) stays in the system prompt: carl-cache's move off"


def apply(home: str) -> List[str]:
    return varlib.remove_hooks(home) + varlib.set_move(home, False)
