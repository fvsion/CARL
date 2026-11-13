"""Where an Auto-tune record was measured, and whether a start is on the same build and backend (decision A72). Pure.

A record keeps the llama.cpp build that measured it (TuneRecord.llama_cpp, the "version:" line of llama-server) and the
backend it ran on (TuneRecord.backend: cpu, cuda-13, vulkan, metal, ...). The context memory type that a tune on the
CPU measured is the CPU's: a start on a GPU takes none of it (kv_applies). Every start compares the record with the
installed build and its backend (stale): on a difference the start keeps the record's settings, says the result is
from another build and so an estimate now, and offers a new tune. A part that either side does not say is not a
difference (a record from before the backend was kept, a build whose version CARL cannot read).
"""
from __future__ import annotations

from typing import Mapping, Optional

from ..llama.version import LlamaVersion, compare, parse_version
from ..shared.platform_words import MACOS, PlatformWords

CPU = "cpu"


def _tune(m: Mapping[str, object]) -> Mapping[str, object]:
    local = m.get("local")
    tune = local.get("tune") if isinstance(local, dict) else None
    return tune if isinstance(tune, dict) else {}


def measured_kv(tune: Mapping[str, object]) -> bool:
    """The record measured the context memory types (results.kv has a row for each type that fit): a tune on the CPU."""
    results = tune.get("results")
    return bool(isinstance(results, dict) and results.get("kv"))


def backend_of(tune: Mapping[str, object]) -> str:
    """The backend a record was measured on: its own word, else cpu for a record that measured the context memory
    types (only a tune on the CPU alone does), else "" (not known)."""
    named = tune.get("backend")
    if isinstance(named, str) and named.strip():
        return named.strip()
    return CPU if measured_kv(tune) else ""


def kv_applies(tune: Mapping[str, object], cpu: bool) -> bool:
    """Does the record's context memory type (settings.kv) hold for a start here? A record that measured the types
    holds for a start on the CPU alone only; one that did not (a GPU tune holds q4_0 as a constant, not a choice) holds
    for a start on a GPU backend or a Mac only: a start on the CPU alone picks its type itself (cpu_kv)."""
    return measured_kv(tune) == cpu


def version_of(text: str) -> Optional[LlamaVersion]:
    """The version in a record's llama_cpp or in `llama-server --version` output (with or without "version:")."""
    if not text or text.strip() in ("", "?"):
        return None
    return parse_version(text) or parse_version("version: " + text.strip())


def differences(m: Mapping[str, object], build_text: str, backend: str) -> str:
    """How the build and backend of a start differ from those of the model's record, in words ("" when they are the
    same, when the model has no record, or when the parts that differ are not known on both sides): "llama.cpp 0.6.0
    (build 11429) then, 0.7.0 (build 11738) now; the cpu backend then, cuda-13 now". build_text: the installed
    `llama-server --version` output; backend: the start's (cpu, cuda-13, metal, ...)."""
    tune = _tune(m)
    if not tune.get("settings"):
        return ""
    then, now = version_of(str(tune.get("llama_cpp") or "")), version_of(build_text)
    was = backend_of(tune)
    parts = []
    if then is not None and now is not None and compare(then, now) not in (0, None):
        parts.append(f"llama.cpp {then.text()} then, {now.text()} now")
    if was and backend and not same_backend(was, backend):
        parts.append(f"the {was} backend then, {backend} now")
    return "; ".join(parts)


def same_backend(a: str, b: str) -> bool:
    """The same backend: equal, or one is the family of the other (a build of your own that CARL could only class as cuda
    is the cuda-13 of an install)."""
    a, b = a.lower(), b.lower()
    return a == b or a.split("-")[0] == b or b.split("-")[0] == a


def stale(m: Mapping[str, object], build_text: str, backend: str, words: PlatformWords = MACOS) -> str:
    """The start line for a model whose record was measured on another llama.cpp build or backend ("" when it is this
    build and backend): the result is kept and is an estimate now; a new tune is offered."""
    parts = differences(m, build_text, backend)
    if not parts:
        return ""
    name = str(m.get("name") or "")
    return (f"The Auto-tune result for {name} is from another build ({parts}), so it is an estimate now. "
            f"To measure again: {words.command} tune {name}")
