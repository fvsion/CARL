"""Ports of the network: the listening ports and the computer's addresses."""
from __future__ import annotations

from typing import List, Optional, Protocol, Set


class PortProbe(Protocol):
    """Which TCP ports are in use, and by whom. Contract C-017."""

    def listening(self) -> Set[int]:
        """The TCP ports in LISTEN state ({} when the tool can't run)."""
        ...

    def owner(self, port: int) -> Optional[int]:
        """The pid listening on the port (None when the port is free or the owner can't be told)."""
        ...

    def free(self, host: str, port: int) -> bool:
        """True when CARL can bind the port on host."""
        ...


class NetworkInterfaces(Protocol):
    """The computer's own addresses (the --vm, --local and --host choices). Contract C-016."""

    def vm_address(self) -> Optional[str]:
        """The VM network's host address when it is up; None when there is none on this computer."""
        ...

    def local_addresses(self) -> List[str]:
        """The computer's IPv4 addresses, loopback first."""
        ...
