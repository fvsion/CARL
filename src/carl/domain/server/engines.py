"""The engines that can serve a model, and the pick of one (WCLI, decision A39). llama.cpp is the only engine today;
another engine (vLLM, an MLX engine) is a new adapter behind the EngineAdapter port and a new name here, with no new
command word. The pick: the environment variable CARL_ENGINE, else the setting engine.name, else the engine named
on the model's card (its field engine), else llama.cpp. Pure. Contract C-057.
"""
from __future__ import annotations

from typing import Tuple

from ..shared.errors import ConfigError

LLAMA_CPP = "llama.cpp"
ENGINES: Tuple[str, ...] = (LLAMA_CPP,)
DEFAULT = LLAMA_CPP
AUTO = "auto"                                    # the setting's default: the model's card decides


def pick(override: str, card: str) -> str:
    """The engine for a model. override: the environment variable or the setting ("" or auto: none); card: the
    engine on the model's card ("": none). ConfigError for a name that no adapter has."""
    for source, name in (("engine.name", override), ("the engine field of the model card", card)):
        if name and name != AUTO:
            if name not in ENGINES:
                raise ConfigError(f"{source}: {name!r} is not an engine of CARL ({', '.join(ENGINES)})")
            return name
    return DEFAULT
