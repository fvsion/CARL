"""Port of an engine that serves a model (WCLI, decision A39): what the start asks of it. llama.cpp is the only
adapter today; another engine is a new adapter and a new name in domain/server/engines.py."""
from __future__ import annotations

from typing import TYPE_CHECKING, FrozenSet, List, Protocol

if TYPE_CHECKING:
    from ..domain.server.launch_plan import LaunchPlan


class EngineAdapter(Protocol):
    """An engine that CARL starts for a model. Contract C-057."""

    def engine_name(self) -> str:
        """The engine's name, as the card and the setting engine.name say it (llama.cpp)."""
        ...

    def program(self) -> str:
        """The engine's server program: a path, or its name when the locator found none (the OS looks for it)."""
        ...

    def server_argv(self, plan: LaunchPlan) -> List[str]:
        """The arguments of the program for a single-model start (the program itself goes first, before these)."""
        ...

    def router_argv(self, plan: LaunchPlan, preset: str) -> List[str]:
        """The arguments of the program in router mode (preset: the presets file)."""
        ...

    def version_text(self) -> str:
        """What the program prints for its version ("" when it can't run)."""
        ...

    def update_hint(self) -> str:
        """The command that updates the engine on this computer."""
        ...

    def show_help(self) -> int:
        """Print CARL's head, then the engine's own help; the exit code (127 when the program can't run)."""
        ...

    def option_names(self) -> FrozenSet[str]:
        """The option names that the engine's own help lists ("-t", "--threads"); empty when it can't run or lists
        none (the start then refuses no option: WFIN)."""
        ...
