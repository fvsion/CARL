"""This Windows computer and its processes, for the dashboard (W6.8): WindowsServerWatch (the ServerWatch port, C-038),
WindowsTcpTable (the TcpTable port, C-039) and WindowsSystemStats (the SystemStats port, C-033). Windows answers
through ctypes (Win32Reads: one object, so a test gives made-up answers on any OS), `nvidia-smi` gives an NVIDIA GPU's
use and heat, and the GPU inventory of the composition (D3DKMT and the GPU counters, any vendor) each GPU's memory and
what each process holds there. A call Windows does not answer gives nothing, never an error.

What the Mac's figures become here (PcStats; the dashboard's PC cards read them):
  memory in use     the RAM less the available memory (GlobalMemoryStatusEx), as Task Manager's In use
  standby files     the system cache (GetPerformanceInfo)
  page file         its size (the commit limit less the RAM) and its use ("\\Paging File(_Total)\\% Usage")
  CPU               the processor's busy share since the last sample, since boot for the first (GetSystemTimes);
                    Windows has no load average
  GPUs              each display adapter with its own memory: its memory in use (the GPU counters), its use and heat
                    (nvidia-smi, NVIDIA only); the server's part: the GPU memory of the processes the dashboard asked
                    about since the last sample (the server, and a router's model server)
  power             GetSystemPowerStatus ("AC", "AC 100%", "battery 54%")
  heat              none: Windows has no thermal state to read without a driver of its own

A process's command line is not readable on Windows: the command is its program's name and the arguments a start
recorded in server-PORT.json (processes.py, the ProcessTable adapter's rule). The computer is kept awake for a server
when the keep-awake child's state file names it, or the start that runs it, and that child runs (keep_awake.py).
"""
from __future__ import annotations

import ctypes
import json
import ntpath
import os
import shutil
import socket
import struct
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Sequence, Set, Tuple

from ...domain.hardware.devices import Gpu
from ...domain.server.snapshot import GpuUse, PcStats, ProcInfo, SlowStats, clean
from ...domain.shared.types import JsonObject
from ..common.key_store import read_key
from ..common.log_tail import LocalServerFiles
from . import gpu_counters, tcp_table
from .host import _MemoryStatus
from .keep_awake import state_file
from .process import FileTime, PROCESS_QUERY_LIMITED_INFORMATION, creation_time, is_alive, terminate
from .processes import INVALID_HANDLE, TH32CS_SNAPPROCESS, _Entry, command_text, recorded_argv, working_set_kib

NVIDIA = "nvidia-smi"
NVIDIA_ARGS = ["--query-gpu=pci.bus_id,name,memory.total,memory.used,utilization.gpu,temperature.gpu",
               "--format=csv,noheader,nounits"]
NVIDIA_EVERY = 5.0                       # seconds between two nvidia-smi runs (it takes a few tenths on Windows)
PAGE_FILE = "\\Paging File(*)\\% Usage"
STATE_LISTEN, STATE_ESTABLISHED = 2, 5   # MIB_TCP_STATE
WILDCARDS = ("0.0.0.0", "::")
PORT_SPAN = 100
MIB = 2 ** 20
FILETIME_EPOCH = 116444736000000000      # 1970-01-01 in 100 ns since 1601
NO_BATTERY, UNKNOWN_BATTERY = 128, 255   # SYSTEM_POWER_STATUS.BatteryFlag


def sh(cmd: Sequence[str], timeout: float = 5) -> str:
    """stdout of cmd, "" on error or timeout (no console window flashes up: CREATE_NO_WINDOW)."""
    try:
        p = subprocess.Popen(list(cmd), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                             text=True, encoding="utf-8", errors="replace", creationflags=0x08000000)
    except (OSError, ValueError):
        return ""
    try:
        out: str = p.communicate(timeout=timeout)[0]
        return out
    except subprocess.TimeoutExpired:
        p.kill()
        if p.stdout:
            p.stdout.close()
        threading.Thread(target=p.wait, daemon=True).start()
        return ""


# ---------------------------------------------------------------- sockets
class TcpRow(NamedTuple):
    """One TCP socket of GetExtendedTcpTable (or netstat)."""
    state: int                  # MIB_TCP_STATE (LISTEN 2, ESTABLISHED 5; netstat: 0 when it is not a listener)
    local_addr: str
    local_port: int
    remote_addr: str
    remote_port: int
    pid: int


