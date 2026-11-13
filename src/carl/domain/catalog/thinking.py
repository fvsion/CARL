"""Thinking per role (Phase 23.4.4 item 11): the main session and the coder each have their own thinking setting per
model (config.json models.<name>.thinking_main and models.<name>.thinking_coder). Pure.

The rules are the shared kernel's (decision 12: carl_client.domain.thinking, which the clients use too); this module
gives them to the server under their names here. The values: off, on, or an effort level (the levels of a model's
template); the coder also takes "main". The defaults: the main session on (the effort models at their lowest level),
the coder "main"; "on" on an effort model is a level of the role's own.

The values' home is client/carl_client/data/thinking.json (rule R18), which the clients read. The server gets the same
rules from the composition: it reads them through the bridge to the client package and installs them once, at the
start of a process (install). Until then a function here raises; the lists the settings schema holds (MAIN_VALUES,
CODER_VALUES) are read when they are first asked for, not at import.
"""
from __future__ import annotations

from typing import Callable, Dict, Iterator, Mapping, Optional, Sequence, Tuple, overload

from carl_client.domain.thinking import KEYS, MAIN, ROLES, Thinking, kind_of  # noqa: F401

_installed: Optional[Thinking] = None


def install(rules: Thinking) -> None:
    """The rules of this process (the composition gives them; the same ones replace them, e.g. in a test)."""
    global _installed
    _installed = rules


def rules() -> Thinking:
    """The installed rules; RuntimeError before the composition installs them."""
    if _installed is None:
        raise RuntimeError("the thinking rules are not installed (the composition gives them: thinking.install)")
    return _installed


class Deferred(Sequence[str]):
    """A list of names that is read when it is first asked for: the settings schema holds it at import, the
    values come from the installed rules."""

    def __init__(self, read: Callable[[], Tuple[str, ...]]) -> None:
        self._read = read

    @overload
    def __getitem__(self, i: int) -> str: ...

    @overload
    def __getitem__(self, i: slice) -> Tuple[str, ...]: ...

    def __getitem__(self, i: "int | slice") -> "str | Tuple[str, ...]":
        return self._read()[i]

    def __len__(self) -> int:
        return len(self._read())

    def __iter__(self) -> Iterator[str]:
        return iter(self._read())

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Sequence) and not isinstance(other, str) and tuple(self) == tuple(other)

    def __hash__(self) -> int:
        return hash(tuple(self))

    def __repr__(self) -> str:
        return repr(self._read())


MAIN_VALUES: Sequence[str] = Deferred(lambda: rules().main_values)        # thinking_main
CODER_VALUES: Sequence[str] = Deferred(lambda: rules().coder_values)      # thinking_coder


def values_of(role: str) -> Tuple[str, ...]:
    """Every value a role takes (config.json, the dashboard, /carl), in this order."""
    return rules().values_of(role)


def choices(thinking: object, role: str = "main") -> Tuple[str, ...]:
    return rules().choices(thinking, role)


def default(thinking: object, role: str) -> str:
    return rules().default(thinking, role)


def normalize(thinking: object, role: str, value: object) -> str:
    return rules().normalize(thinking, role, value)


def defaults(thinking: object) -> Dict[str, str]:
    return rules().role_defaults(thinking)


def values(thinking: object, settings: Optional[Mapping[str, object]] = None) -> Dict[str, str]:
    return rules().values(thinking, settings)


def coder_thinks(thinking: object, settings: Optional[Mapping[str, object]] = None) -> str:
    return rules().coder_thinks(thinking, settings)
