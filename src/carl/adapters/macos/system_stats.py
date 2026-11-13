"""This Mac and its processes, through macOS commands (ps, netstat, sysctl, vm_stat, pmset, ioreg, ifconfig) and
one libproc call (a process's footprint), for the dashboard: MacServerWatch (the ServerWatch port) and NetstatTable
(the TcpTable port). Every command runs from an argument list, never a shell, and through sh() here, so a test fakes
them all in one place. The parsers are in their own modules (bsd_netstat, ps, vm_stat, ioreg, pmset; the footprint:
libproc; the clipboard: desktop) and pure; their names stay importable from here."""
from __future__ import annotations

import os
import re
import shutil
import signal
import time
from typing import Callable, List, Optional, Sequence, Tuple

from ...domain.server.snapshot import ProcInfo, SlowStats, SystemStats, clean
from ..common.key_store import read_key
from ..common.log_tail import LocalServerFiles
from . import ioreg, pmset, vm_stat
from .bsd_netstat import ARGS as NETSTAT_ARGS, TcpRow, parse_netstat, run_limited, tcp_rows  # noqa: F401  (their names here too)
from .desktop import copy_to_clipboard  # noqa: F401  (the dashboard's actions copy through it until W5.3)
from .ioreg import parse_gpu  # noqa: F401
from .libproc import RUSAGE_INFO_V2, RusageInfoV2, phys_footprint  # noqa: F401
from .pmset import parse_power, parse_sleep_events, parse_thermal  # noqa: F401
from .ps import ROW_FIELDS, parse_ps_row
from .vm_stat import parse_swap, parse_vm_stat, pressure_name  # noqa: F401


def sh(cmd: Sequence[str], timeout: float = 3) -> str:
    """stdout of cmd, "" on error or timeout. Does not wait for a child that ignores the kill (stuck in the kernel):
    subprocess.run would block forever (bsd_netstat.run_limited). Bytes that are not UTF-8 are replaced: pmset prints
    device names in MacRoman ("Alex\xd5s Magic Keyboard")."""
    return run_limited(cmd, timeout)


# ---------------------------------------------------------------- sockets
def netstat_tcp() -> List[TcpRow]:
    """TCP sockets of this Mac. netstat; not lsof unless netstat shows nothing (bsd_netstat.tcp_rows): lsof stats
    every mounted filesystem and hangs, unkillable, on a stale network share (a Time Machine SMB volume froze the
    monitor), and its fallback runs with -b, which does not."""
    return tcp_rows(sh)


def listen_host(rows: Sequence[TcpRow], port: int) -> Optional[str]:
    """Address the server on port listens on (all addresses: 127.0.0.1), or None."""
    for r in rows:
        if r.local_port == port and r.state == "LISTEN":
            return "127.0.0.1" if r.local_addr == "*" else r.local_addr
    return None


def listen_pid(rows: Sequence[TcpRow], port: int) -> Optional[int]:
    """PID of the process listening on port, or None."""
    return next((r.pid for r in rows if r.local_port == port and r.state == "LISTEN" and r.pid), None)


def next_free_port(rows: Sequence[TcpRow], port: int, span: int = 100) -> Optional[int]:
    """The first port after `port` that nothing listens on (Phase 23.5: the start offers it), or None."""
    taken = {r.local_port for r in rows if r.state == "LISTEN"}
    return next((p for p in range(port + 1, min(port + span, 65535) + 1) if p not in taken), None)


def connections(rows: Sequence[TcpRow], port: int, pid: int) -> List[Tuple[str, str]]:
    """(client address, client port) of each connection to pid's port."""
    out = []
    for r in rows:
        if r.pid == pid and r.local_port == port and r.state == "ESTABLISHED":
            host, _, cport = r.remote.rpartition(".")
            out.append((host, cport))
    return out


# ---------------------------------------------------------------- processes
def parse_ps(text: str) -> Optional[ProcInfo]:
    """`ps -o rss=,%cpu=,etime=,command=` output (rss in KiB) as a ProcInfo."""
    r = parse_ps_row(text)
    if r is None:
        return None
    return ProcInfo(rss=r.rss_kib * 1024, cpu=r.cpu, etime=r.etime, cmd=clean(r.command))


def process_info(pid: int) -> Optional[ProcInfo]:
    """Memory (RSS and footprint), CPU, run time and command line of pid."""
    info = parse_ps(sh(["ps", "-o", ROW_FIELDS, "-p", str(pid)]))
    if info:
        info.footprint = phys_footprint(pid)
    return info


def pid_alive(pid: Optional[int]) -> bool:
    """True if pid is running. Reaps it first when it's our child: the launcher
    execs into this monitor, so the server is our child and would otherwise
    linger as a zombie (still listed by ps) after it exits."""
    if not pid:
        return False
    try:
        if os.waitpid(pid, os.WNOHANG)[0] == pid:
            return False
    except ChildProcessError:
        pass
    stat = sh(["ps", "-o", "stat=", "-p", str(pid)]).strip()
    return bool(stat) and not stat.startswith("Z")


def kill(pid: int, sig: int) -> None:
    """Send sig to pid; a process that is already gone is fine."""
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass


def kill_group(pid: int, sig: int) -> None:
    """Send sig to pid's process group (a job started with start_new_session)."""
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass


