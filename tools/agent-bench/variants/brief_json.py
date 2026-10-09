"""brief_json: the coder's brief and report as JSON with the same keys and structure as CARL's TOML (Phase 23.4.3's
measurement of the brief's format): variants/texts/brief_json_*.md (CARL's delegation rule and coder, only the
format parts converted, the examples faithfully), the brief check, the gates and the chain on (carl-brief.js reads
JSON too; a refusal of a task with no brief names JSON: carl-delegation briefFormat "json", Pi's carl.json
delegation.brief_format "json"), and Pi's subagent tool guidelines naming a JSON brief."""
from __future__ import annotations

from typing import List

from agentbench import varlib

DESCRIPTION = "The coder's brief and report as JSON, the same structure as the TOML; the check, gates and chain on"


def apply(home: str) -> List[str]:
    return varlib.set_brief_format(home, "json")
