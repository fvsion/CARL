"""The installed models as the OpenCode / Pi configs list them (client/carl_models.py builds
the entries from this list; installed-models.json carries it to a VM). Pure.

Only downloaded models, never the whole catalogue. Each one: its CARL name (the id the server
serves it under), its label, its window per slot (its effective settings), how it thinks
(the catalogue's or the user's card "thinking"; a custom model without one: on / off) and the
thinking of each role (thinking_main, thinking_coder: its effective settings, domain/thinking.py).
"""
from __future__ import annotations

import re
from typing import Callable, List, Mapping, Optional

from . import thinking
from .types import JsonObject, JsonValue, ModelInfo

SCHEMA = 1
LABEL_MAX = 80
DEFAULT_THINKING = "on-off"
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


Roles = Mapping[str, object]
"""A model's settings that hold the thinking of each role (thinking_main, thinking_coder)."""


def client_entry(m: ModelInfo, ctx: int, roles: Optional[Roles] = None) -> JsonObject:
    """One installed model for the client configs. roles: its settings with the thinking of each role (None: the
    defaults of its kind)."""
    label = _CONTROL.sub("", str(m.get("label") or m.get("name", "")))[:LABEL_MAX]
    # Qwen has a separate non-thinking sampling (its model cards); Gemma 4 and other models use one
    # sampling for every use, so their "none" variant only turns thinking off
    qwen = str(m.get("family") or "").startswith("qwen")
    kind = m.get("thinking") or DEFAULT_THINKING
    return {"id": m.get("name", ""), "label": label or m.get("name", ""), "ctx": int(ctx),
            "thinking": kind, "off_sampling": "qwen" if qwen else "same", **thinking.values(kind, roles)}


def client_list(models: List[ModelInfo], ctx_of: Callable[[ModelInfo], int], default: Optional[str],
                roles_of: Optional[Callable[[ModelInfo], Roles]] = None) -> JsonObject:
    """installed-models.json: the downloaded models, and the default (the model a start loads)
    when it is one of them. roles_of: a model's settings with the thinking of each role (None: the defaults)."""
    entries: List[JsonValue] = [client_entry(m, ctx_of(m), roles_of(m) if roles_of else None)
                                for m in models if m.get("status") == "downloaded"]
    ids = {e.get("id") for e in entries if isinstance(e, dict)}
    return {"schema": SCHEMA, "default": default if default in ids else None, "models": entries}