def parse_all_v4(buf: bytes) -> List[TcpRow]:
    """The rows of a MIB_TCPTABLE_OWNER_PID (a count, then six DWORDs: state, local address, local port, remote
    address, remote port, pid; the addresses in network order)."""
    out: List[TcpRow] = []
    if len(buf) < 4:
        return out
    (count,) = struct.unpack_from("<I", buf, 0)
    for i in range(count):
        at = 4 + i * tcp_table.ROW_V4.size
        if at + tcp_table.ROW_V4.size > len(buf):
            break
        state, la, lp, ra, rp, pid = tcp_table.ROW_V4.unpack_from(buf, at)
        out.append(TcpRow(state, socket.inet_ntoa(struct.pack("<I", la)), tcp_table._port(lp),
                          socket.inet_ntoa(struct.pack("<I", ra)), tcp_table._port(rp), pid))
    return out


def _v6(raw: bytes) -> str:
    try:
        return socket.inet_ntop(socket.AF_INET6, raw)
    except (OSError, ValueError):
        return ""


def parse_all_v6(buf: bytes) -> List[TcpRow]:
    """The rows of a MIB_TCP6TABLE_OWNER_PID (a count, then rows of 56 bytes)."""
    out: List[TcpRow] = []
    if len(buf) < 4:
        return out
    (count,) = struct.unpack_from("<I", buf, 0)
    for i in range(count):
        at = 4 + i * tcp_table.ROW_V6.size
        if at + tcp_table.ROW_V6.size > len(buf):
            break
        la, _scope, lp, ra, _rscope, rp, state, pid = tcp_table.ROW_V6.unpack_from(buf, at)
        out.append(TcpRow(state, _v6(la), tcp_table._port(lp), _v6(ra), tcp_table._port(rp), pid))
    return out


def split_address(text: str) -> Tuple[str, int]:
    """(address, port) of netstat's "127.0.0.1:8080" and "[::1]:8080" (a port that is not a number is 0)."""
    host, _, port = text.rpartition(":")
    return host.strip("[]"), int(port) if port.isdigit() else 0


def parse_netstat_all(text: str) -> List[TcpRow]:
    """The rows of `netstat -ano -p TCP` (and TCPv6). The state word is in the system's language, so a listener is a
    row whose remote port is 0 and every other row counts as a connection (ESTABLISHED)."""
    out: List[TcpRow] = []
    for line in text.splitlines():
        f = line.split()
        if len(f) < 5 or f[0].upper() != "TCP" or not f[-1].isdigit():
            continue
        (la, lp), (ra, rp) = split_address(f[1]), split_address(f[2])
        out.append(TcpRow(STATE_LISTEN if rp == 0 else STATE_ESTABLISHED, la, lp, ra, rp, int(f[-1])))
    return out


class WindowsTcpTable:
    """The TCP sockets of one read (implements TcpTable, contract C-039)."""

    def __init__(self, rows: Sequence[TcpRow]) -> None:
        self.rows = list(rows)

    def listen_host(self, port: int) -> Optional[str]:
        for r in self.rows:
            if r.local_port == port and r.state == STATE_LISTEN:
                return "127.0.0.1" if r.local_addr in WILDCARDS else r.local_addr
        return None

    def listen_pid(self, port: int) -> Optional[int]:
        return next((r.pid for r in self.rows if r.local_port == port and r.state == STATE_LISTEN and r.pid), None)

    def connections(self, port: int, pid: int) -> List[Tuple[str, str]]:
        return [(r.remote_addr, str(r.remote_port)) for r in self.rows
                if r.local_port == port and r.state == STATE_ESTABLISHED and r.pid == pid]

    def next_free_port(self, port: int) -> Optional[int]:
        taken = {r.local_port for r in self.rows if r.state == STATE_LISTEN}
        return next((p for p in range(port + 1, min(port + PORT_SPAN, 65535) + 1) if p not in taken), None)


# ---------------------------------------------------------------- the pure parts
def etime_text(seconds: float) -> str:
    """A run time as ps writes it: [[dd-]hh:]mm:ss."""
    s = max(int(seconds), 0)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d}-{h:02d}:{m:02d}:{s:02d}"
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def filetime_epoch(ft: int) -> float:
    """A FILETIME (100 ns since 1601) as epoch seconds."""
    return (ft - FILETIME_EPOCH) / 1e7


