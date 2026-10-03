"""The OpenCode and Pi model entries for the models installed on a CARL server.

One entry per installed (downloaded) model, never the whole catalogue: the id is the model's
CARL name, which is also the name the server serves it under, so OpenCode's /models and Pi's
/model list what this Mac really has. Each entry carries its family's thinking options:
  on-off   thinking on or off only (Qwen3.6 35B-A3B): variants none / high, default high
  effort   effort levels (Qwen3.8 27B): variants none / low / medium / xhigh, default low
The list comes from the server Mac (tools/carl.py client-models, written to
installed-models.json next to this file, so a client bundle copied into a VM carries it), or,
without it, from the server's /v1/models (ids only: generic on / off entries).

Standalone (stdlib only): it runs in the VM, from client/configure.py, and in the dashboard.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

LIST_FILE = "installed-models.json"
SCHEMA = 1
THINKING = ("on-off", "effort")
DEFAULT_CTX = 98304
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
# Qwen's non-thinking sampling, sent with the "none" variant.
NO_THINK = {"reasoningEffort": "none", "temperature": 0.7, "top_p": 0.8, "presence_penalty": 1.5}
OC_LEVELS = ("minimal", "low", "medium", "high", "xhigh")
OC_ON = {"on-off": ("high",), "effort": ("low", "medium", "xhigh")}
DEFAULT_EFFORT = {"on-off": "high", "effort": "low"}
PI_LEVEL_MAP: Dict[str, Dict[str, Optional[str]]] = {
    "on-off": {"minimal": None, "low": None, "medium": None, "xhigh": None},
    "effort": {"minimal": None, "high": None, "xhigh": "xhigh"},
}


@dataclass(frozen=True)
class ClientModel:
    """One installed model as the clients see it."""
    id: str
    label: str
    ctx: int
    thinking: str

    def title(self, ctx: Optional[int] = None) -> str:
        return f"{self.label} — llama.cpp, {(ctx or self.ctx) // 1024}K"


@dataclass(frozen=True)
class ModelList:
    """The installed models and the one to make the default."""
    models: List[ClientModel]
    default: Optional[str]

    @property
    def ids(self) -> List[str]:
        return [m.id for m in self.models]


def parse_list(doc: Any, where: str = LIST_FILE) -> ModelList:
    """installed-models.json, checked: ids become config keys and labels are shown, so both
    are plain text. Raises ValueError naming the problem."""
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA or not isinstance(doc.get("models"), list):
        raise ValueError(f"{where}: not a CARL model list (schema {SCHEMA})")
    out: List[ClientModel] = []
    for i, m in enumerate(doc["models"]):
        at = f"{where}: models[{i}]"
        if not isinstance(m, dict):
            raise ValueError(f"{at}: must be an object")
        mid, label, ctx, thinking = m.get("id"), m.get("label") or m.get("id"), m.get("ctx"), m.get("thinking")
        if not isinstance(mid, str) or not _ID.fullmatch(mid):
            raise ValueError(f"{at}: id {mid!r} is not a model name")
        if not isinstance(label, str) or _CONTROL.search(label) or len(label) > 80:
            raise ValueError(f"{at}: label must be one line of text")
        if not isinstance(ctx, int) or isinstance(ctx, bool) or not 1024 <= ctx <= 1 << 20:
            raise ValueError(f"{at}: ctx must be a token count")
        if thinking not in THINKING:
            raise ValueError(f"{at}: thinking must be one of {', '.join(THINKING)}")
        out.append(ClientModel(mid, label, ctx, thinking))
    default = doc.get("default")
    return ModelList(out, default if isinstance(default, str) and default in [m.id for m in out] else None)


def load_list(path: str) -> ModelList:
    with open(path, encoding="utf-8") as f:
        return parse_list(json.load(f), path)


def from_server_ids(ids: List[str], ctx: int) -> ModelList:
    """The fallback without a list file: the ids /v1/models gave, as generic on / off entries."""
    ms = [ClientModel(i, i, ctx, "on-off") for i in ids if isinstance(i, str) and _ID.fullmatch(i)]
    return ModelList(ms, ms[0].id if ms else None)


def _ctx_for(m: ClientModel, running: Optional[str], running_ctx: Optional[int]) -> int:
    """The running model's window comes from the server itself; the others from their settings."""
    return running_ctx if running_ctx and m.id == running else m.ctx


def opencode_model(m: ClientModel, ctx: int) -> Dict[str, Any]:
    """One OpenCode model entry: its family's variants, the others disabled."""
    on = OC_ON[m.thinking]
    variants: Dict[str, Any] = {"none": dict(NO_THINK)}
    for lvl in OC_LEVELS:
        variants[lvl] = {"reasoningEffort": lvl} if lvl in on else {"disabled": True}
    return {
        "name": m.title(ctx),
        "reasoning": True, "tool_call": True, "temperature": True,
        "limit": {"context": ctx, "output": min(32000, ctx // 2)},
        "modalities": {"input": ["text"], "output": ["text"]},
        "options": {"reasoningEffort": DEFAULT_EFFORT[m.thinking]},
        "variants": variants,
    }


def opencode_models(ml: ModelList, running: Optional[str] = None, running_ctx: Optional[int] = None) -> Dict[str, Any]:
    return {m.id: opencode_model(m, _ctx_for(m, running, running_ctx)) for m in ml.models}


def pi_model(m: ClientModel, ctx: int) -> Dict[str, Any]:
    """One Pi model entry: its family's thinking levels (None = not offered)."""
    return {
        "id": m.id, "name": m.title(ctx), "reasoning": True,
        "thinkingLevelMap": dict(PI_LEVEL_MAP[m.thinking]),
        "input": ["text"], "contextWindow": ctx, "maxTokens": min(32768, ctx // 2),
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
    }


def pi_models(ml: ModelList, running: Optional[str] = None, running_ctx: Optional[int] = None) -> List[Dict[str, Any]]:
    return [pi_model(m, _ctx_for(m, running, running_ctx)) for m in ml.models]
