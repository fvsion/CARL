"""Linux's listening ports and processes (implements PortProbe, contract C-017, and ProcessTable, C-018).

The ports: `ss -ltnpH` (iproute2: the owner's pid when the user may see it), else /proc/net/tcp and tcp6 (LISTEN is
state 0A; the owner from the socket's inode in /proc/PID/fd, for the user's own processes). The processes:
/proc/PID/stat (the name), /proc/PID/status (VmRSS) and /proc/PID/exe or cmdline (the program's path). The computer's
addresses are the socket library's (adapters/common/socket_addresses.py, one adapter for Windows and Linux).
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
from typing import Dict, List, Optional, Set

from ...domain.hardware.processes import ProcessInfo

SS_ARGS = ["ss", "-ltnpH"]
TIMEOUT = 10
PROC = "/proc"
LISTEN = "0A"
_SS_PORT = re.compile(r":(\d+)$")
_SS_PID = re.compile(r"pid=(\d+)")


def parse_ss(text: str) -> Dict[int, Optional[int]]:
    """port -> the first owner pid (None when ss could not tell) of `ss -ltnpH` output."""
    out: Dict[int, Optional[int]] = {}
    for line in text.splitlines():
        f = line.split()
        if len(f) < 4:
            continue
        m = _SS_PORT.search(f[3])
        if not m:
            continue
        port = int(m.group(1))
        pid = _SS_PID.search(line)
        if port not in out or out[port] is None:
            out[port] = int(pid.group(1)) if pid else None
    return out


def parse_proc_tcp(text: str) -> Dict[int, str]:
    """port -> the socket inode of the LISTEN rows of /proc/net/tcp or tcp6."""
    out: Dict[int, str] = {}
    for line in text.splitlines()[1:]:
        f = line.split()
        if len(f) < 10 or f[3] != LISTEN:
            continue
        try:
            port = int(f[1].rsplit(":", 1)[1], 16)
        except (IndexError, ValueError):
            continue
        out.setdefault(port, f[9])
    return out


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


class ProcPorts:
    """The TCP listeners of this computer."""

    def _ss(self) -> Optional[Dict[int, Optional[int]]]:
        try:
            p = subprocess.run(SS_ARGS, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                               text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            return None
        return parse_ss(p.stdout) if p.returncode == 0 else None

    def _proc(self) -> Dict[int, str]:
        out: Dict[int, str] = {}
        for name in ("tcp", "tcp6"):
            for port, inode in parse_proc_tcp(_read(os.path.join(PROC, "net", name))).items():
                out.setdefault(port, inode)
        return out

    def listening(self) -> Set[int]:
        ss = self._ss()
        return set(ss) if ss is not None else set(self._proc())

    def owner(self, port: int) -> Optional[int]:
        ss = self._ss()
        if ss is not None:
            return ss.get(port)
        inode = self._proc().get(port)
        return _inode_owner(inode) if inode else None

    def free(self, host: str, port: int) -> bool:
        with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET) as s:
            try:
                s.bind((host, port))
                return True
            except OSError:
                return False


def _inode_owner(inode: str) -> Optional[int]:
    """The pid whose fd is socket:[inode] (only the processes this user may look into)."""
    want = f"socket:[{inode}]"
    for pid in _pids():
        fd_dir = os.path.join(PROC, str(pid), "fd")
        try:
            for fd in os.listdir(fd_dir):
                if os.readlink(os.path.join(fd_dir, fd)) == want:
                    return pid
        except OSError:
            continue
    return None


def _pids() -> List[int]:
    try:
        return sorted(int(n) for n in os.listdir(PROC) if n.isdigit())
    except OSError:
        return []


def _command(pid: int) -> str:
    """The program's path (exe, else the first word of cmdline, else the name in stat)."""
    try:
        return os.readlink(os.path.join(PROC, str(pid), "exe"))
    except OSError:
        pass
    cmd = _read(os.path.join(PROC, str(pid), "cmdline")).split("\0", 1)[0]
    if cmd:
        return cmd
    stat = _read(os.path.join(PROC, str(pid), "stat"))
    return stat[stat.find("(") + 1:stat.rfind(")")] if "(" in stat else ""


def _rss_kib(pid: int) -> int:
    for line in _read(os.path.join(PROC, str(pid), "status")).splitlines():
        if line.startswith("VmRSS:"):
            f = line.split()
            return int(f[1]) if len(f) > 1 and f[1].isdigit() else 0
    return 0


class ProcTable:
    """The processes of this computer from /proc."""

    def processes(self) -> List[ProcessInfo]:
        out = []
        for pid in _pids():
            command = _command(pid)
            if command:
                out.append(ProcessInfo(pid, _rss_kib(pid), command))
        return out

    def alive(self, pid: int) -> bool:
        return os.path.isdir(os.path.join(PROC, str(pid)))