def power_text(ac: int, flag: int, percent: int) -> str:
    """"AC", "AC 100%" or "battery 54%" of SYSTEM_POWER_STATUS (no battery, or none Windows knows of: "AC")."""
    if flag in (NO_BATTERY, UNKNOWN_BATTERY):
        return "AC"
    level = f" {percent}%" if 0 <= percent <= 100 else ""
    return ("battery" if ac == 0 else "AC") + level


def cpu_share(before: Optional[Tuple[int, int]], now: Tuple[int, int]) -> Optional[float]:
    """The busy share (percent) between two (busy, total) readings of GetSystemTimes; for the first, since boot (as
    LinuxSystemStats gives it); None when the times did not move."""
    before = before or (0, 0)
    if now[1] <= before[1]:
        return None
    return round(max(now[0] - before[0], 0) / (now[1] - before[1]) * 100, 1)


def _int(text: str) -> Optional[int]:
    try:
        return int(float(text))
    except ValueError:
        return None


def pci_tail(text: str) -> str:
    """The bus, device and function of a PCI address in one form ("01:00.0"): nvidia-smi writes 00000000:01:00.0,
    the inventory 0000:01:00.0."""
    parts = text.strip().lower().split(":")
    return ":".join(parts[-2:]) if len(parts) >= 2 else ""


class NvidiaRow(NamedTuple):
    pci: str
    name: str
    total: Optional[int]        # bytes
    used: Optional[int]
    util: Optional[int]         # percent
    temp: Optional[int]         # degrees C


def parse_nvidia(text: str) -> List[NvidiaRow]:
    """`nvidia-smi --query-gpu=pci.bus_id,name,memory.total,memory.used,utilization.gpu,temperature.gpu` (csv, MiB;
    "[N/A]" is None)."""
    out: List[NvidiaRow] = []
    for line in text.splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) < 6 or not pci_tail(f[0]):
            continue
        total, used = _int(f[2]), _int(f[3])
        out.append(NvidiaRow(pci_tail(f[0]), f[1], total * MIB if total is not None else None,
                             used * MIB if used is not None else None, _int(f[4]), _int(f[5])))
    return out


def gpu_uses(gpus: Sequence[Gpu], nvidia: Sequence[NvidiaRow]) -> List[GpuUse]:
    """Each GPU with its own memory (the inventory's, any vendor), its use and heat from nvidia-smi by PCI address;
    nvidia-smi's GPUs alone when the inventory has none."""
    by_pci = {r.pci: r for r in nvidia}
    out: List[GpuUse] = []
    for g in gpus:
        r = by_pci.get(pci_tail(g.pci)) if g.pci else None
        used = (g.total - g.free) if g.free is not None else (r.used if r else None)
        out.append(GpuUse(g.name, g.total, used, r.util if r else None, r.temp if r else None, g.luid))
    if not out:
        out = [GpuUse(r.name, r.total, r.used, r.util, r.temp) for r in nvidia if r.total]
    return out


def server_share(vram: Dict[int, Dict[str, int]], pids: Set[int]) -> Dict[str, int]:
    """What these processes hold on each GPU, by the GPU's key."""
    out: Dict[str, int] = {}
    for pid in pids:
        for key, n in vram.get(pid, {}).items():
            out[key] = out.get(key, 0) + n
    return out


def pc_stats(mem: Tuple[int, int], perf: Tuple[int, int, int], page_used_pct: Optional[float],
             cpu: Optional[float], gpus: List[GpuUse], server: Dict[str, int], power: str) -> PcStats:
    """The figures as PcStats. mem: (RAM, available); perf: (commit limit, physical total, system cache), bytes."""
    total, avail = mem
    limit, physical, cache = perf
    page = max(limit - physical, 0)
    used_page = page * page_used_pct / 100 if page_used_pct is not None else 0.0
    utils = [g.util for g in gpus if g.util is not None]
    used = [g.used for g in gpus if g.used is not None]
    return PcStats(anon=max(total - avail, 0), free=avail, files=cache, swap=(float(used_page), float(page)),
                   gpu=max(utils) if utils else None, gpumem=sum(used) if used else None, power=power,
                   available=avail, cpu=cpu, gpus=gpus, server=server)


