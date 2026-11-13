"""What `carl tune` says while it runs, as events: the one place that knows the lines of its output.

Auto-tune prints one line for each thing that happens (a step starts, a note, a model of an "all" run starts, the
end) and the dashboard follows them in a log file. The printer (adapters/common/console.py) makes the lines with
the functions here; the dashboard reads them back with parse_line(), so nobody else reads the text of a line:

  STEP 2/7 speculation MTP + n-gram, 2 guesses   a step (the old form: "speculation ngram-mod n=2: prose 26.0 ...")
    a note under the step                        a note (two spaces first)
  MODEL 3/5 NAME                                 `carl tune all`: the next model
  DONE {settings}  /  DONE ALL 5 of 5 tuned      the end
  error: ...                                     a failure

Pure.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Tuple

STEP_PREFIX = "STEP "
NOTE_PREFIX = "  "
MODEL_PREFIX = "MODEL "
DONE_PREFIX = "DONE "
SPECULATION = "speculation"                         # the topic of the steps that measure a mode
OLD_MODES = ("draft-mtp,ngram-mod", "draft-mtp", "ngram-mod", "none")      # the modes of the old step lines


class Kind(str, Enum):
    """What a line of the output is."""
    STEP = "step"
    NOTE = "note"
    MODEL = "model"
    DONE = "done"
    ERROR = "error"
    OTHER = "other"


@dataclass(frozen=True)
class TuneProgress:
    """One line of the output. text is the line as printed; the rest is what it says."""
    kind: Kind
    text: str
    n: int = 0              # STEP, MODEL: this one of `of`
    of: int = 0
    topic: str = ""         # STEP: what it measures ("memory", "speculation", "read speed", ...)
    spec: str = ""          # STEP speculation, the old form: the mode ("ngram-mod") ...
    spec_n: int = 0         # ... and its guesses
    mode: str = ""          # STEP speculation: the mode as the line says it ("MTP + n-gram, 2 guesses", "none")
    detail: str = ""        # STEP: what follows the topic (from its colon, if any); MODEL: the name; DONE: the record


def step_line(text: str) -> str:
    """The line for a step; text is "N/OF what it measures"."""
    return f"{STEP_PREFIX}{text}"


def note_line(text: str) -> str:
    """The line for a note under a step."""
    return f"{NOTE_PREFIX}{text}"


def model_line(k: int, n: int, name: str) -> str:
    """The line before each model of `carl tune all`."""
    return f"{MODEL_PREFIX}{k}/{n} {name}"


def done_line(record: str) -> str:
    """The last line of a tune: what it saved (a model's settings, or the count of `all`)."""
    return f"{DONE_PREFIX}{record}"


def _pair(text: str) -> Tuple[int, int]:
    """"3/7" as (3, 7); (0, 0) when it is not a pair of numbers."""
    a, _, b = text.partition("/")
    return (int(a), int(b)) if a.isdigit() and b.isdigit() else (0, 0)


def _step(line: str) -> TuneProgress:
    head, _, what = line[len(STEP_PREFIX):].partition(" ")
    n, of = _pair(head)
    if not of:                                      # no "N/OF": the whole text is what the step measures
        what = line[len(STEP_PREFIX):]
    if what.startswith(SPECULATION + " "):
        mode, colon, rest = what[len(SPECULATION) + 1:].partition(":")
        spec, _, num = mode.rpartition(" n=")
        if num.isdigit() and spec in OLD_MODES:
            return TuneProgress(Kind.STEP, line, n, of, SPECULATION, spec, int(num), mode, colon + rest)
        return TuneProgress(Kind.STEP, line, n, of, SPECULATION, mode=mode, detail=colon + rest)
    topic, colon, rest = what.partition(":")
    return TuneProgress(Kind.STEP, line, n, of, topic, detail=colon + rest)


def parse_line(line: str) -> TuneProgress:
    """A line of the output as an event."""
    if line.startswith(STEP_PREFIX):
        return _step(line)
    if line.startswith(DONE_PREFIX):
        return TuneProgress(Kind.DONE, line, detail=line[len(DONE_PREFIX):])
    if line.startswith(MODEL_PREFIX):
        head, _, name = line[len(MODEL_PREFIX):].partition(" ")
        n, of = _pair(head)
        return TuneProgress(Kind.MODEL, line, n, of, detail=name)
    if line.startswith(NOTE_PREFIX):
        return TuneProgress(Kind.NOTE, line, detail=line[len(NOTE_PREFIX):])
    if line.lower().startswith(("error", "refused")):
        return TuneProgress(Kind.ERROR, line, detail=line)
    return TuneProgress(Kind.OTHER, line)
