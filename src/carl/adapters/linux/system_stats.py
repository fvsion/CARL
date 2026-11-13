"""This Linux computer and its processes, through /proc and /sys (and `ss` and `nvidia-smi` where they are installed),
for the dashboard: LinuxServerWatch (the ServerWatch port, C-038), LinuxTcpTable (the TcpTable port, C-039) and
LinuxSystemStats (the SystemStats port, C-033). The kernel's files are read as text and parsed by pure functions that
take the text; a file or a program that is missing gives nothing, never an error. Every program runs from an
argument list, never a shell, and through sh() here, so a test fakes them all in one place.

What the Mac's figures become here (the dashboard's MEMORY and THIS MAC cards read the same fields):
  used memory       MemTotal less MemAvailable (what `free` calls used): app memory (AnonPages), the compressed
                    memory (Zswap), and the rest as "wired" (the kernel's, the shared and the mapped memory)
  cached files      Buffers and Cached
  pressure          the kernel's pressure stall figure (/proc/pressure/memory, "some" over 10 s), else the share of
                    memory that is available; the words are the Mac's (normal, WARNING, CRITICAL)
  GPU use, memory   nvidia-smi, else the amdgpu files: the busiest GPU's use and the memory in use on all of them
  power             the battery and the mains in /sys/class/power_supply ("AC", "AC 100%", "battery 54%")
  heat              the hottest CPU sensor of /sys, in CARL's bands (nominal below 80 C, fair, serious from 90 C,
                    critical from 100 C): Linux has no thermal state of its own
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

from ...domain.server.snapshot import ProcInfo, SlowStats, SystemStats, clean
from ...domain.shared.types import JsonObject
from ..common.key_store import read_key
from ..common.log_tail import LocalServerFiles
from .host import PROC, parse_meminfo, read
from .inhibit import INHIBIT, WHO

SYS = "/sys"
SS_ARGS = ["ss", "-tanpH"]
NVIDIA = "nvidia-smi"
NVIDIA_GPUS = ["--query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu",
               "--format=csv,noheader,nounits"]
NVIDIA_APPS = ["--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"]
CPU_SENSORS = ("coretemp", "k10temp", "zenpower", "cpu_thermal")      # the hwmon chips that are a processor's
MIB = 2 ** 20
PORT_SPAN = 100


def sh(cmd: Sequence[str], timeout: float = 3) -> str:
    """stdout of cmd, "" on error or timeout. Does not wait for a child that ignores the kill (stuck in the kernel):
    subprocess.run would block forever. Bytes that are not UTF-8 are replaced."""
    try:
        p = subprocess.Popen(list(cmd), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
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


def _pids(proc: str) -> List[int]:
    try:
        return sorted(int(n) for n in os.listdir(proc) if n.isdigit())
    except OSError:
        return []


# ---------------------------------------------------------------- sockets
class TcpRow(NamedTuple):
    """One TCP socket. inode: the kernel's number of the socket when the owner is not known (/proc/net/tcp)."""
    state: str                  # LISTEN, ESTABLISHED, ...
    local_addr: str
    local_port: int
    remote_addr: str
    remote_port: int
    pid: Optional[int] = None
    inode: str = ""


_SS_PID = re.compile(r"pid=(\d+)")
_STATES = {"ESTAB": "ESTABLISHED"}              # ss's short name
_PROC_STATES = {"0A": "LISTEN", "01": "ESTABLISHED"}
WILDCARDS = ("*", "0.0.0.0", "::")


def split_address(text: str) -> Tuple[str, int]:
    """(address, port) of ss's "127.0.0.1:8080", "[::1]:8080" and "*:*" (a port that is not a number is 0)."""
    host, _, port = text.rpartition(":")
    return host.strip("[]"), int(port) if port.isdigit() else 0


