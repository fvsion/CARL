"""The Note record."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class Note:
    id: int
    text: str
    tags: List[str] = field(default_factory=list)
    done: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "text": self.text, "tags": list(self.tags), "done": self.done}

    @staticmethod
    def from_dict(item: Dict[str, Any]) -> "Note":
        return Note(id=int(item["id"]), text=str(item["text"]), tags=list(item["tags"]),
                    done=bool(item.get("done", False)))
