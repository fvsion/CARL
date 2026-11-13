"""This Mac's own addresses from ifconfig (implements NetworkInterfaces, contract C-016): the --vm, --local and --host
choices of a start. ifconfig by its name, as host/common.sh has_addr called it; read again at each question, so an
interface that comes up during a start counts.
"""
from __future__ import annotations

import re
import subprocess
from typing import List, Optional

from ...domain.shared.constants import VM_ADDR

ARGS = ["ifconfig"]
TIMEOUT = 10
LOOPBACK = "127.0.0.1"
_INET = re.compile(r"\binet (\d+\.\d+\.\d+\.\d+) ")


def parse_addresses(text: str) -> List[str]:
    """The IPv4 addresses of ifconfig's output, in its order (each "inet ADDR " once)."""
    return list(dict.fromkeys(_INET.findall(text)))


class Ifconfig:
    """The addresses of this Mac. vm_host: the VM network's host address (VM_HOST, VMware Fusion's vmnet8)."""

    def __init__(self, vm_host: str = VM_ADDR) -> None:
        self.vm_host = vm_host

    def _addresses(self) -> List[str]:
        try:
            out = subprocess.run(ARGS, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                 text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        return parse_addresses(out)

    def vm_address(self) -> Optional[str]:
        return self.vm_host if self.vm_host in self._addresses() else None

    def local_addresses(self) -> List[str]:
        found = self._addresses()
        return [a for a in found if a == LOOPBACK] + [a for a in found if a != LOOPBACK]