def parse_ss(text: str) -> List[TcpRow]:
    """The rows of `ss -tanpH`: state, queues, local, peer, and the owner as users:(("name",pid=N,fd=M))."""
    rows: List[TcpRow] = []
    for line in text.splitlines():
        f = line.split()
        if len(f) < 5:
            continue
        local, remote = split_address(f[3]), split_address(f[4])
        owner = _SS_PID.search(line)
        rows.append(TcpRow(_STATES.get(f[0], f[0]), local[0], local[1], remote[0], remote[1],
                           int(owner.group(1)) if owner else None))
    return rows


def decode_address(hexed: str) -> str:
    """The address of /proc/net/tcp's "0100007F" and tcp6's 32 digits (each group of 8 is a 32-bit word in the kernel's
    byte order, which is little-endian on the computers CARL runs on)."""
    try:
        if len(hexed) == 8:
            return socket.inet_ntop(socket.AF_INET, bytes.fromhex(hexed)[::-1])
        return socket.inet_ntop(socket.AF_INET6, b"".join(bytes.fromhex(hexed[i:i + 8])[::-1] for i in range(0, 32, 8)))
    except (ValueError, OSError):
        return ""


def parse_proc_tcp(text: str) -> List[TcpRow]:
    """The rows of /proc/net/tcp or tcp6 (the owner is the socket's inode until owners() finds the pid)."""
    rows: List[TcpRow] = []
    for line in text.splitlines()[1:]:
        f = line.split()
        if len(f) < 10:
            continue
        try:
            la, _, lp = f[1].partition(":")
            ra, _, rp = f[2].partition(":")
            rows.append(TcpRow(_PROC_STATES.get(f[3], f[3]), decode_address(la), int(lp, 16), decode_address(ra),
                               int(rp, 16), None, f[9]))
        except ValueError:
            continue
    return rows


def socket_owners(proc: str = PROC) -> Dict[str, int]:
    """socket inode -> the pid whose file descriptor it is (only the processes this user may look into)."""
    out: Dict[str, int] = {}
    for pid in _pids(proc):
        fd_dir = os.path.join(proc, str(pid), "fd")
        try:
            for fd in os.listdir(fd_dir):
                target = os.readlink(os.path.join(fd_dir, fd))
                if target.startswith("socket:["):
                    out.setdefault(target[8:-1], pid)
        except OSError:
            continue
    return out


def read_rows(proc: str = PROC, run: Callable[[Sequence[str]], str] = sh) -> List[TcpRow]:
    """The TCP sockets now: `ss` when it answers, else /proc/net/tcp and tcp6."""
    rows = parse_ss(run(SS_ARGS))
    if rows:
        return rows
    return parse_proc_tcp(read(os.path.join(proc, "net", "tcp"))) + parse_proc_tcp(read(os.path.join(proc, "net", "tcp6")))


class LinuxTcpTable:
    """The TCP sockets of one read (implements TcpTable, contract C-039). owners: the pids of the sockets that ss did
    not name (asked once, and only when an answer needs it)."""

    def __init__(self, rows: Sequence[TcpRow], owners: Callable[[], Dict[str, int]] = socket_owners) -> None:
        self.rows = list(rows)
        self._owners = owners
        self._found: Optional[Dict[str, int]] = None

    def _pid(self, row: TcpRow) -> Optional[int]:
        if row.pid or not row.inode:
            return row.pid
        if self._found is None:
            self._found = self._owners()
        return self._found.get(row.inode)

    def listen_host(self, port: int) -> Optional[str]:
        for r in self.rows:
            if r.local_port == port and r.state == "LISTEN":
                return "127.0.0.1" if r.local_addr in WILDCARDS else r.local_addr
        return None

    def listen_pid(self, port: int) -> Optional[int]:
        for r in self.rows:
            if r.local_port == port and r.state == "LISTEN":
                pid = self._pid(r)
                if pid:
                    return pid
        return None

    def connections(self, port: int, pid: int) -> List[Tuple[str, str]]:
        return [(r.remote_addr, str(r.remote_port)) for r in self.rows
                if r.local_port == port and r.state == "ESTABLISHED" and self._pid(r) == pid]

    def next_free_port(self, port: int) -> Optional[int]:
        taken = {r.local_port for r in self.rows if r.state == "LISTEN"}
        return next((p for p in range(port + 1, min(port + PORT_SPAN, 65535) + 1) if p not in taken), None)