def stop_pid(pid: Optional[int]) -> None:
    """Stop pid: SIGTERM, up to 30 s to exit, then SIGKILL."""
    if not pid or not pid_alive(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    for _ in range(60):
        if not pid_alive(pid):
            return
        time.sleep(0.5)
    kill(pid, signal.SIGKILL)
    time.sleep(2)


def kept_awake(pid: int) -> bool:
    """True if a power assertion (caffeinate -w) keeps the Mac awake for pid."""
    return pmset.awake_for(sh(pmset.ASSERTIONS_ARGS), pid)


# ---------------------------------------------------------------- memory, GPU, power
def sysctl_int(name: str, default: int) -> int:
    """An integer sysctl value, default if it can't be read."""
    v = sh(["sysctl", "-n", name]).strip()
    return int(v) if v.isdigit() else default


def read_system(page: int) -> SystemStats:
    """Memory (page: the VM page size), memory pressure, swap, GPU, power and load now."""
    vm = vm_stat.parse_vm_stat(sh(vm_stat.ARGS), page)
    gpu, gpumem = ioreg.parse_gpu(sh(ioreg.ARGS))
    return SystemStats(
        wired=vm.get("Pages wired down", 0), active=vm.get("Pages active", 0),
        comp=vm.get("Pages occupied by compressor", 0), free=vm.get("Pages free", 0),
        anon=vm.get("Anonymous pages", 0), purgeable=vm.get("Pages purgeable", 0),
        files=vm.get("File-backed pages", 0),
        pressure=vm_stat.pressure_name(sh(vm_stat.PRESSURE_ARGS).strip()),
        swap=vm_stat.parse_swap(sh(vm_stat.SWAP_ARGS)), gpu=gpu, gpumem=gpumem,
        power=pmset.parse_power(sh(pmset.BATT_ARGS)), load=os.getloadavg())


# ---------------------------------------------------------------- slow stats (once a minute)
def process_start(pid: int) -> Optional[float]:
    """Epoch seconds pid started, from ps lstart."""
    try:
        return time.mktime(time.strptime(sh(["ps", "-o", "lstart=", "-p", str(pid)]).strip(), "%a %b %d %H:%M:%S %Y"))
    except ValueError:
        return None


def read_slow(server_pid: Callable[[], Optional[int]], disk_path: str) -> SlowStats:
    """Thermal state, sleep / wake since the server started (pmset log takes ~1 s), free disk.
    server_pid is asked after the thermal check: at start-up the first refresh has found it by then."""
    thermal = pmset.parse_thermal(sh(pmset.THERM_ARGS))
    pid = server_pid()
    start = process_start(pid) if pid else None
    events = pmset.parse_sleep_events(sh(pmset.LOG_ARGS, timeout=10), start) if start else []
    try:
        du = shutil.disk_usage(disk_path)
        disk: Optional[Tuple[int, int]] = (du.free, du.total)
    except OSError:
        disk = None
    return SlowStats(thermal=thermal, sleep_events=events, disk=disk)


# ---------------------------------------------------------------- network
def parse_ifconfig(text: str, skip: str) -> List[str]:
    """IPv4 addresses from ifconfig output, without loopback, skip and duplicates."""
    out: List[str] = []
    for m in re.finditer(r"\binet (\d+\.\d+\.\d+\.\d+)", text):
        a = m.group(1)
        if a != "127.0.0.1" and a != skip and a not in out:
            out.append(a)
    return out


def local_addrs(vm_addr: str) -> List[str]:
    """IPv4 addresses of this Mac (not loopback, not the VM address): the LAN, Parallels, ..."""
    return parse_ifconfig(sh(["ifconfig"]), vm_addr)


# ---------------------------------------------------------------- the ports
class NetstatTable:
    """The TCP sockets of one netstat read (implements TcpTable, contract C-039)."""

    def __init__(self, rows: Sequence[TcpRow]) -> None:
        self.rows = list(rows)

    def listen_host(self, port: int) -> Optional[str]:
        return listen_host(self.rows, port)

    def listen_pid(self, port: int) -> Optional[int]:
        return listen_pid(self.rows, port)

    def connections(self, port: int, pid: int) -> List[Tuple[str, str]]:
        return connections(self.rows, port, pid)

    def next_free_port(self, port: int) -> Optional[int]:
        return next_free_port(self.rows, port)


class MacServerWatch:
    """What the dashboard reads of this Mac and of the server it watches (implements ServerWatch, contract C-038).
    page: the VM page size (sysctl hw.pagesize, read at the dashboard's start)."""

    def __init__(self, page: int) -> None:
        self.page = page

    def sockets(self) -> NetstatTable:
        return NetstatTable(netstat_tcp())

    def process(self, pid: int) -> Optional[ProcInfo]:
        return process_info(pid)

    def alive(self, pid: Optional[int]) -> bool:
        return pid_alive(pid)

    def kept_awake(self, pid: int) -> bool:
        return kept_awake(pid)

    def kill(self, pid: int) -> None:
        kill(pid, signal.SIGKILL)

    def memory(self) -> SystemStats:
        return read_system(self.page)

    def slow(self, server_pid: Callable[[], Optional[int]], disk_path: str) -> SlowStats:
        return read_slow(server_pid, disk_path)

    def read_key(self, path: str) -> str:
        return read_key(path)

    def files(self) -> LocalServerFiles:
        return LocalServerFiles()
