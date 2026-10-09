"""brief_kv: the coder's task as 1.12.1 wrote it (Phase 23.4.3's measurement of the brief's format): 1.12.1's
delegation rule and coder (variants/texts/brief_kv_*.md: `git show v1.12.1:client/agents/...`, a "Mode: / Goal: /
Files: ..." task), CARL's brief check, gates and chain off (carl-delegation brief: false, chain: false; Pi's
carl.json delegation.brief and .chain false), and 1.12.1's coder lines in Pi's subagent tool guidelines."""
from __future__ import annotations

from typing import List

from agentbench import varlib

DESCRIPTION = "1.12.1's Key: value task for the coder (rule and coder of v1.12.1); the brief check, gates and chain off"


def apply(home: str) -> List[str]:
    return varlib.set_brief_format(home, "kv")
