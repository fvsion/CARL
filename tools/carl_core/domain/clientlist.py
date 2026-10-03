"""The installed models as the OpenCode / Pi configs list them (client/carl_models.py builds
the entries from this list; installed-models.json carries it to a VM). Pure.

Only downloaded models, never the whole catalogue. Each one: its CARL name (the id the server
serves it under), its label, its window per slot (its effective settings) and how it thinks
(the catalogue's or the user's card "thinking"; a custom model without one: on / off).
"""
from __future__ import annotations

import re
from typing import Callable, List, Optional

from .types import JsonObject, JsonValue, ModelInfo

SCHEMA = 1
LABEL_MAX = 80
DEFAULT_THINKING = "on-off"
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def client_entry(m: ModelInfo, ctx: int) -> JsonObject:
    """One installed model for the client configs."""
    label = _CONTROL.sub("", str(m.get("label") or m.get("name", "")))[:LABEL_MAX]
    return {"id": m.get("name", ""), "label": label or m.get("name", ""), "ctx": int(ctx),
            "thinking": m.get("thinking") or DEFAULT_THINKING}


def client_list(models: List[ModelInfo], ctx_of: Callable[[ModelInfo], int], default: Optional[str]) -> JsonObject:
    """installed-models.json: the downloaded models, and the default (the model a start loads)
    when it is one of them."""
    entries: List[JsonValue] = [client_entry(m, ctx_of(m)) for m in models if m.get("status") == "downloaded"]
    ids = {e.get("id") for e in entries if isinstance(e, dict)}
    return {"schema": SCHEMA, "default": default if default in ids else None, "models": entries}
