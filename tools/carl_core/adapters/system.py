"""This Mac: sysctl, the GPU memory limit, listening ports, processes, the VM network,
the llama-server version and the date. Every command runs from an argument list."""
from __future__ import annotations

import os
import subprocess
import time
from typing import List, Optional, Set, Tuple

from ..domain.fit import estimated_limit
from ..domain.tuning import ProcessInfo

NETSTAT = "/usr/sbin/netstat"
CMD_TIMEOUT = 10
SWIFT_TIMEOUT = 120
VM_ADDR = "192.168.42.1"                    # the VMware network's host address (host/common.sh)


def run_text(args: List[str], timeout: float = CMD_TIMEOUT) -> str:
    """stdout of a command, "" when it is missing, fails to start or times out."""
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def sysctl_int(name: str) -> int:
    try:
        return int(run_text(["sysctl", "-n", name]).strip() or 0)
    except ValueError:
        return 0


def sysctl_text(name: str) -> str:
    return run_text(["sysctl", "-n", name]).strip()


class MacGpuLimit:
    """What the GPU may use (implements GpuLimit). An iogpu.wired_limit_mb override wins;
    otherwise Metal's recommendedMaxWorkingSetSize (a Swift probe, cached per RAM size since
    it takes seconds), else an estimate. Measured once per process."""

    def __init__(self, probe: str, cache_file: str) -> None:
        self.probe = probe
        self.cache_file = cache_file
        self._value: Optional[Tuple[int, str]] = None

    def limit(self) -> Tuple[int, str]:
        if self._value is None:
            self._value = self._measure()
        return self._value

    def _cached(self, mem: int) -> Optional[int]:
        try:
            with open(self.cache_file, encoding="utf-8") as f:
                cmem, lim = f.read().split()
            return int(lim) if int(cmem) == mem else None
        except (OSError, ValueError):
            return None

    def _measure(self) -> Tuple[int, str]:
        mem = sysctl_int("hw.memsize")
        wired = sysctl_int("iogpu.wired_limit_mb")
        if wired:
            return wired * 2 ** 20, f"iogpu.wired_limit_mb={wired}"
        cached = self._cached(mem)
        if cached:
            return cached, "Metal recommendedMaxWorkingSetSize"
        try:
            lim = int(run_text(["swift", self.probe], SWIFT_TIMEOUT).strip())
        except ValueError:
            limit, frac = estimated_limit(mem)
            return limit, f"estimate ({frac:.0%} of RAM; Swift unavailable)"
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            with open(self.cache_file, "w", encoding="utf-8") as f:
                f.write(f"{mem} {lim}\n")
        except OSError:
            pass                              # only a cache: the next run probes again
        return lim, "Metal recommendedMaxWorkingSetSize"


def parse_listening_ports(netstat_output: str) -> Set[int]:
    """TCP ports in LISTEN state from `netstat -anv -p tcp` (local address like *.8080)."""
    ports: Set[int] = set()
    for line in netstat_output.splitlines():
        f = line.split()
        if len(f) > 5 and f[5] == "LISTEN":
            port = f[3].rsplit(".", 1)[-1]
            if port.isdigit():
                ports.add(int(port))
    return ports


def listening_ports() -> Set[int]:
    return parse_listening_ports(run_text([NETSTAT, "-anv", "-p", "tcp"]))


def parse_processes(ps_output: str) -> List[ProcessInfo]:
    """`ps -axo pid=,rss=,comm=` lines (rss in KiB)."""
    out: List[ProcessInfo] = []
    for line in ps_output.splitlines():
        f = line.split(None, 2)
        if len(f) == 3 and f[0].isdigit() and f[1].isdigit():
            out.append(ProcessInfo(int(f[0]), int(f[1]), f[2]))
    return out


def processes() -> List[ProcessInfo]:
    return parse_processes(run_text(["ps", "-axo", "pid=,rss=,comm="]))


def vm_network_up(addr: str = VM_ADDR) -> bool:
    """True while VMware's host-only network (this Mac's VM address) is configured."""
    return f"inet {addr} " in run_text(["ifconfig"])


def llama_server_version() -> str:
    try:
        r = subprocess.run(["llama-server", "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return "?"
    lines = [x.strip() for x in (r.stdout + r.stderr).splitlines() if x.startswith("version:")]
    return lines[0] if lines else "?"


class MacHost:
    """This Mac's RAM and VM network (implements HostMemory)."""

    def ram_bytes(self) -> int:
        return sysctl_int("hw.memsize")

    def vm_network_up(self) -> bool:
        return vm_network_up()


class SystemClock:
    def today(self) -> str:
        return time.strftime("%Y-%m-%d")
