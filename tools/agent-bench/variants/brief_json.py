"""brief_json (OBSOLETE: the user dropped JSON on 2026-10-09 and chose TOML; its texts have the keys of revision 1 of
the brief, which CARL's check now refuses with the names of revision 4's keys; kept as a record, not deleted).
The coder's brief and report as JSON with the same keys and structure as CARL's TOML of revision 1 (Phase 23.4.3's
measurement of the brief's format): variants/texts/brief_json_*.md (CARL's delegation rule and coder, only the
format parts converted, the examples faithfully), the brief check, the gates and the chain on (carl-brief.js reads
JSON too; a refusal of a task with no brief names JSON: carl-delegation briefFormat "json", Pi's carl.json
delegation.brief_format "json"), and Pi's subagent tool guidelines naming a JSON brief."""
from __future__ import annotations

from typing import List

from agentbench import varlib

DESCRIPTION = ("OBSOLETE (JSON dropped, 2026-10-09; revision 1's keys): the coder's brief and report as JSON, the "
               "same structure as revision 1's TOML; the check, gates and chain on")


def apply(home: str) -> List[str]:
    return varlib.set_brief_format(home, "json")
