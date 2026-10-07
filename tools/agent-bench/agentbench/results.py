"""The results file: one JSON object per line (JSONL), one line per run. Resumable: a cell with a line is done."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from .events import LEGACY_MEASURE, MEASURE   # LEGACY: schema 1 lines (no measure; decision = the first tool)

SCHEMA = 2
Key = Tuple[str, str, str, str, str, str, int, str]
KEY_FIELDS = ("mode", "model", "client", "thinking", "variant", "prompt", "run", "measure")


@dataclass
class Result:
    """One run. decision: the practical decision (the first decisive action: delegated | self-write | answer |
    undecided | error); decision_strict: the first tool call (delegated | self | answer | error). correct and
    correct_strict: None for an error."""
    time: str
    mode: str                       # decision | full
    model: str
    client: str
    client_version: str
    thinking: str                   # default | off | a level
    thinking_level: str             # what the client used (OpenCode variant, Pi level)
    variant: str
    category: str
    prompt: str
    run: int
    decision: str
    expected: str                   # delegate | keep
    correct: Optional[bool]
    measure: str = MEASURE
    decision_strict: str = ""
    correct_strict: Optional[bool] = None
    decision_tool: str = ""         # the tool of the decisive action ("" for answer, undecided, error)
    decision_args: Dict[str, Any] = field(default_factory=dict)
    looks: int = 0                  # tool calls before the decisive action (or before the end)
    decision_seconds: Optional[float] = None   # from the first model step to the decisive action
    undecided_reason: str = ""
    tool: str = ""                  # the first tool call (strict)
    args: Dict[str, Any] = field(default_factory=dict)
    step_tools: List[str] = field(default_factory=list)
    seconds: float = 0.0            # from the client's start to the decision
    tool_seconds: Optional[float] = None   # from the first model step to the first tool call
    startup_seconds: Optional[float] = None
    thinking_tokens: Optional[int] = None           # until the first tool call
    thinking_tokens_decision: Optional[int] = None  # until the harness stopped the client
    thinking_chars: int = 0
    first_event: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    full: Dict[str, Any] = field(default_factory=dict)   # full runs: coder, tests, hidden tests
    schema: int = SCHEMA

    def key(self) -> Key:
        return (self.mode, self.model, self.client, self.thinking, self.variant, self.prompt, self.run, self.measure)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=False)


def key_of(doc: Dict[str, Any]) -> Optional[Key]:
    try:
        return (str(doc["mode"]), str(doc["model"]), str(doc["client"]), str(doc["thinking"]),
                str(doc["variant"]), str(doc["prompt"]), int(doc["run"]), str(doc.get("measure", LEGACY_MEASURE)))
    except (KeyError, TypeError, ValueError):
        return None


def read_results(path: str) -> Iterator[Dict[str, Any]]:
    """The result lines of the file; a broken line (a run stopped while it wrote) is skipped."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                doc = json.loads(line)
            except ValueError:
                continue
            if isinstance(doc, dict):
                yield doc


class ResultStore:
    """Appends results to a JSONL file and knows which cells have one."""

    def __init__(self, path: str, retry_errors: bool = False) -> None:
        self.path = path
        self.retry_errors = retry_errors
        self._done: Set[Key] = set()
        for doc in read_results(path):
            k = key_of(doc)
            if k is not None and not (retry_errors and doc.get("decision") == "error"):
                self._done.add(k)

    def done(self, key: Key) -> bool:
        return key in self._done

    def done_keys(self) -> Set[Key]:
        return set(self._done)

    def append(self, result: Result) -> None:
        d = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(d, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(result.to_json() + "\n")
            f.flush()
            os.fsync(f.fileno())
        self._done.add(result.key())