# ---------------------------------------------------------------- processes
def parse_stat(text: str) -> Optional[Tuple[str, int, int]]:
    """(state, utime + stime, starttime) of /proc/PID/stat, the times in clock ticks. The name is in parentheses and
    may hold spaces and parentheses, so the fields are counted from the last one."""
    rest = text[text.rfind(")") + 1:].split()
    if text.rfind(")") < 0 or len(rest) < 20:
        return None
    try:
        return rest[0], int(rest[11]) + int(rest[12]), int(rest[19])
    except ValueError:
        return None


def parse_status(text: str) -> Dict[str, int]:
    """/proc/PID/status as bytes by name for its kB lines (VmRSS, RssAnon, ...)."""
    return parse_meminfo(text)


def etime_text(seconds: float) -> str:
    """A run time as ps writes it: [[dd-]hh:]mm:ss."""
    s = max(int(seconds), 0)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d}-{h:02d}:{m:02d}:{s:02d}"
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def clock_ticks() -> int:
    try:
        return int(os.sysconf("SC_CLK_TCK"))
    except (AttributeError, ValueError, OSError):
        return 100


def process_info(pid: int, proc: str = PROC, hz: int = 0) -> Optional[ProcInfo]:
    """Memory (RSS, and the footprint: RSS without the mapped files), CPU (the share of the time since the process
    started, as ps counts it), run time and command line of pid; None when it can't be read."""
    hz = hz or clock_ticks()
    base = os.path.join(proc, str(pid))
    stat = parse_stat(read(os.path.join(base, "stat")))
    status = parse_status(read(os.path.join(base, "status")))
    if stat is None or not status:
        return None
    try:
        uptime = float(read(os.path.join(proc, "uptime")).split()[0])
    except (IndexError, ValueError):
        uptime = 0.0
    elapsed = max(uptime - stat[2] / hz, 0.0)
    cpu = round(stat[1] / hz / elapsed * 100, 1) if elapsed > 0 else 0.0
    cmd = " ".join(read(os.path.join(base, "cmdline")).replace("\0", " ").split())
    if not cmd:                                   # a zombie and a kernel thread have no command line
        text = read(os.path.join(base, "stat"))
        cmd = "[" + text[text.find("(") + 1:text.rfind(")")] + "]"
    return ProcInfo(rss=status.get("VmRSS", 0), cpu=cpu, etime=etime_text(elapsed), cmd=clean(cmd),
                    footprint=status.get("RssAnon"))


def process_start(pid: int, proc: str = PROC, hz: int = 0) -> Optional[float]:
    """Epoch seconds pid started."""
    stat = parse_stat(read(os.path.join(proc, str(pid), "stat")))
    if stat is None:
        return None
    try:
        boot = time.time() - float(read(os.path.join(proc, "uptime")).split()[0])
    except (IndexError, ValueError):
        return None
    return boot + stat[2] / (hz or clock_ticks())


def pid_alive(pid: Optional[int], proc: str = PROC) -> bool:
    """True if pid is running. Reaps it first when it's our child (the launcher execs into the dashboard, so the
    server is our child and would otherwise linger as a zombie after it exits)."""
    if not pid:
        return False
    try:
        if os.waitpid(pid, os.WNOHANG)[0] == pid:
            return False
    except ChildProcessError:
        pass
    stat = parse_stat(read(os.path.join(proc, str(pid), "stat")))
    return stat is not None and stat[0] != "Z"


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


def kept_awake(pid: int, proc: str = PROC) -> bool:
    """True if a systemd-inhibit lock of CARL's (adapters/linux/inhibit.py) is held for pid: its process waits for
    that pid, which is the last word of its command line."""
    want = str(pid)
    for other in _pids(proc):
        argv = read(os.path.join(proc, str(other), "cmdline")).split("\0")
        if argv and argv[-1] == "":
            argv.pop()
        if len(argv) > 1 and os.path.basename(argv[0]) == INHIBIT and WHO in argv and argv[-1] == want:
            return True
    return False


