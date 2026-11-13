"""The OpenCode and Pi model entries for the models installed on a CARL server (the model-entry type of the shared
kernel, decision 12). Pure: the thinking values come in as ModelRules (data/thinking.json).

One entry per installed (downloaded) model, never the whole catalogue: the id is the model's
CARL name, which is also the name the server serves it under, so OpenCode's /models and Pi's
/model list what the server really has. Each entry carries its family's thinking options:
  on-off   thinking on or off only: the off variant and the variant that turns it on
  effort   effort levels: the off variant and the levels
The thinking of each role (Phase 23.4.4: the dashboard's Main thinking and Coder thinking, per
model): thinking_main is the entry's default (OpenCode's model option reasoningEffort, Pi's
modelThinkingLevels), thinking_coder is the coder's (OpenCode: carl-delegation's chat.params hook
sets reasoningEffort per request for the model the coder runs on; Pi: the subagent extension's
--thinking). Values: off, on and the levels (on / off only for on-off models); the coder also
"main": as the main session (no value of its own: the request stays as it is). A list without them
(an older server) gets the defaults (carl_client.domain.thinking, the same rules as the server's).
/carl's Coder thinking (Phase 23.4.4) overrides the dashboard's value on one computer, per model:
CODER_THINKING=MODEL:VALUE,MODEL:VALUE in ~/.config/carl/client-install.env (carl-sync.py set writes it;
the setup passes it to the merge, which merges it over the list: coder_overrides(), which keeps the
dashboard's value in dash_coder for the /carl panel's "dashboard default"; "set MODEL:default" removes the entry).
Single-model mode (Phase 23.4.4 item 13): the list holds only the model the server runs (the
server writes it so; only_running() also keeps only the running model of a longer list).
Router mode: every installed model.
The list comes from the server (carl.py client-models, written to installed-models.json
next to carl_setup.py, so a client bundle copied into a VM carries it), or, without it, from the
server's /v1/models (ids only: generic on / off entries).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import thinking as th
from .thinking import MAIN, OFF, ON, Thinking

LIST_FILE = "installed-models.json"
SCHEMA = 1
THINKING = th.KINDS
QWEN_SAMPLING = "qwen"               # off_sampling of the lists from before 1.7.0 (they have none)
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


@dataclass(frozen=True)
class ModelRules:
    """The thinking rules and the values the clients' entries take (data/thinking.json)."""
    thinking: Thinking
    oc_levels: Tuple[str, ...]                    # OpenCode's reasoningEffort variants, in order
    oc_on: Mapping[str, Tuple[str, ...]]          # per kind: the variants that think (the others are disabled)
    on_effort: str                                # "on" as reasoningEffort on an on / off model (its variant that
    #                                               thinks; CARL's template patch turns thinking off only for none /
    #                                               minimal / off, so any other value is on)
    off_effort: str                               # off as reasoningEffort
    off_sampling: Mapping[str, Mapping[str, Any]]  # the none variant per off_sampling (qwen: Qwen's non-thinking
    #                                                sampling; same: thinking off only, one sampling for every use)
    pi_level_map: Mapping[str, Mapping[str, Optional[str]]]   # per kind: Pi's thinkingLevelMap (None: not offered)
    pi_on_level: str                              # "on" as a Pi thinking level
    pi_default_level: str                         # Pi's defaultThinkingLevel


