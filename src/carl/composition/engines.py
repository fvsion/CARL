"""The engines of this computer (WCLI, decision A39): the EngineAdapter of each engine name, and the pick of the
engine for a model.

llama.cpp is the only engine: LlamaCppEngine over this computer's LlamaCpp (llama.py). Another engine is another
adapter in `adapters()` and another name in domain/server/engines.py. Nothing is built at import (rule R6).
"""
from __future__ import annotations

from typing import Dict, Mapping

from ..domain.config.settings import ENGINE_KEYS
from ..domain.server.engines import LLAMA_CPP, pick
from ..domain.shared.errors import ConfigError
from ..ports import EngineAdapter
from . import llama as llama_wiring
from .profiles import Machine, Profile


def adapters(profile: Profile, machine: Machine) -> Dict[str, EngineAdapter]:
    """Every engine of this computer, by name."""
    from ..adapters.llama.engine import LlamaCppEngine
    return {LLAMA_CPP: LlamaCppEngine(llama_wiring.llama_cpp(profile, machine), llama_wiring.server_program(machine))}


def engine_name(env: Mapping[str, str], setting: object, card: str = "") -> str:
    """The engine for a model: CARL_ENGINE, else the setting engine.name (setting: its value, None when not set), else
    the engine on the model's card ("": none), else llama.cpp. ConfigError for a name that no adapter has."""
    override = env.get(str(ENGINE_KEYS["name"].env)) or (setting if isinstance(setting, str) else "")
    return pick(override, card)


def default_adapter(profile: Profile, machine: Machine, env: Mapping[str, str], setting: object) -> EngineAdapter:
    """The engine of the commands that name no model (engine version, engine help): the override, else llama.cpp."""
    name = engine_name(env, setting)
    found = adapters(profile, machine).get(name)
    if found is None:
        raise ConfigError(f"no adapter for the engine {name}")
    return found
