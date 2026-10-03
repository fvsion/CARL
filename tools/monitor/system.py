"""This Mac and its processes, through macOS commands (ps, netstat, sysctl, vm_stat, pmset,
ioreg, ifconfig, pbcopy). Every command runs from an argument list, never a shell. The
parse_* functions are pure and take the commands' text output."""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import threading
import time
from typing import Callable, List, NamedTuple, Optional, Sequence, Tuple

from .model import ProcInfo, SleepEvent, SlowStats, SystemStats, clean

NETSTAT = "/usr/sbin/netstat"


def sh(cmd: Sequence[str], timeout: float = 3) -> str:
    """stdout of cmd, "" on error or timeout. Does not wait for a child that
    ignores the kill (stuck in the kernel): subprocess.run would block forever.
    Bytes that are not UTF-8 are replaced: pmset prints device names in MacRoman
    ("Brennon\xd5s Magic Keyboard")."""
    try:
        p = subprocess.Popen(list(cmd), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, text=True, errors="replace")
    except OSError:
        return ""
    try:
        out: str = p.communicate(timeout=timeout)[0]
        return out
    except subprocess.TimeoutExpired:
        p.kill()
        if p.stdout:
            p.stdout.close()                # our end of the pipe: closing it does not wait for the child
        threading.Thread(target=p.wait, daemon=True).start()
        return ""


# ---------------------------------------------------------------- sockets
class TcpRow(NamedTuple):
    """One TCP socket from netstat."""
    local_addr: str
    local_port: int
    remote: str         # address.port
    state: str
    pid: Optional[int]


_NETSTAT_PID = re.compile(r":(\d+)\s+\d{5}\s")     # "process:pid  00100": process names may hold spaces


def parse_netstat(text: str) -> List[TcpRow]:
    """TCP sockets from `netstat -anv -p tcp` output."""
    rows = []
    for line in text.splitlines():
        f = line.split()
        if len(f) < 6 or not f[0].startswith("tcp"):
            continue
        m = _NETSTAT_PID.search(line)
        la, _, lp = f[3].rpartition(".")
        if lp.isdigit():
            rows.append(TcpRow(la, int(lp), f[4], f[5], int(m.group(1)) if m else None))
    return rows


def netstat_tcp() -> List[TcpRow]:
    """TCP sockets of this Mac. Not lsof: lsof stats every mounted filesystem and hangs,
    unkillable, on a stale network share (a Time Machine SMB volume froze the monitor)."""
    return parse_netstat(sh([NETSTAT, "-anv", "-p", "tcp"]))


def listen_host(rows: Sequence[TcpRow], port: int) -> Optional[str]:
    """Address the server on port listens on (all addresses: 127.0.0.1), or None."""
    for r in rows:
        if r.local_port == port and r.state == "LISTEN":
            return "127.0.0.1" if r.local_addr == "*" else r.local_addr
    return None


def listen_pid(rows: Sequence[TcpRow], port: int) -> Optional[int]:
    """PID of the process listening on port, or None."""
    return next((r.pid for r in rows if r.local_port == port and r.state == "LISTEN" and r.pid), None)


def connections(rows: Sequence[TcpRow], port: int, pid: int) -> List[Tuple[str, str]]:
    """(client address, client port) of each connection to pid's port."""
    out = []
    for r in rows:
        if r.pid == pid and r.local_port == port and r.state == "ESTABLISHED":
            host, _, cport = r.remote.rpartition(".")
            out.append((host, cport))
    return out


# ---------------------------------------------------------------- processes
_PS = re.compile(r"\s*(\d+)\s+([\d.]+)\s+(\S+)\s+(.*)")


def parse_ps(text: str) -> Optional[ProcInfo]:
    """`ps -o rss=,%cpu=,etime=,command=` output (rss in KiB) as a ProcInfo."""
    m = _PS.match(text)
    if not m:
        return None
    return ProcInfo(rss=int(m.group(1)) * 1024, cpu=float(m.group(2)), etime=m.group(3), cmd=clean(m.group(4).strip()))


def process_info(pid: int) -> Optional[ProcInfo]:
    """Memory, CPU, run time and command line of pid."""
    return parse_ps(sh(["ps", "-o", "rss=,%cpu=,etime=,command=", "-p", str(pid)]))


def etime_seconds(etime: Optional[str]) -> int:
    """Seconds in a ps etime ([[dd-]hh:]mm:ss), plus a minute of slack."""
    secs = 0
    for v, mul in zip(reversed(re.split(r"[-:]", etime or "0")), (1, 60, 3600, 86400)):
        secs += int(v or 0) * mul
    return secs + 60


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
    return re.search(rf"on behalf of Process ID {pid}(?!\d)", sh(["pmset", "-g", "assertions"])) is not None