# ---------------------------------------------------------------- Windows
class _PerfInfo(ctypes.Structure):           # PERFORMANCE_INFORMATION
    _fields_ = [("cb", ctypes.c_uint32)] + [(n, ctypes.c_size_t) for n in (
        "commit_total", "commit_limit", "commit_peak", "physical_total", "physical_available", "system_cache",
        "kernel_total", "kernel_paged", "kernel_nonpaged", "page_size")] + [
        (n, ctypes.c_uint32) for n in ("handles", "processes", "threads")]


class _PowerStatus(ctypes.Structure):        # SYSTEM_POWER_STATUS
    _fields_ = [("ac", ctypes.c_ubyte), ("flag", ctypes.c_ubyte), ("percent", ctypes.c_ubyte),
                ("saver", ctypes.c_ubyte), ("life", ctypes.c_uint32), ("full_life", ctypes.c_uint32)]


def _k32() -> Any:
    return ctypes.WinDLL("kernel32", use_last_error=True)          # type: ignore[attr-defined]


class Win32Reads:
    """The calls to Windows the dashboard reads (each one: a value, or None / empty when Windows does not answer)."""

    def memory(self) -> Optional[Tuple[int, int]]:
        """(RAM, available) bytes."""
        try:
            st = _MemoryStatus()
            st.length = ctypes.sizeof(_MemoryStatus)
            return (int(st.total_phys), int(st.avail_phys)) if _k32().GlobalMemoryStatusEx(ctypes.byref(st)) else None
        except (AttributeError, OSError):
            return None

    def performance(self) -> Optional[Tuple[int, int, int]]:
        """(commit limit, physical total, system cache) bytes."""
        try:
            k = _k32()
            k.K32GetPerformanceInfo.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            p = _PerfInfo()
            p.cb = ctypes.sizeof(_PerfInfo)
            if not k.K32GetPerformanceInfo(ctypes.byref(p), p.cb):
                return None
            return (p.commit_limit * p.page_size, p.physical_total * p.page_size, p.system_cache * p.page_size)
        except (AttributeError, OSError):
            return None

    def page_file_use(self) -> Optional[float]:
        """The page file's use in percent (its _Total instance)."""
        return gpu_counters.counter_values(PAGE_FILE, double=True).get("_Total")

    def cpu_times(self) -> Optional[Tuple[int, int]]:
        """(busy, total) of GetSystemTimes, 100 ns (the kernel time holds the idle time)."""
        try:
            idle, kern, user = FileTime(), FileTime(), FileTime()
            if not _k32().GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):
                return None
        except (AttributeError, OSError):
            return None
        total = kern.value() + user.value()
        return total - idle.value(), total

    def power(self) -> Optional[Tuple[int, int, int]]:
        """(AC line, battery flag, battery percent) of GetSystemPowerStatus."""
        try:
            st = _PowerStatus()
            return (st.ac, st.flag, st.percent) if _k32().GetSystemPowerStatus(ctypes.byref(st)) else None
        except (AttributeError, OSError):
            return None

    def tcp_rows(self) -> List[TcpRow]:
        """Every TCP socket and its owner: GetExtendedTcpTable, else netstat."""
        v4 = tcp_table._table(tcp_table.AF_INET, tcp_table.TCP_TABLE_OWNER_PID_ALL)
        v6 = tcp_table._table(tcp_table.AF_INET6, tcp_table.TCP_TABLE_OWNER_PID_ALL)
        if v4 is None and v6 is None:
            return parse_netstat_all(sh(tcp_table.ARGS) + "\n" + sh(tcp_table.ARGS6))
        return parse_all_v4(v4 or b"") + parse_all_v6(v6 or b"")

    def image_name(self, pid: int) -> str:
        """The process's program file name ("" when it cannot be opened)."""
        try:
            k = _k32()
            k.OpenProcess.restype = ctypes.c_void_p
            k.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
            k.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p,
                                                     ctypes.POINTER(ctypes.c_uint32)]
            k.CloseHandle.argtypes = [ctypes.c_void_p]
            h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        except (AttributeError, OSError):
            return ""
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_uint32(len(buf))
            return ntpath.basename(buf.value) if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)) else ""
        finally:
            k.CloseHandle(h)

    def cpu_seconds(self, pid: int) -> Optional[float]:
        """The process's kernel and user time, seconds."""
        try:
            k = _k32()
            k.OpenProcess.restype = ctypes.c_void_p
            k.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
            k.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.c_void_p] * 4
            k.CloseHandle.argtypes = [ctypes.c_void_p]
            h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        except (AttributeError, OSError):
            return None
        if not h:
            return None
        try:
            c, e, kern, user = FileTime(), FileTime(), FileTime(), FileTime()
            if not k.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kern), ctypes.byref(user)):
                return None
            return (kern.value() + user.value()) / 1e7
        finally:
            k.CloseHandle(h)

    def created(self, pid: int) -> Optional[int]:
        return creation_time(pid)

    def parent(self, pid: int) -> Optional[int]:
        """The pid of the process that started pid (a Toolhelp snapshot); None when it is not found."""
        try:
            k = _k32()
            k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
            k.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
            k.Process32FirstW.argtypes = k.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            k.CloseHandle.argtypes = [ctypes.c_void_p]
            h = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        except (AttributeError, OSError):
            return None
        if not h or h == INVALID_HANDLE:
            return None
        try:
            e = _Entry()
            e.size = ctypes.sizeof(_Entry)
            ok = k.Process32FirstW(h, ctypes.byref(e))
            while ok:
                if int(e.pid) == pid:
                    return int(e.parent)
                ok = k.Process32NextW(h, ctypes.byref(e))
        finally:
            k.CloseHandle(h)
        return None

    def working_set(self, pid: int) -> int:
        return working_set_kib(pid) * 1024

    def alive(self, pid: int) -> bool:
        return is_alive(pid)

    def terminate(self, pid: int) -> None:
        terminate(pid)


