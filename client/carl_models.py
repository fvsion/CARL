"""The OpenCode and Pi model entries for the models installed on a CARL server.

One entry per installed (downloaded) model, never the whole catalogue: the id is the model's
CARL name, which is also the name the server serves it under, so OpenCode's /models and Pi's
/model list what this Mac really has. Each entry carries its family's thinking options:
  on-off   thinking on or off only (Qwen3.6 35B-A3B): variants none / high, default high
  effort   effort levels (Qwen3.8 27B): variants none / low / medium / xhigh, default low
The thinking of each role (Phase 23.4.4: the dashboard's Main thinking and Coder thinking, per
model): thinking_main is the entry's default (OpenCode's model option reasoningEffort, Pi's
modelThinkingLevels), thinking_coder is the coder's (OpenCode: carl-delegation's chat.params hook
sets reasoningEffort per request for the model the coder runs on; Pi: the subagent extension's
--thinking). Values: off, on, low, medium, xhigh (on / off only for on-off models); the coder also
"main": as the main session (no value of its own: the request stays as it is). A list without them
(an older server) gets the defaults of tools/carl_core/domain/thinking.py: main on (effort models:
low), coder "main".
/carl's Coder thinking (Phase 23.4.4) overrides the dashboard's value on one computer, per model:
CODER_THINKING=MODEL:VALUE,MODEL:VALUE in ~/.config/carl/client-install.env (carl-sync.py set writes it;
install.sh passes it to configure.py, which merges it over the list: coder_overrides(), which keeps the
dashboard's value in dash_coder for the /carl panel's "dashboard default"; "set MODEL:default" removes the entry).
Single-model mode (Phase 23.4.4 item 13): the list holds only the model the server runs (the
server Mac writes it so; only_running() also keeps only the running model of a longer list).
Router mode: every installed model.
The list comes from the server Mac (tools/carl.py client-models, written to
installed-models.json next to this file, so a client bundle copied into a VM carries it), or,
without it, from the server's /v1/models (ids only: generic on / off entries).

Standalone (stdlib only): it runs in the VM, from client/configure.py, and in the dashboard.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional

LIST_FILE = "installed-models.json"
SCHEMA = 1
THINKING = ("on-off", "effort")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
# Qwen's non-thinking sampling, sent with the "none" variant.
NO_THINK = {"reasoningEffort": "none", "temperature": 0.7, "top_p": 0.8, "presence_penalty": 1.5}
OC_LEVELS = ("minimal", "low", "medium", "high", "xhigh")
OC_ON = {"on-off": ("high",), "effort": ("low", "medium", "xhigh")}
PI_LEVEL_MAP: Dict[str, Dict[str, Optional[str]]] = {
    "on-off": {"minimal": None, "low": None, "medium": None, "xhigh": None},
    "effort": {"minimal": None, "high": None, "xhigh": "xhigh"},
}
# Thinking per role (the same rules as tools/carl_core/domain/thinking.py; this file runs without it)
MAIN = "main"                        # the coder's value "same as main"
ROLE_LEVELS = ("low", "medium", "xhigh")
ROLE_VALUES = {"main": ("on", "off", *ROLE_LEVELS), "coder": (MAIN, "on", "off", *ROLE_LEVELS)}
ROLE_DEFAULTS = {"effort": {"main": "low", "coder": MAIN}, "on-off": {"main": "on", "coder": MAIN}}
ON_LEVEL = {"main": "low", "coder": "medium"}    # "on" on a model with effort levels
# "on" as OpenCode's reasoningEffort on an on / off model (its "high" variant; CARL's template patch turns thinking
# off only for none / minimal / off, so any other value is on)
ON_EFFORT = "high"


@dataclass(frozen=True)
class ClientModel:
    """One installed model as the clients see it."""
    id: str
    label: str
    ctx: int
    thinking: str
    off_sampling: str = "qwen"   # "qwen": the none variant sends Qwen's non-thinking sampling; "same": it only
                                  # turns thinking off (Gemma 4: one sampling for every use)
    think_main: str = ""         # the main session's thinking: off, on or a level ("": the default of its kind)
    think_coder: str = ""        # the coder's
    dash_coder: Optional[str] = None   # the dashboard's coder value when /carl's override of this computer replaced it
    free_form: bool = False      # its main session may write the brief's free-form part (the catalogue's free_form_brief)

    def dashboard_coder(self) -> str:
        """The coder's thinking as the dashboard sets it (without this computer's override), as this model takes it."""
        return role_value(self.thinking, "coder", self.think_coder if self.dash_coder is None else self.dash_coder)

    def title(self, ctx: Optional[int] = None) -> str:
        return f"{self.label} — llama.cpp, {(ctx or self.ctx) // 1024}K"

    def role(self, role: str) -> str:
        """The thinking of a role (main, coder) as this model takes it: off, on, a level (effort models), or for
        the coder "main"."""
        return role_value(self.thinking, role, self.think_main if role == "main" else self.think_coder)

    def effort(self, role: str) -> Optional[str]:
        """The role's thinking as OpenCode's reasoningEffort: "none" (off: CARL's template patch turns thinking
        off), a level, or for "on" the value that turns it on. None: the coder as the main session (no value)."""
        v = self.role(role)
        return None if v == MAIN else "none" if v == "off" else ON_EFFORT if v == "on" else v

    def pi_level(self, role: str) -> Optional[str]:
        """The role's thinking as a Pi thinking level: off, high (on: the level an on-off model offers) or a
        level. None: the coder as the main session (the session's level)."""
        v = self.role(role)
        return None if v == MAIN else "high" if v == "on" else v


def role_value(thinking: str, role: str, value: str) -> str:
    """A role's value as a model of this kind takes it (tools/carl_core/domain/thinking.py normalize)."""
    kind = "effort" if thinking == "effort" else "on-off"
    if value not in ROLE_VALUES[role]:
        return ROLE_DEFAULTS[kind][role]
    if value == MAIN:
        return value
    if kind == "effort":
        return ON_LEVEL[role] if value == "on" else value
    return value if value == "off" else "on"


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
        off = m.get("off_sampling", "qwen")             # lists from before 1.7.0 have none: Qwen's, as before
        if off not in ("qwen", "same"):
            raise ValueError(f"{at}: off_sampling must be qwen or same")
        roles = []
        for role, key in (("main", "thinking_main"), ("coder", "thinking_coder")):   # lists from before 1.13.0
            v = m.get(key, "")                                                        # have none: the defaults
            if v != "" and v not in ROLE_VALUES[role]:
                raise ValueError(f"{at}: {key} must be one of {', '.join(ROLE_VALUES[role])}")
            roles.append(v)
        out.append(ClientModel(mid, label, ctx, thinking, off, roles[0], roles[1], free_form=m.get("free_form") is True))
    default = doc.get("default")
    return ModelList(out, default if isinstance(default, str) and default in [m.id for m in out] else None)


def load_list(path: str) -> ModelList:
    with open(path, encoding="utf-8") as f:
        return parse_list(json.load(f), path)


def only_running(ml: ModelList, running: Optional[str]) -> ModelList:
    """Single-model mode: only the model the server runs, when the list has it (install.sh passes no running model
    in router mode). Else the list as it is (the server Mac already wrote it for its mode)."""
    if not running or running not in ml.ids:
        return ml
    return ModelList([m for m in ml.models if m.id == running], running)


def coder_efforts(ml: ModelList, provider_id: str) -> Dict[str, str]:
    """carl-delegation's option coderThinking: {"provider/model": reasoningEffort} for each model whose Coder
    thinking has a value of its own (the chat.params hook sets it on the coder's requests); a model whose coder
    thinks as the main session has no entry."""
    out: Dict[str, str] = {}
    for m in ml.models:
        e = m.effort("coder")
        if e is not None:
            out[f"{provider_id}/{m.id}"] = e
    return out


def parse_overrides(text: str) -> Dict[str, str]:
    """CODER_THINKING ("MODEL:VALUE,MODEL:VALUE", /carl's choices on this computer) as {model: value}; an entry
    that is not a model name and a coder value is left out (the line is the user's file)."""
    out: Dict[str, str] = {}
    for part in (text or "").split(","):
        mid, sep, v = part.strip().partition(":")
        if sep and _ID.fullmatch(mid) and v in ROLE_VALUES["coder"]:
            out[mid] = v
    return out


def coder_overrides(ml: ModelList, over: Dict[str, str]) -> ModelList:
    """The list with /carl's Coder thinking of this computer over the dashboard's (only the models it has)."""
    if not over:
        return ml
    return ModelList([replace(m, think_coder=over[m.id], dash_coder=m.think_coder) if m.id in over else m
                      for m in ml.models], ml.default)


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
    variants: Dict[str, Any] = {"none": dict(NO_THINK) if m.off_sampling == "qwen" else {"reasoningEffort": "none"}}
    for lvl in OC_LEVELS:
        variants[lvl] = {"reasoningEffort": lvl} if lvl in on else {"disabled": True}
    return {
        "name": m.title(ctx),
        "reasoning": True, "tool_call": True, "temperature": True,
        "limit": {"context": ctx, "output": min(32000, ctx // 2)},
        "modalities": {"input": ["text"], "output": ["text"]},
        # parallel_tool_calls: llama.cpp lets the model call several tools in one turn only when the
        # request asks (measured 2026-10-03: 2 reads in one turn with it, 1 without); OpenCode
        # sends a model's options as they are
        # the main session's thinking (Main thinking in the dashboard); a variant chosen in OpenCode wins over it
        "options": {"reasoningEffort": m.effort("main") or ON_EFFORT, "parallel_tool_calls": True},
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