def parse_rules(doc: Any) -> ModelRules:
    """thinking.json, checked; ValueError naming the problem."""
    if not isinstance(doc, dict):
        raise ValueError("thinking.json: not an object")
    rules = th.parse(doc.get("roles"))
    oc, pi = doc.get("opencode"), doc.get("pi")
    if not isinstance(oc, dict) or not isinstance(pi, dict):
        raise ValueError("thinking.json: opencode and pi must be objects")
    levels, on = oc.get("levels"), oc.get("on")
    if not (isinstance(levels, list) and all(isinstance(x, str) for x in levels)):
        raise ValueError("thinking.json: opencode.levels must be a list of names")
    if not (isinstance(on, dict) and set(on) == set(THINKING)
            and all(isinstance(v, list) and set(v) <= set(levels) for v in on.values())):
        raise ValueError("thinking.json: opencode.on must list the variants of each kind")
    off = oc.get("off_sampling")
    if not (isinstance(off, dict) and QWEN_SAMPLING in off and all(isinstance(v, dict) for v in off.values())):
        raise ValueError("thinking.json: opencode.off_sampling must map each sampling to the none variant")
    level_map = pi.get("level_map")
    if not (isinstance(level_map, dict) and set(level_map) == set(THINKING)
            and all(isinstance(v, dict) for v in level_map.values())):
        raise ValueError("thinking.json: pi.level_map must have a map for each kind")
    words = (oc.get("on_effort"), oc.get("off_effort"), pi.get("on_level"), pi.get("default_level"))
    if not all(isinstance(w, str) and w for w in words):
        raise ValueError("thinking.json: on_effort, off_effort, pi.on_level and pi.default_level are names")
    return ModelRules(rules, tuple(levels), {k: tuple(v) for k, v in on.items()}, str(words[0]), str(words[1]),
                      {k: dict(v) for k, v in off.items()}, {k: dict(v) for k, v in level_map.items()},
                      str(words[2]), str(words[3]))


@dataclass(frozen=True)
class ClientModel:
    """One installed model as the clients see it."""
    id: str
    label: str
    ctx: int
    thinking: str
    off_sampling: str = QWEN_SAMPLING   # "qwen": the none variant sends Qwen's non-thinking sampling; "same": it
                                        # only turns thinking off (Gemma 4: one sampling for every use)
    think_main: str = ""         # the main session's thinking: off, on or a level ("": the default of its kind)
    think_coder: str = ""        # the coder's
    dash_coder: Optional[str] = None   # the dashboard's coder value when /carl's override of this computer replaced it
    free_form: bool = False      # its main session may write the brief's free-form part (the catalogue's free_form_brief)

    def dashboard_coder(self, rules: ModelRules) -> str:
        """The coder's thinking as the dashboard sets it (without this computer's override), as this model takes it."""
        return rules.thinking.normalize(self.thinking, "coder",
                                        self.think_coder if self.dash_coder is None else self.dash_coder)

    def title(self, ctx: Optional[int] = None) -> str:
        return f"{self.label} — llama.cpp, {(ctx or self.ctx) // 1024}K"

    def role(self, role: str, rules: ModelRules) -> str:
        """The thinking of a role (main, coder) as this model takes it: off, on, a level (effort models), or for
        the coder "main"."""
        return rules.thinking.normalize(self.thinking, role, self.think_main if role == "main" else self.think_coder)

    def effort(self, role: str, rules: ModelRules) -> Optional[str]:
        """The role's thinking as OpenCode's reasoningEffort: off_effort (CARL's template patch turns thinking off),
        a level, or for "on" the value that turns it on. None: the coder as the main session (no value)."""
        v = self.role(role, rules)
        return None if v == MAIN else rules.off_effort if v == OFF else rules.on_effort if v == ON else v

    def pi_level(self, role: str, rules: ModelRules) -> Optional[str]:
        """The role's thinking as a Pi thinking level: off, the level an on-off model offers (on) or a level. None:
        the coder as the main session (the session's level)."""
        v = self.role(role, rules)
        return None if v == MAIN else rules.pi_on_level if v == ON else v


@dataclass(frozen=True)
class ModelList:
    """The installed models, the one to make the default, and the rules their entries follow."""
    models: List[ClientModel]
    default: Optional[str]
    rules: ModelRules

    @property
    def ids(self) -> List[str]:
        return [m.id for m in self.models]


def parse_list(doc: Any, rules: ModelRules, where: str = LIST_FILE) -> ModelList:
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
        off = m.get("off_sampling", QWEN_SAMPLING)       # lists from before 1.7.0 have none: Qwen's, as before
        if off not in rules.off_sampling:
            raise ValueError(f"{at}: off_sampling must be {' or '.join(rules.off_sampling)}")
        roles = []
        for role, key in (("main", "thinking_main"), ("coder", "thinking_coder")):   # lists from before 1.13.0
            v = m.get(key, "")                                                        # have none: the defaults
            if v != "" and v not in rules.thinking.values_of(role):
                raise ValueError(f"{at}: {key} must be one of {', '.join(rules.thinking.values_of(role))}")
            roles.append(v)
        out.append(ClientModel(mid, label, ctx, thinking, off, roles[0], roles[1], free_form=m.get("free_form") is True))
    default = doc.get("default")
    return ModelList(out, default if isinstance(default, str) and default in [m.id for m in out] else None, rules)


