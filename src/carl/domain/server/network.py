"""Where a start listens: the network mode, an address of this computer, never a wildcard (host/common.sh
resolve_host). Pure: the caller says which addresses this computer has (has_addr), so the rule runs the same in a
test and on every OS.

    --local, NET=local (the default)   127.0.0.1: this computer only
    --vm, NET=vm                       VM_HOST (192.168.42.1): the VM network on a Mac; its interface must exist
    --host ADDR, HOST=ADDR             one address of this computer; it wins over the mode
    NET=auto                           removed in 1.3.0: local, with a note

Contract C-119.
"""
from __future__ import annotations

from typing import Callable, Tuple

from ..config.settings import LLAMA_KEYS, NET_CHOICES
from ..shared.platform_words import PlatformWords

LOCAL_NAMES = ("127.0.0.1", "localhost", "::1")
WILDCARDS = ("0.0.0.0", "0", "::", "[::]", "*")
LOOPBACK = LOCAL_NAMES[0]
LOCAL_MODE, VM_MODE = NET_CHOICES
RETIRED_MODE = "auto"                     # NET=auto before 1.3.0: the VM address whenever it existed


class NetworkRefused(Exception):
    """A start CARL does not make: the whole message (starting "error: ") and the exit code."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


def resolve_host(mode: str, host: str, vm_host: str, has_addr: Callable[[str], bool], words: PlatformWords,
                 addr_tool: str = "ifconfig") -> Tuple[str, str]:
    """(the address, the note of the start lines) for a network mode ("" is local) and an address ("" none: the
    mode decides). has_addr: this computer has the address (the caller asks the OS, as ifconfig does on a Mac);
    addr_tool: the program that lists them, named in the message. Raises NetworkRefused. A computer without a VM
    network (words.vm is "") has no --vm."""
    mode = mode or LLAMA_KEYS["net"].default
    retired = ""
    if mode == RETIRED_MODE:
        mode, retired = LOCAL_MODE, f" {LLAMA_KEYS['net'].env}=auto was removed in 1.3.0: use --vm for a VM."
    if host:
        if host in LOCAL_NAMES:
            note = f"Only {words.machine} can use the server."
        elif host == vm_host and words.vm:
            note = f"The VM and {words.machine} can use the server."
        else:
            note = "CAUTION: every computer that can reach this address can use the server. Only the API key protects it."
    elif mode == VM_MODE:
        if not words.vm:
            raise NetworkRefused(f"error: --vm: {words.machine} has no VM network. Use --host with one address of "
                                 f"{words.machine}, or --local.", 2)
        if not has_addr(vm_host):
            raise NetworkRefused(f"error: --vm: no network interface has the address {vm_host}. Start {words.vm} "
                                 f"Fusion first, or use --local to serve only {words.machine}.", 1)
        host, note = vm_host, f"The VM network: the VM and {words.machine} can use the server."
    elif mode == LOCAL_MODE:
        host, note = LOOPBACK, f"Only {words.machine} can use the server.{retired}"
        if not retired and words.vm and has_addr(vm_host):
            note += f" The {words.vm} network is up: use --vm to serve a VM too."
    else:
        raise NetworkRefused(f"error: unknown network mode '{mode}'. Use {' or '.join(reversed(NET_CHOICES))}.", 2)
    if host in WILDCARDS:
        modes = "--local, --vm or --host" if words.vm else "--local or --host"
        raise NetworkRefused(f"error: CARL is refusing to listen on {host}: every computer on the LAN could use the "
                             f"server. Use {modes} with one address of {words.machine}.", 1)
    if host not in LOCAL_NAMES and not has_addr(host):
        raise NetworkRefused(f"error: no network interface of {words.machine} has the address {host}. {addr_tool} "
                             "shows the addresses. Or use --local.", 1)
    return host, note