def no_gpus() -> Tuple[List[Gpu], Dict[int, Dict[str, int]]]:
    return [], {}


class _Reader:
    """The figures both ports read: the memory, the CPU, the GPUs (nvidia-smi at most every NVIDIA_EVERY s)."""

    def __init__(self, win: Win32Reads, gpus: Callable[[], Tuple[List[Gpu], Dict[int, Dict[str, int]]]],
                 run: Callable[[Sequence[str]], str], clock: Callable[[], float]) -> None:
        self.win, self.gpus, self.run, self.clock = win, gpus, run, clock
        self._cpu: Optional[Tuple[int, int]] = None
        self._nvidia: Tuple[float, List[NvidiaRow]] = (-NVIDIA_EVERY, [])

    def cpu(self) -> Optional[float]:
        now = self.win.cpu_times()
        if now is None:
            return None
        share, self._cpu = cpu_share(self._cpu, now), now
        return share

    def nvidia(self) -> List[NvidiaRow]:
        at, rows = self._nvidia
        if self.clock() - at >= NVIDIA_EVERY:            # not installed: the run gives "" (no NVIDIA GPU)
            rows = parse_nvidia(self.run([NVIDIA, *NVIDIA_ARGS]))
            self._nvidia = (self.clock(), rows)
        return rows

    def stats(self, pids: Set[int]) -> PcStats:
        found, vram = self.gpus()
        p = self.win.power()
        return pc_stats(self.win.memory() or (0, 0), self.win.performance() or (0, 0, 0), self.win.page_file_use(),
                        self.cpu(), gpu_uses(found, self.nvidia()), server_share(vram, pids),
                        power_text(*p) if p else "")