# ---------------------------------------------------------------- memory, GPU, power
def sysctl_int(name: str, default: int) -> int:
    """An integer sysctl value, default if it can't be read."""
    v = sh(["sysctl", "-n", name]).strip()
    return int(v) if v.isdigit() else default


def parse_vm_stat(text: str, page: int) -> dict[str, int]:
    """vm_stat counters in bytes, by name ("Pages wired down", ...)."""
    vm = {}
    for line in text.splitlines():
        if ":" in line:
            key, v = line.split(":", 1)
            v = v.strip().rstrip(".")
            if v.isdigit():
                vm[key.strip()] = int(v) * page
    return vm


def parse_swap(text: str) -> Tuple[float, float]:
    """(used, total) bytes from `sysctl -n vm.swapusage`."""
    m = re.search(r"total = ([\d.]+)M\s+used = ([\d.]+)M", text)
    return (float(m.group(2)) * 2**20, float(m.group(1)) * 2**20) if m else (0, 0)


def parse_gpu(text: str) -> Tuple[Optional[int], Optional[int]]:
    """(utilisation %, bytes in use) from `ioreg -r -d 1 -c IOAccelerator`."""
    g = re.search(r'"Device Utilization %"=(\d+)', text)
    gm = re.search(r'"In use system memory"=(\d+)', text)
    return (int(g.group(1)) if g else None), (int(gm.group(1)) if gm else None)


def parse_power(text: str) -> str:
    """"AC 100%" / "battery 54%" from `pmset -g batt`."""
    p = re.search(r"(\d+)%", text)
    return ("AC" if "AC Power" in text else "battery") + (f" {p.group(1)}%" if p else "")


def pressure_name(level: str) -> str:
    """kern.memorystatus_vm_pressure_level as a word."""
    return {"1": "normal", "2": "WARNING", "4": "CRITICAL"}.get(level, level or "?")


def read_system(page: int) -> SystemStats:
    """Memory (page: the VM page size), memory pressure, swap, GPU, power and load now."""
    vm = parse_vm_stat(sh(["vm_stat"]), page)
    gpu, gpumem = parse_gpu(sh(["ioreg", "-r", "-d", "1", "-c", "IOAccelerator"]))
    return SystemStats(
        wired=vm.get("Pages wired down", 0), active=vm.get("Pages active", 0),
        comp=vm.get("Pages occupied by compressor", 0), free=vm.get("Pages free", 0),
        pressure=pressure_name(sh(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"]).strip()),
        swap=parse_swap(sh(["sysctl", "-n", "vm.swapusage"])), gpu=gpu, gpumem=gpumem,
        power=parse_power(sh(["pmset", "-g", "batt"])), load=os.getloadavg())


# ---------------------------------------------------------------- slow stats (once a minute)
def parse_thermal(text: str) -> str:
    """"nominal", or the warnings of `pmset -g therm` (60 characters at most)."""
    warn = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("Note:")]
    return "nominal" if not warn else "; ".join(warn)[:60]


_PMSET_EVENT = re.compile(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) [+-]\d{4} (Sleep|Wake|DarkWake)\s+(.*)")


def parse_sleep_events(text: str, since: float) -> List[SleepEvent]:
    """Sleep / wake events at or after since (epoch seconds) from `pmset -g log`."""
    events = []
    for line in text.splitlines():
        m = _PMSET_EVENT.match(line)
        if m and time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")) >= since:
            events.append((m.group(1)[11:], m.group(2), m.group(3).strip()[:50]))
    return events


def process_start(pid: int) -> Optional[float]:
    """Epoch seconds pid started, from ps lstart."""
    try:
        return time.mktime(time.strptime(sh(["ps", "-o", "lstart=", "-p", str(pid)]).strip(), "%a %b %d %H:%M:%S %Y"))
    except ValueError:
        return None


def read_slow(server_pid: Callable[[], Optional[int]], disk_path: str) -> SlowStats:
    """Thermal state, sleep / wake since the server started (pmset log takes ~1 s), free disk.
    server_pid is asked after the thermal check: at start-up the first refresh has found it by then."""
    thermal = parse_thermal(sh(["pmset", "-g", "therm"]))
    pid = server_pid()
    start = process_start(pid) if pid else None
    events = parse_sleep_events(sh(["pmset", "-g", "log"], timeout=10), start) if start else []
    try:
        du = shutil.disk_usage(disk_path)
        disk: Optional[Tuple[int, int]] = (du.free, du.total)
    except OSError:
        disk = None
    return SlowStats(thermal=thermal, sleep_events=events, disk=disk)


# ---------------------------------------------------------------- network, clipboard
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


def copy_to_clipboard(text: str) -> bool:
    """Put text on the clipboard (pbcopy); False if that failed."""
    try:
        subprocess.run(["pbcopy"], input=text, text=True, timeout=3, check=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
