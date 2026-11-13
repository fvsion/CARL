"""The computer's own addresses from the socket library (implements NetworkInterfaces, contract C-016): one adapter
for Windows and Linux (decision A31; the windows profile wires it, W6.11 the linux one). There is no VM network
off the Mac (no --vm). The addresses: the loopback, the host name's IPv4 addresses (getaddrinfo), and the address
a UDP socket would leave from towards the local network (connect sends nothing); an address counts only when CARL
can bind it, so --host takes only this computer's own addresses. Read again at each question.
"""
from __future__ import annotations

import socket
from typing import Callable, Iterable, List, Optional

LOOPBACK = "127.0.0.1"
PROBE = ("192.0.2.1", 9)                  # TEST-NET-1 (RFC 5737): a connect picks the route, and no packet is sent


def name_addresses(host_name: str) -> List[str]:
    """The IPv4 addresses the host name resolves to ([] when it does not)."""
    try:
        infos = socket.getaddrinfo(host_name, None, socket.AF_INET, socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return []
    return [str(i[4][0]) for i in infos]


def route_address() -> Optional[str]:
    """The address this computer would send from towards the local network (None without one)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(PROBE)
            return str(s.getsockname()[0])
    except OSError:
        return None


def can_bind(addr: str) -> bool:
    """CARL can bind a TCP socket to the address (a free port of the system's choosing; closed at once)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((addr, 0))
            return True
    except OSError:
        return False


def ordered(found: Iterable[str]) -> List[str]:
    """Each IPv4 address once, the loopback first, then the others in their order (no wildcard)."""
    seen = [a for a in dict.fromkeys(found) if a and a != "0.0.0.0"]
    return [LOOPBACK] + [a for a in seen if a != LOOPBACK]


class SocketAddresses:
    """host_name, route, bind: the socket calls (the tests give their own)."""

    def __init__(self, host_name: Callable[[], str] = socket.gethostname,
                 route: Callable[[], Optional[str]] = route_address, bind: Callable[[str], bool] = can_bind) -> None:
        self.host_name, self.route, self.bind = host_name, route, bind

    def vm_address(self) -> Optional[str]:
        return None

    def local_addresses(self) -> List[str]:
        try:
            name = self.host_name()
        except OSError:
            name = ""
        found = (name_addresses(name) if name else []) + [self.route() or ""]
        return [a for a in ordered(found) if a == LOOPBACK or self.bind(a)]