# ---------------------------------------------------------------- memory, GPU, power
def parse_loadavg(text: str) -> Tuple[float, float, float]:
    f = text.split()
    try:
        return float(f[0]), float(f[1]), float(f[2])
    except (IndexError, ValueError):
        return 0.0, 0.0, 0.0


def parse_pressure(text: str) -> Optional[float]:
    """The share of the last 10 seconds in which a task waited for memory ("some avg10=" of /proc/pressure/memory);
    None when the kernel has no such file."""
    m = re.search(r"^some\s+avg10=([\d.]+)", text, re.M)
    return float(m.group(1)) if m else None


def pressure_word(some10: Optional[float], available: int, total: int) -> str:
    """The Mac's words for the memory pressure: from the stall figure (under 5 normal, under 20 WARNING), else from the
    share of memory that is available (10 % and 5 %)."""
    if some10 is not None:
        return "normal" if some10 < 5 else "WARNING" if some10 < 20 else "CRITICAL"
    if total <= 0:
        return "?"
    free = available / total
    return "normal" if free >= 0.10 else "WARNING" if free >= 0.05 else "CRITICAL"


def memory_stats(info: Dict[str, int], pressure: str, swap: Tuple[float, float], gpu: Optional[int],
                 gpumem: Optional[int], power: str, load: Tuple[float, float, float]) -> SystemStats:
    """The memory counters of /proc/meminfo as the dashboard's figures (the top of this module)."""
    total = info.get("MemTotal", 0)
    available = info.get("MemAvailable", info.get("MemFree", 0) + info.get("Cached", 0))
    used = max(total - available, 0)
    anon, comp = info.get("AnonPages", 0), info.get("Zswap", 0)
    return SystemStats(
        wired=max(used - anon - comp, 0), active=info.get("Active", 0), comp=comp, free=info.get("MemFree", 0),
        anon=anon, purgeable=0, files=info.get("Cached", 0) + info.get("Buffers", 0), pressure=pressure, swap=swap,
        gpu=gpu, gpumem=gpumem, power=power, load=load)


def swap_of(info: Dict[str, int]) -> Tuple[float, float]:
    """(used, total) swap bytes."""
    total = info.get("SwapTotal", 0)
    return float(max(total - info.get("SwapFree", 0), 0)), float(total)


class GpuReading(NamedTuple):
    name: str
    total: int                  # bytes of memory
    used: int
    util: Optional[int]         # percent
    temp: Optional[int]         # degrees C


def _int(text: str) -> Optional[int]:
    try:
        return int(float(text))
    except ValueError:
        return None


def parse_nvidia_gpus(text: str) -> List[GpuReading]:
    """`nvidia-smi --query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu` (csv, MiB)."""
    out: List[GpuReading] = []
    for line in text.splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) < 5:
            continue
        total, used = _int(f[1]), _int(f[2])
        if total is None or used is None:
            continue
        out.append(GpuReading(f[0], total * MIB, used * MIB, _int(f[3]), _int(f[4])))
    return out


def parse_nvidia_apps(text: str) -> Dict[int, int]:
    """pid -> bytes of GPU memory of `nvidia-smi --query-compute-apps=pid,used_memory` (a process on two GPUs: the
    sum; "[N/A]" is skipped)."""
    out: Dict[int, int] = {}
    for line in text.splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) >= 2 and f[0].isdigit() and _int(f[1]) is not None:
            out[int(f[0])] = out.get(int(f[0]), 0) + (_int(f[1]) or 0) * MIB
    return out


