"""This Mac: sysctl, the GPU memory limit, the RAM and the processor (HostInfo), listening ports, processes, the VM
network, the llama-server version and the date. Every command runs from an argument list, and its output is read as
UTF-8 (rule R15). The netstat and ps parsers: bsd_netstat.py and ps.py."""
from __future__ import annotations

import os
import subprocess
import time
from typing import List, Optional, Set, Tuple

from ...domain.memory.fit import estimated_limit
from ...domain.memory.split import Slot
from ...domain.shared.constants import VM_ADDR
from ...domain.hardware.processes import ProcessInfo
from .bsd_netstat import ARGS as NETSTAT_ARGS, NETSTAT, parse_listening_ports, run_limited, tcp_rows  # noqa: F401  (names here too)
from .ps import LIST_ARGS as PS_LIST_ARGS, parse_processes

CMD_TIMEOUT = 10
SWIFT_TIMEOUT = 120


def run_text(args: List[str], timeout: float = CMD_TIMEOUT) -> str:
    """stdout of a command, "" when it is missing, fails to start or times out."""
    try:
        return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout).stdout
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
    it takes seconds), else an estimate. Measured once per process. unified: the GPU and the CPU share the RAM."""

    unified = True
    dedicated = 0                       # all of the limit is the RAM's
    shared_gpus: Tuple[str, ...] = ()

    def __init__(self, probe: str, cache_file: str) -> None:
        self.probe = probe
        self.cache_file = cache_file
        self._value: Optional[Tuple[int, str]] = None

    def limit(self) -> Tuple[int, str]:
        if self._value is None:
            self._value = self._measure()
        return self._value

    def slots(self) -> List[Slot]:
        """No devices to name: Metal is the one device, and llama.cpp picks it (no -dev)."""
        return []

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
            with open(self.cache_file, "w", encoding="utf-8", newline="\n") as f:
                f.write(f"{mem} {lim}\n")
        except OSError:
            pass                              # only a cache: the next run probes again
        return lim, "Metal recommendedMaxWorkingSetSize"


def listening_ports() -> Set[int]:
    return {r.local_port for r in tcp_rows(lambda argv: run_limited(argv, CMD_TIMEOUT)) if r.state == "LISTEN"}


def processes() -> List[ProcessInfo]:
    return parse_processes(run_text(PS_LIST_ARGS))


def vm_network_up(addr: str = VM_ADDR) -> bool:
    """True while VMware's host-only network (this Mac's VM address) is configured."""
    return f"inet {addr} " in run_text(["ifconfig"])


def llama_server_version(binary: str = "llama-server") -> str:
    """The "version:" line of `llama-server --version` ("?" when it cannot run). binary: its path when it is not on
    PATH (Windows: CARL_LLAMA_DIR, adapters/gpu_probe.py llama_program)."""
    try:
        r = subprocess.run([binary, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=30)
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

    def free_bytes(self) -> int:
        """The free, inactive and speculative pages of `vm_stat` (0 when it does not answer)."""
        from .vm_stat import ARGS, available_bytes
        return available_bytes(run_text(ARGS))


class MacHostInfo:
    """This Mac's RAM and processor in words, from sysctl (implements HostInfo, contract C-015): the machine of an
    Auto-tune record ("Apple M2 Max 32 GB": the RAM in whole GiB, written GB as the records always had it). A computer
    without sysctl gives 0 and "" (Linux, until W6.11 gives it its own HostInfo)."""

    def ram_bytes(self) -> int:
        return sysctl_int("hw.memsize")

    def cpu_name(self) -> str:
        """The processor's name alone (Auto-tune's first line)."""
        return sysctl_text("machdep.cpu.brand_string")

    def machine(self) -> str:
        cpu, ram = self.cpu_name(), self.ram_bytes()
        return f"{cpu} {ram // 2 ** 30} GB" if cpu or ram else ""


class SystemClock:
    def today(self) -> str:
        return time.strftime("%Y-%m-%d")
