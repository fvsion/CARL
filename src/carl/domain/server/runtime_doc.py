"""What this Mac's clients need to know about the running server (Phase 23.5): ~/.config/carl/server-PORT.json.

llama.cpp's /props does not say how a model's cache was set up, and carl-cache needs it: with the window cache
(a sliding-window model without --swa-full) a saved prompt or session cannot be reused, so restoring and saving
them only costs time and disk. The launcher writes this file at each start (serve-llama.sh; router mode: from its
presets); carl-cache reads it (client/shared/carl-cache.js cacheMode()), the dashboard serves it to other computers.

    {"version": 1, "port": 9275, "mode": "single", "written": "2026-10-10T14:00:00",
     "models": {"gemma-4-e4b": {"swa": "window", "kv": "q4_0", "ctx": 98304, "slots": 2}}}

swa: "full" (sliding-window layers at full length: saved states restore), "window" (the window cache: they do not),
"none" (no sliding-window layers). The document is pure; adapters/common/runtime_files.py reads and writes the
files (atomic, mode 0600, indent 2 and a trailing newline).
"""
from __future__ import annotations

import json
from typing import Any, Dict, Mapping

from ..shared.constants import DEFAULT_PORT  # noqa: F401  (tools/monitor/cli.py reads it here)

VERSION = 1
SWA = ("full", "window", "none")
CARL_ID_FILE = "carl-id"            # in the settings folder: this CARL's ID
TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"   # "written": the local time, no zone


def file_name(port: int) -> str:
    """server-PORT.json, in the settings folder."""
    return f"server-{int(port)}.json"


def model_entry(swa: str, kv: str = "", ctx: int = 0, slots: int = 0) -> Dict[str, Any]:
    """One model's cache set-up; swa "-" (the launcher's word for a model with no sliding window) is "none"."""
    swa = "none" if swa in ("-", "") else swa
    if swa not in SWA:
        raise ValueError(f"swa takes {', '.join(SWA)}, not {swa!r}")
    out: Dict[str, Any] = {"swa": swa}
    if kv:
        out["kv"] = kv
    if ctx:
        out["ctx"] = int(ctx)
    if slots:
        out["slots"] = int(slots)
    return out


def document(port: int, mode: str, models: Mapping[str, Mapping[str, Any]], written: str = "") -> Dict[str, Any]:
    """The file's document. written: the time of the write in TIME_FORMAT (the caller reads its clock)."""
    if mode not in ("single", "router"):
        raise ValueError(f"mode takes single or router, not {mode!r}")
    return {"version": VERSION, "port": int(port), "mode": mode,
            "written": written, "models": {k: dict(v) for k, v in models.items()}}


def with_api_port(doc: Mapping[str, Any], api_port: int) -> Dict[str, Any]:
    """The document with the dashboard API's real port (the dashboard adds it once its API runs)."""
    out = dict(doc)
    out["api_port"] = int(api_port)
    return out


def dumps(doc: Mapping[str, Any]) -> str:
    """The file's text: indent 2 and a trailing newline."""
    return json.dumps(doc, indent=2) + "\n"


def loads(text: str) -> Dict[str, Any]:
    """The document in a file's text; {} when it is not a JSON object."""
    try:
        doc = json.loads(text)
    except ValueError:
        return {}
    return doc if isinstance(doc, dict) else {}


def is_carl_id(value: str) -> bool:
    """A CARL ID: 32 lower-case hex digits (the dashboard API's /carl/ping answers it; not a secret)."""
    return len(value) == 32 and all(c in "0123456789abcdef" for c in value)
