"""Design by contract (decision A17): the checks at the boundaries of the ports and the application services.

Each check names its contract (C-NNN) in CARL's list of contracts, and the contract names the file that checks it.
A check catches a programming error only: a caller that breaks a precondition (require), a service or an adapter
that breaks its postcondition (ensure), or a state that must hold before and after (invariant). It never checks
what a user typed or a file held: that keeps its own messages and exit codes (ConfigError), so the outputs stay
the same. A failed check raises ContractError. Pure (R1).
"""
from __future__ import annotations

import re

from .errors import ContractError

CONTRACT_ID = re.compile(r"^C-\d{3}$")


def _check(ok: object, contract_id: str, kind: str, message: str) -> None:
    if not CONTRACT_ID.match(contract_id):
        raise ContractError(contract_id, f"not a contract ID ({kind}: {message})")
    if not ok:
        raise ContractError(contract_id, f"{kind} broken: {message}")


def require(ok: object, contract_id: str, message: str) -> None:
    """A precondition: what the caller must give. message: what must hold, in words."""
    _check(ok, contract_id, "precondition", message)


def ensure(ok: object, contract_id: str, message: str) -> None:
    """A postcondition: what the service or the adapter gives back."""
    _check(ok, contract_id, "postcondition", message)


def invariant(ok: object, contract_id: str, message: str) -> None:
    """What holds before and after every call."""
    _check(ok, contract_id, "invariant", message)
