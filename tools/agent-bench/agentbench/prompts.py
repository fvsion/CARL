"""prompts.json: the requests, each with its category, its fixture and the decision it expects. Pure."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import BENCH_DIR

PROMPTS_FILE = os.path.join(BENCH_DIR, "prompts.json")
CATEGORIES = ("large", "stuck", "small", "question")
FIXTURES = ("pycli", "pylib", "webapp", "empty", "pyrules")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


@dataclass(frozen=True)
class Prompt:
    id: str
    category: str
    fixture: str
    text: str
    expect: str                                   # delegate | keep
    hidden: Tuple[str, ...] = field(default=())   # hidden test files (full runs)
    extra: bool = False                           # only when asked for (--with-extra or its id): the later prompts

    @property
    def full(self) -> bool:
        return bool(self.hidden)


def parse_prompts(doc: Mapping[str, Any]) -> List[Prompt]:
    """The prompts of prompts.json, checked: unique ids, known categories and fixtures."""
    expect = doc.get("expect")
    if not isinstance(expect, dict):
        raise ValueError("prompts.json: 'expect' must map each category to delegate or keep")
    out: List[Prompt] = []
    seen = set()
    for item in doc.get("prompts", []):
        if not isinstance(item, dict):
            raise ValueError("prompts.json: each prompt is an object")
        pid, cat, fix, text = item.get("id"), item.get("category"), item.get("fixture"), item.get("prompt")
        if not (isinstance(pid, str) and _ID.match(pid)) or pid in seen:
            raise ValueError(f"prompts.json: bad or repeated id {pid!r}")
        if cat not in CATEGORIES:
            raise ValueError(f"prompts.json: {pid}: unknown category {cat!r}")
        if fix not in FIXTURES:
            raise ValueError(f"prompts.json: {pid}: unknown fixture {fix!r}")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"prompts.json: {pid}: empty prompt")
        exp = expect.get(cat)
        if exp not in ("delegate", "keep"):
            raise ValueError(f"prompts.json: category {cat} expects delegate or keep, not {exp!r}")
        raw_full = item.get("full")
        full: Dict[str, Any] = raw_full if isinstance(raw_full, dict) else {}
        hidden = tuple(str(h) for h in full.get("hidden", []) if isinstance(h, str))
        for h in hidden:
            if not re.fullmatch(r"(test)?_[a-z0-9_]+\.py", h):     # a test file, or a helper the tests import
                raise ValueError(f"prompts.json: {pid}: bad hidden test name {h!r}")
        seen.add(pid)
        out.append(Prompt(pid, cat, fix, text, exp, hidden, item.get("extra") is True))
    return out


def load_prompts(path: str = PROMPTS_FILE) -> List[Prompt]:
    with open(path, encoding="utf-8") as f:
        return parse_prompts(json.load(f))


def select(prompts: Sequence[Prompt], categories: Optional[Sequence[str]] = None,
           ids: Optional[Sequence[str]] = None, full: bool = False, with_extra: bool = False) -> List[Prompt]:
    """The prompts of these categories and ids (all when not given); for full runs only those with hidden tests.
    An extra prompt (added after the baseline) only with with_extra or when its id is named, so a running matrix
    keeps its prompts."""
    for c in categories or ():
        if c not in CATEGORIES:
            raise ValueError(f"unknown category {c!r} (one of: {', '.join(CATEGORIES)})")
    known = {p.id for p in prompts}
    for i in ids or ():
        if i not in known:
            raise ValueError(f"unknown prompt id {i!r}")
    out = [p for p in prompts if (not categories or p.category in categories) and (not ids or p.id in ids)
           and (not p.extra or with_extra or (ids is not None and p.id in ids))]
    return [p for p in out if p.full] if full else out