class WindowsServerWatch:
    """What the dashboard reads of this computer and of the server it watches (implements ServerWatch, contract
    C-038). conf_dir: the settings folder (the servers' recorded argv); logs_dir: ~/models/logs (the keep-awake state
    files); gpus: the GPU inventory's GPUs and what each process holds there (pid -> {LUID: bytes}), from the
    composition; win, run, clock: Windows' answers, how a program runs, the time (a test gives made-up ones)."""

    def __init__(self, page: int = 4096, conf_dir: str = "", logs_dir: str = "",
                 gpus: Callable[[], Tuple[List[Gpu], Dict[int, Dict[str, int]]]] = no_gpus,
                 win: Optional[Win32Reads] = None, run: Callable[[Sequence[str]], str] = sh,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.page, self.conf_dir, self.logs_dir = page, conf_dir, logs_dir
        self.win = win or Win32Reads()
        self._read = _Reader(self.win, gpus, run, clock)
        self._asked: Set[int] = set()            # the processes asked about since the last memory()

    def sockets(self) -> WindowsTcpTable:
        return WindowsTcpTable(self.win.tcp_rows())

    def process(self, pid: int) -> Optional[ProcInfo]:
        """The working set, the CPU share since the process started (as ps counts it), the run time and the
        command (the program's name and the arguments a start recorded)."""
        created = self.win.created(pid)
        name = self.win.image_name(pid)
        if created is None or not name:
            return None
        self._asked.add(pid)
        elapsed = max(time.time() - filetime_epoch(created), 0.0)
        cpu_s = self.win.cpu_seconds(pid)
        cpu = round(cpu_s / elapsed * 100, 1) if cpu_s is not None and elapsed > 0 else 0.0
        rec = recorded_argv(self.conf_dir).get(pid, [])
        same = bool(rec) and ntpath.basename(rec[0]).lower() == name.lower()      # not a pid Windows reused
        return ProcInfo(rss=self.win.working_set(pid), cpu=cpu, etime=etime_text(elapsed),
                        cmd=clean(command_text(name, rec[1:] if same else [])))

    def alive(self, pid: Optional[int]) -> bool:
        return bool(pid) and self.win.alive(int(pid or 0))

    def kept_awake(self, pid: int) -> bool:
        """The keep-awake child's state file names pid, or the start that runs it (a start in this process, MONITOR=0,
        stays the server's parent on Windows and holds for itself), and that child runs."""
        if self._holds(pid):
            return True
        parent = self.win.parent(pid)
        return bool(parent) and self._holds(int(parent or 0))

    def _holds(self, pid: int) -> bool:
        try:
            with open(state_file(self.logs_dir, pid), encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            return False
        holder = doc.get("pid") if isinstance(doc, dict) else None
        return doc.get("server") == pid and isinstance(holder, int) and self.win.alive(holder)

    def kill(self, pid: int) -> None:
        self.win.terminate(pid)

    def memory(self) -> PcStats:
        pids, self._asked = self._asked, set()
        return self._read.stats(pids)

    def slow(self, server_pid: Callable[[], Optional[int]], disk_path: str) -> SlowStats:
        """The free disk (no thermal state and no sleep record to read)."""
        try:
            du = shutil.disk_usage(disk_path)
            disk: Optional[Tuple[int, int]] = (du.free, du.total)
        except OSError:
            disk = None
        return SlowStats(thermal="", sleep_events=[], disk=disk)

    def read_key(self, path: str) -> str:
        return read_key(path)

    def files(self) -> LocalServerFiles:
        return LocalServerFiles()


class WindowsSystemStats:
    """One sample of this computer's state (implements SystemStats, contract C-033), with the fields of
    LinuxSystemStats: ram_total, ram_used, ram_available, swap_total, swap_used (the page file), cpu_percent (since
    the last sample, since boot for the first), power; gpus: a list of {name, mem_total, mem_used, util, temp}; server
    (when a pid is given and readable): {pid, ram, vram}. A field it can't tell is left out."""

    def __init__(self, gpus: Callable[[], Tuple[List[Gpu], Dict[int, Dict[str, int]]]] = no_gpus,
                 win: Optional[Win32Reads] = None, run: Callable[[Sequence[str]], str] = sh,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.win = win or Win32Reads()
        self._read = _Reader(self.win, gpus, run, clock)

    def sample(self, server_pid: Optional[int]) -> JsonObject:
        s = self._read.stats({server_pid} if server_pid else set())
        out: JsonObject = {}
        mem = self.win.memory()
        if mem:
            out.update(ram_total=mem[0], ram_used=s.used, ram_available=s.available)
        if s.swap[1]:
            out.update(swap_total=int(s.swap[1]), swap_used=int(s.swap[0]))
        if s.cpu is not None:
            out["cpu_percent"] = s.cpu
        if s.power:
            out["power"] = s.power
        if s.gpus:
            out["gpus"] = [{k: v for k, v in (("name", g.name), ("mem_total", g.total), ("mem_used", g.used),
                                              ("util", g.util), ("temp", g.temp)) if v is not None} for g in s.gpus]
        if server_pid and self.win.created(server_pid) is not None:
            server: JsonObject = {"pid": server_pid, "ram": self.win.working_set(server_pid)}
            if s.server:
                server["vram"] = sum(s.server.values())
            out["server"] = server
        return out