def only_running(ml: ModelList, running: Optional[str]) -> ModelList:
    """Single-model mode: only the model the server runs, when the list has it (the setup passes no running model
    in router mode). Else the list as it is (the server already wrote it for its mode)."""
    if not running or running not in ml.ids:
        return ml
    return ModelList([m for m in ml.models if m.id == running], running, ml.rules)


def coder_efforts(ml: ModelList, provider_id: str) -> Dict[str, str]:
    """carl-delegation's option coderThinking: {"provider/model": reasoningEffort} for each model whose Coder
    thinking has a value of its own (the chat.params hook sets it on the coder's requests); a model whose coder
    thinks as the main session has no entry."""
    out: Dict[str, str] = {}
    for m in ml.models:
        e = m.effort("coder", ml.rules)
        if e is not None:
            out[f"{provider_id}/{m.id}"] = e
    return out


def parse_overrides(text: str, rules: ModelRules) -> Dict[str, str]:
    """CODER_THINKING ("MODEL:VALUE,MODEL:VALUE", /carl's choices on this computer) as {model: value}; an entry
    that is not a model name and a coder value is left out (the line is the user's file)."""
    out: Dict[str, str] = {}
    for part in (text or "").split(","):
        mid, sep, v = part.strip().partition(":")
        if sep and _ID.fullmatch(mid) and v in rules.thinking.coder_values:
            out[mid] = v
    return out


def coder_overrides(ml: ModelList, over: Dict[str, str]) -> ModelList:
    """The list with /carl's Coder thinking of this computer over the dashboard's (only the models it has)."""
    if not over:
        return ml
    return ModelList([replace(m, think_coder=over[m.id], dash_coder=m.think_coder) if m.id in over else m
                      for m in ml.models], ml.default, ml.rules)


def from_server_ids(ids: List[str], ctx: int, rules: ModelRules) -> ModelList:
    """The fallback without a list file: the ids /v1/models gave, as generic on / off entries."""
    ms = [ClientModel(i, i, ctx, th.ON_OFF) for i in ids if isinstance(i, str) and _ID.fullmatch(i)]
    return ModelList(ms, ms[0].id if ms else None, rules)


def _ctx_for(m: ClientModel, running: Optional[str], running_ctx: Optional[int]) -> int:
    """The running model's window comes from the server itself; the others from their settings."""
    return running_ctx if running_ctx and m.id == running else m.ctx


def opencode_model(m: ClientModel, ctx: int, rules: ModelRules) -> Dict[str, Any]:
    """One OpenCode model entry: its family's variants, the others disabled."""
    on = rules.oc_on[m.thinking]
    off = rules.off_sampling.get(m.off_sampling)
    variants: Dict[str, Any] = {"none": dict(off) if off is not None else {"reasoningEffort": rules.off_effort}}
    for lvl in rules.oc_levels:
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
        "options": {"reasoningEffort": m.effort("main", rules) or rules.on_effort, "parallel_tool_calls": True},
        "variants": variants,
    }


def opencode_models(ml: ModelList, running: Optional[str] = None, running_ctx: Optional[int] = None) -> Dict[str, Any]:
    return {m.id: opencode_model(m, _ctx_for(m, running, running_ctx), ml.rules) for m in ml.models}


def pi_model(m: ClientModel, ctx: int, rules: ModelRules) -> Dict[str, Any]:
    """One Pi model entry: its family's thinking levels (None = not offered)."""
    return {
        "id": m.id, "name": m.title(ctx), "reasoning": True,
        "thinkingLevelMap": dict(rules.pi_level_map[m.thinking]),
        "input": ["text"], "contextWindow": ctx, "maxTokens": min(32768, ctx // 2),
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
    }


def pi_models(ml: ModelList, running: Optional[str] = None, running_ctx: Optional[int] = None) -> List[Dict[str, Any]]:
    return [pi_model(m, _ctx_for(m, running, running_ctx), ml.rules) for m in ml.models]
