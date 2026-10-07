"""Load and save the notes file (JSON)."""
from __future__ import annotations

import json
import os
from typing import List, Optional

from notes.model import Note


def notes_path() -> str:
    return os.environ.get("NOTES_FILE", "notes.json")


def load_notes(path: Optional[str] = None) -> List[Note]:
    path = path or notes_path()
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return [Note.from_dict(item) for item in data.get("notes", [])]


def save_notes(notes: List[Note], path: Optional[str] = None) -> None:
    path = path or notes_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": 2, "notes": [n.to_dict() for n in notes]}, f, indent=2)
    os.replace(tmp, path)


def _next_id(notes: List[Note]) -> int:
    return max((n.id for n in notes), default=0) + 1


def add_note(text: str, tags: List[str], path: Optional[str] = None) -> Note:
    notes = load_notes(path)
    note = Note(id=_next_id(notes), text=text.strip(), tags=tags)
    notes.append(note)
    save_notes(notes, path)
    return note


def mark_done(note_id: int, path: Optional[str] = None) -> bool:
    notes = load_notes(path)
    for n in notes:
        if n.id == note_id:
            n.done = True
            save_notes(notes, path)
            return True
    return False
