"""Thinking per role (Phase 23.4.4 item 11): the main session and the coder each have their own thinking setting
per model (config.json models.<name>.thinking_main and models.<name>.thinking_coder). Pure.

The values: off, on, or an effort level (low, medium, xhigh: the levels of Qwen3.8's template; it has no "high").
The coder also takes "main" (MAIN, the dashboard shows it as "same as main"): the coder thinks as the main session
does with the model it runs on. A model whose catalogue entry (or card) says thinking "effort" takes off or a level;
"on" there means the role's own level (ON_LEVEL). A model with thinking "on-off" takes on or off; a level there means
on.

The defaults (user, 2026-10-09: "default should be the same thinking mode as your main session"):
- the main session: what CARL did before the setting existed: on; the effort models at low (OpenCode's model option
  reasoningEffort, Pi's defaultThinkingLevel);
- the coder: "main". "on" for the coder on an effort model is medium (the best of 9 coder runs on the 35B,
  2026-10-02; the user: "coders do better with thinking on").

The clients apply the coder's value per request, for the model the coder runs on (OpenCode: carl-delegation's
chat.params hook; Pi: the subagent extension's --thinking). "main" leaves the request as the main session's.

client/carl_models.py keeps the same rules for model lists from an older server (it is stdlib only and runs on
other computers, so it cannot import this module; tests/test_router.py checks that the two agree).
"""
from __future__ import annotations

from typing import Dict, Mapping, Optional, Tuple

ROLES = ("main", "coder")
KEYS = {"main": "thinking_main", "coder": "thinking_coder"}     # config.json models.<name>.<key>
MAIN = "main"                                                   # the coder's value "same as main"
LEVELS = ("low", "medium", "xhigh")
MAIN_VALUES = ("on", "off", *LEVELS)                            # thinking_main
CODER_VALUES = (MAIN, *MAIN_VALUES)                             # thinking_coder
VALUES_OF = {"main": MAIN_VALUES, "coder": CODER_VALUES}
DEFAULTS: Dict[str, Dict[str, str]] = {"effort": {"main": "low", "coder": MAIN},
                                       "on-off": {"main": "on", "coder": MAIN}}
ON_LEVEL = {"main": "low", "coder": "medium"}                   # "on" on a model with effort levels


def kind_of(thinking: object) -> str:
    """The model's thinking kind: "effort" (levels) or "on-off" (also for a model that does not say)."""
    return "effort" if thinking == "effort" else "on-off"


def choices(thinking: object, role: str = "main") -> Tuple[str, ...]:
    """The values the dashboard offers for a model and a role (its Agents panel, in this order): off and the levels, or
    off and on; the coder first has "main"."""
    own = ("off", *LEVELS) if kind_of(thinking) == "effort" else ("off", "on")
    return (MAIN, *own) if role == "coder" else own


def default(thinking: object, role: str) -> str:
    """The role's default for a model of this kind."""
    return DEFAULTS[kind_of(thinking)][role]


def normalize(thinking: object, role: str, value: object) -> str:
    """A value as this model takes it: "on" on an effort model is the role's level; a level on an on / off model is
    "on"; "main" stays (the coder only); an unknown value is the default."""
    v = str(value)
    if v not in VALUES_OF[role]:
        return default(thinking, role)
    if v == MAIN:
        return v
    if kind_of(thinking) == "effort":
        return ON_LEVEL[role] if v == "on" else v
    return v if v == "off" else "on"


def defaults(thinking: object) -> Dict[str, str]:
    """{thinking_main: ..., thinking_coder: ...}: the defaults of a model of this kind (the catalogue layer of its
    settings)."""
    return {KEYS[r]: default(thinking, r) for r in ROLES}


def values(thinking: object, settings: Optional[Mapping[str, object]] = None) -> Dict[str, str]:
    """Both roles' values for a model, normalized: from its settings when they have them, else the defaults."""
    s = settings or {}
    return {KEYS[r]: normalize(thinking, r, s.get(KEYS[r], default(thinking, r))) for r in ROLES}


def coder_thinks(thinking: object, settings: Optional[Mapping[str, object]] = None) -> str:
    """How the coder thinks with this model: its own value, or the main session's when it is "main"."""
    v = values(thinking, settings)
    return v[KEYS["main"]] if v[KEYS["coder"]] == MAIN else v[KEYS["coder"]]
