"""Thinking per role (Phase 23.4.4 item 11; the shared kernel, decision 12): the main session and the coder each have
their own thinking setting per model (config.json models.<name>.thinking_main and models.<name>.thinking_coder).
Pure. The server (src/carl/domain/catalog/thinking.py) and the clients (carl_client.domain.models) use these rules;
the values come in as a Thinking (client/carl_client/data/thinking.json, read by an adapter).

The values: off, on, or an effort level (the levels of a model's template). The coder also takes "main" (MAIN, the
dashboard shows it as "same as main"): the coder thinks as the main session does with the model it runs on. A model
whose catalogue entry (or card) says thinking "effort" takes off or a level; "on" there means the role's own level
(on_level). A model with thinking "on-off" takes on or off; a level there means on.

The defaults (user, 2026-10-09: "default should be the same thinking mode as your main session"):
- the main session: what CARL did before the setting existed: on; the effort models at their lowest level (OpenCode's
  model option reasoningEffort, Pi's defaultThinkingLevel);
- the coder: "main". "on" for the coder on an effort model is its own level (the best of 9 coder runs on the 35B,
  2026-10-02; the user: "coders do better with thinking on").

The clients apply the coder's value per request, for the model the coder runs on (OpenCode: carl-delegation's
chat.params hook; Pi: the subagent extension's --thinking). "main" leaves the request as the main session's.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

ROLES = ("main", "coder")
KEYS = {"main": "thinking_main", "coder": "thinking_coder"}     # config.json models.<name>.<key>
MAIN = "main"                                                   # the coder's value "same as main"
ON, OFF = "on", "off"
EFFORT, ON_OFF = "effort", "on-off"                             # the kinds (the catalogue's "thinking")
KINDS = (ON_OFF, EFFORT)


def kind_of(thinking: object) -> str:
    """The model's thinking kind: "effort" (levels) or "on-off" (also for a model that does not say)."""
    return EFFORT if thinking == EFFORT else ON_OFF


@dataclass(frozen=True)
class Thinking:
    """The rules over their values: the levels of an effort model, each role's default per kind, and the level that
    "on" means on an effort model, per role."""
    levels: Tuple[str, ...]
    defaults: Mapping[str, Mapping[str, str]]
    on_level: Mapping[str, str]

    def values_of(self, role: str) -> Tuple[str, ...]:
        """Every value a role takes (config.json, the dashboard, /carl), in this order."""
        own = (ON, OFF, *self.levels)
        return (MAIN, *own) if role == "coder" else own

    @property
    def main_values(self) -> Tuple[str, ...]:
        return self.values_of("main")

    @property
    def coder_values(self) -> Tuple[str, ...]:
        return self.values_of("coder")

    def choices(self, thinking: object, role: str = "main") -> Tuple[str, ...]:
        """The values the dashboard offers for a model and a role (its Agents panel, in this order): off and the
        levels, or off and on; the coder first has "main"."""
        own = (OFF, *self.levels) if kind_of(thinking) == EFFORT else (OFF, ON)
        return (MAIN, *own) if role == "coder" else own

    def default(self, thinking: object, role: str) -> str:
        """The role's default for a model of this kind."""
        return self.defaults[kind_of(thinking)][role]

    def normalize(self, thinking: object, role: str, value: object) -> str:
        """A value as this model takes it: "on" on an effort model is the role's level; a level on an on / off model
        is "on"; "main" stays (the coder only); an unknown value is the default."""
        v = str(value)
        if v not in self.values_of(role):
            return self.default(thinking, role)
        if v == MAIN:
            return v
        if kind_of(thinking) == EFFORT:
            return self.on_level[role] if v == ON else v
        return v if v == OFF else ON

    def role_defaults(self, thinking: object) -> Dict[str, str]:
        """{thinking_main: ..., thinking_coder: ...}: the defaults of a model of this kind (the catalogue layer of
        its settings)."""
        return {KEYS[r]: self.default(thinking, r) for r in ROLES}

    def values(self, thinking: object, settings: Optional[Mapping[str, object]] = None) -> Dict[str, str]:
        """Both roles' values for a model, normalized: from its settings when they have them, else the defaults."""
        s = settings or {}
        return {KEYS[r]: self.normalize(thinking, r, s.get(KEYS[r], self.default(thinking, r))) for r in ROLES}

    def coder_thinks(self, thinking: object, settings: Optional[Mapping[str, object]] = None) -> str:
        """How the coder thinks with this model: its own value, or the main session's when it is "main"."""
        v = self.values(thinking, settings)
        return v[KEYS["main"]] if v[KEYS["coder"]] == MAIN else v[KEYS["coder"]]


def parse(doc: Any) -> Thinking:
    """The "roles" part of thinking.json, checked; ValueError naming the problem."""
    if not isinstance(doc, dict):
        raise ValueError("thinking: roles must be an object")
    levels, defaults, on_level = doc.get("levels"), doc.get("defaults"), doc.get("on_level")
    if not (isinstance(levels, list) and levels and all(isinstance(x, str) and x for x in levels)):
        raise ValueError("thinking: levels must be a list of names")
    if len(set(levels) | {MAIN, ON, OFF}) != len(levels) + 3:
        raise ValueError("thinking: a level repeats, or is main, on or off")
    if not isinstance(defaults, dict) or set(defaults) != set(KINDS):
        raise ValueError(f"thinking: defaults must have {' and '.join(KINDS)}")
    if not isinstance(on_level, dict) or set(on_level) != set(ROLES) or not set(on_level.values()) <= set(levels):
        raise ValueError("thinking: on_level must give a level for each role")
    rules = Thinking(tuple(levels), {k: dict(v) for k, v in defaults.items() if isinstance(v, dict)},
                     dict(on_level))
    for kind in KINDS:
        d = rules.defaults.get(kind, {})
        if set(d) != set(ROLES) or any(d[r] not in rules.values_of(r) or rules.normalize(kind, r, d[r]) != d[r]
                                       for r in ROLES):
            raise ValueError(f"thinking: the defaults of {kind} must be a value of each role for that kind")
    return rules