def amdgpu_readings(sys_dir: str = SYS) -> List[GpuReading]:
    """The AMD GPUs of the amdgpu driver's files: the memory, the use and the temperature of each card."""
    out: List[GpuReading] = []
    for total in sorted(glob.glob(os.path.join(sys_dir, "class", "drm", "card*", "device", "mem_info_vram_total"))):
        dev = os.path.dirname(total)
        size, used = _int(read(total)), _int(read(os.path.join(dev, "mem_info_vram_used")))
        if size is None or used is None:
            continue
        temps = [_int(read(p)) for p in sorted(glob.glob(os.path.join(dev, "hwmon", "hwmon*", "temp1_input")))]
        temp = next((t // 1000 for t in temps if t is not None), None)
        name = read(os.path.join(dev, "product_name")).strip() or "AMD GPU"
        out.append(GpuReading(name, size, used, _int(read(os.path.join(dev, "gpu_busy_percent"))), temp))
    return out


def gpu_readings(run: Callable[[Sequence[str]], str] = sh, sys_dir: str = SYS) -> List[GpuReading]:
    """The GPUs now: nvidia-smi's, else the amdgpu files'."""
    return parse_nvidia_gpus(run([NVIDIA, *NVIDIA_GPUS])) or amdgpu_readings(sys_dir)


def gpu_figures(readings: Sequence[GpuReading]) -> Tuple[Optional[int], Optional[int]]:
    """(the busiest GPU's use in percent, the memory in use on all of them in bytes); None for what none can tell."""
    utils = [g.util for g in readings if g.util is not None]
    return (max(utils) if utils else None), (sum(g.used for g in readings) if readings else None)


def parse_power(sys_dir: str = SYS) -> str:
    """"AC", "AC 100%" or "battery 54%" from /sys/class/power_supply: a computer with no battery is on the mains."""
    batteries: List[Tuple[int, str]] = []
    mains = False
    for supply in sorted(glob.glob(os.path.join(sys_dir, "class", "power_supply", "*"))):
        kind = read(os.path.join(supply, "type")).strip()
        if kind == "Mains":
            mains = mains or read(os.path.join(supply, "online")).strip() == "1"
        elif kind == "Battery":
            capacity = _int(read(os.path.join(supply, "capacity")))
            batteries.append((capacity if capacity is not None else -1, read(os.path.join(supply, "status")).strip()))
    if not batteries:
        return "AC"
    level, status = batteries[0]
    charging = mains or status in ("Charging", "Full", "Not charging")
    return ("AC" if charging else "battery") + (f" {level}%" if level >= 0 else "")


def thermal_state(sys_dir: str = SYS) -> str:
    """nominal, fair, serious or critical from the hottest processor sensor ("" when there is none)."""
    temps: List[int] = []
    for path in glob.glob(os.path.join(sys_dir, "class", "thermal", "thermal_zone*", "temp")):
        t = _int(read(path))
        if t is not None:
            temps.append(t)
    for chip in glob.glob(os.path.join(sys_dir, "class", "hwmon", "hwmon*")):
        if read(os.path.join(chip, "name")).strip() in CPU_SENSORS:
            temps += [t for t in (_int(read(p)) for p in glob.glob(os.path.join(chip, "temp*_input"))) if t is not None]
    hot = max((t for t in temps if 0 < t < 150000), default=0) / 1000
    if not hot:
        return ""
    return "critical" if hot >= 100 else "serious" if hot >= 90 else "fair" if hot >= 80 else "nominal"


def parse_cpu_times(text: str) -> Tuple[int, int]:
    """(busy, total) clock ticks since boot of the first line of /proc/stat (idle and iowait are not busy)."""
    f = text.split("\n", 1)[0].split()
    try:
        v = [int(x) for x in f[1:9]]
    except ValueError:
        return 0, 0
    return (sum(v) - v[3] - v[4], sum(v)) if len(v) == 8 else (0, 0)


class LinuxServerWatch:
    """What the dashboard reads of this computer and of the server it watches (implements ServerWatch, contract
    C-038). page: the memory page size (unused here: the counters are in bytes). proc, sys_dir and run: the folders of
    the kernel's files and how a program runs (a test gives made-up ones)."""

    def __init__(self, page: int = 4096, proc: str = PROC, sys_dir: str = SYS,
                 run: Callable[[Sequence[str]], str] = sh) -> None:
        self.page, self.proc, self.sys, self.run = page, proc, sys_dir, run

    def sockets(self) -> LinuxTcpTable:
        return LinuxTcpTable(read_rows(self.proc, self.run), lambda: socket_owners(self.proc))

    def process(self, pid: int) -> Optional[ProcInfo]:
        return process_info(pid, self.proc)

    def alive(self, pid: Optional[int]) -> bool:
        return pid_alive(pid, self.proc)

    def kept_awake(self, pid: int) -> bool:
        return kept_awake(pid, self.proc)

    def kill(self, pid: int) -> None:
        kill(pid, signal.SIGKILL)

    def memory(self) -> SystemStats:
        info = parse_meminfo(read(os.path.join(self.proc, "meminfo")))
        some10 = parse_pressure(read(os.path.join(self.proc, "pressure", "memory")))
        word = pressure_word(some10, info.get("MemAvailable", info.get("MemFree", 0)), info.get("MemTotal", 0))
        gpu, gpumem = gpu_figures(gpu_readings(self.run, self.sys))
        return memory_stats(info, word, swap_of(info), gpu, gpumem, parse_power(self.sys),
                            parse_loadavg(read(os.path.join(self.proc, "loadavg"))))

    def slow(self, server_pid: Callable[[], Optional[int]], disk_path: str) -> SlowStats:
        """The thermal state and the free disk (a Linux computer keeps no sleep and wake record to read)."""
        thermal = thermal_state(self.sys)
        try:
            du = shutil.disk_usage(disk_path)
            disk: Optional[Tuple[int, int]] = (du.free, du.total)
        except OSError:
            disk = None
        return SlowStats(thermal=thermal, sleep_events=[], disk=disk)

    def read_key(self, path: str) -> str:
        return read_key(path)

    def files(self) -> LocalServerFiles:
        return LocalServerFiles()


class LinuxSystemStats:
    """One sample of this computer's state (implements SystemStats, contract C-033). Fields (bytes unless said): ram_total,
    ram_used, ram_available, swap_total, swap_used, cpu_percent (since the last sample; since boot for the first),
    load (1, 5 and 15 minutes), power, pressure; gpus: a list of {name, mem_total, mem_used, util (percent),
    temp (C)}; server (when a pid is given and readable): {pid, ram, vram}. A field it can't tell is left out."""

    def __init__(self, proc: str = PROC, sys_dir: str = SYS, run: Callable[[Sequence[str]], str] = sh) -> None:
        self.proc, self.sys, self.run = proc, sys_dir, run
        self._cpu: Optional[Tuple[int, int]] = None

    def sample(self, server_pid: Optional[int]) -> JsonObject:
        info = parse_meminfo(read(os.path.join(self.proc, "meminfo")))
        total = info.get("MemTotal", 0)
        available = info.get("MemAvailable", info.get("MemFree", 0) + info.get("Cached", 0))
        out: JsonObject = {"ram_total": total, "ram_used": max(total - available, 0), "ram_available": available}
        used_swap, swap = swap_of(info)
        out.update(swap_total=int(swap), swap_used=int(used_swap))
        busy, whole = parse_cpu_times(read(os.path.join(self.proc, "stat")))
        before = self._cpu or (0, 0)
        self._cpu = (busy, whole)
        if whole > before[1]:
            out["cpu_percent"] = round((busy - before[0]) / (whole - before[1]) * 100, 1)
        out["load"] = list(parse_loadavg(read(os.path.join(self.proc, "loadavg"))))
        out["power"] = parse_power(self.sys)
        out["pressure"] = pressure_word(parse_pressure(read(os.path.join(self.proc, "pressure", "memory"))),
                                        available, total)
        gpus = gpu_readings(self.run, self.sys)
        if gpus:
            out["gpus"] = [{k: v for k, v in (("name", g.name), ("mem_total", g.total), ("mem_used", g.used),
                                              ("util", g.util), ("temp", g.temp)) if v is not None} for g in gpus]
        if server_pid:
            found = process_info(server_pid, self.proc)
            if found is not None:
                server: JsonObject = {"pid": server_pid, "ram": found.rss}
                vram = parse_nvidia_apps(self.run([NVIDIA, *NVIDIA_APPS])).get(server_pid) if gpus else None
                if vram is not None:
                    server["vram"] = vram
                out["server"] = server
        return out
