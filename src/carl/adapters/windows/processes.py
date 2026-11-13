"""The processes of a Windows computer (implements ProcessTable, contract C-018): a Toolhelp snapshot (every pid and
its program's name), each one's working set (GetProcessMemoryInfo), else `tasklist /fo csv /nh`. Windows does not
show another process's command line, so a process's command is its program's name, followed by its arguments for a
server CARL started (server-PORT.json's "argv", which the Windows runner writes).

resident=False gives no resident sizes: on a discrete GPU the second-model guard goes by the known servers' names,
and a second model shows as less free VRAM in the memory check (domain/hardware/processes.py), so a large program
that holds no model (a training job) does not stop a start. parse_tasklist is pure and takes the command's text.
"""
from __future__ import annotations

import ctypes
import glob
import json
import ntpath
import os
import subprocess
from typing import Dict, List, Optional, Sequence, Tuple

from ...domain.hardware.processes import ProcessInfo
from .process import PROCESS_QUERY_LIMITED_INFORMATION, is_alive

ARGS = ["tasklist", "/fo", "csv", "/nh"]
TH32CS_SNAPPROCESS = 0x00000002
MAX_PATH = 260
INVALID_HANDLE = ctypes.c_void_p(-1).value
TIMEOUT = 15


def parse_tasklist(csv_output: str) -> List[Tuple[str, int]]:
    """[(image name, pid)] ("llama-server.exe","14240","Console","1","1,074 K")."""
    out: List[Tuple[str, int]] = []
    for line in csv_output.splitlines():
        f = [x.strip().strip('"') for x in line.split('","')]
        if len(f) >= 2 and f[1].isdigit():
            out.append((f[0].lstrip('"'), int(f[1])))
    return out


def parse_tasklist_kib(csv_output: str) -> Dict[int, int]:
    """{pid: memory in KiB} of tasklist's CSV (its last field, "1,074 K"; the separators are the locale's)."""
    out: Dict[int, int] = {}
    for line in csv_output.splitlines():
        f = [x.strip().strip('"') for x in line.split('","')]
        if len(f) >= 5 and f[1].isdigit():
            digits = "".join(c for c in f[4] if c.isdigit())
            if digits:
                out[int(f[1])] = int(digits)
    return out


def command_text(name: str, args: Sequence[str]) -> str:
    """A process's command: its program's name, then the arguments a start recorded (quoted as Windows quotes them)."""
    return name if not args else f"{name} {subprocess.list2cmdline(list(args))}"


def recorded_argv(conf_dir: str) -> Dict[int, List[str]]:
    """{pid: argv} of the servers CARL started, from the settings folder's server-PORT.json files."""
    out: Dict[int, List[str]] = {}
    if not conf_dir:
        return out
    for path in glob.glob(os.path.join(glob.escape(conf_dir), "server-*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
            pid, argv = doc.get("pid"), doc.get("argv")
            if isinstance(pid, int) and isinstance(argv, list):
                out[pid] = [str(a) for a in argv]
        except (OSError, ValueError, AttributeError):
            continue
    return out


class _Entry(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("usage", ctypes.c_uint32), ("pid", ctypes.c_uint32),
                ("heap", ctypes.c_size_t), ("module", ctypes.c_uint32), ("threads", ctypes.c_uint32),
                ("parent", ctypes.c_uint32), ("priority", ctypes.c_long), ("flags", ctypes.c_uint32),
                ("exe", ctypes.c_wchar * MAX_PATH)]


class _Memory(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_uint32), ("faults", ctypes.c_uint32)] + [
        (n, ctypes.c_size_t) for n in ("peak_ws", "ws", "peak_paged", "paged", "peak_nonpaged", "nonpaged",
                                       "pagefile", "peak_pagefile")]


def snapshot() -> Optional[List[Tuple[str, int]]]:
    """[(program name, pid)] from a Toolhelp snapshot; None when it cannot be taken."""
    try:
        k = ctypes.WinDLL("kernel32", use_last_error=True)          # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return None
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    k.Process32FirstW.argtypes = k.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    h = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not h or h == INVALID_HANDLE:
        return None
    out: List[Tuple[str, int]] = []
    try:
        e = _Entry()
        e.size = ctypes.sizeof(_Entry)
        ok = k.Process32FirstW(h, ctypes.byref(e))
        while ok:
            out.append((e.exe, int(e.pid)))
            ok = k.Process32NextW(h, ctypes.byref(e))
    finally:
        k.CloseHandle(h)
    return out


def working_set_kib(pid: int) -> int:
    """The process's working set in KiB (0 when it cannot be read)."""
    try:
        k = ctypes.WinDLL("kernel32", use_last_error=True)          # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return 0
    k.OpenProcess.restype = ctypes.c_void_p
    k.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    k.K32GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return 0
    try:
        m = _Memory()
        m.cb = ctypes.sizeof(_Memory)
        return int(m.ws) // 1024 if k.K32GetProcessMemoryInfo(h, ctypes.byref(m), m.cb) else 0
    finally:
        k.CloseHandle(h)


class WindowsProcessTable:
    """The processes of this computer. conf_dir: the settings folder (the servers' recorded argv); resident: report
    each process's working set (False: 0, for the discrete GPU's guard by name)."""

    def __init__(self, conf_dir: str = "", resident: bool = True) -> None:
        self.conf_dir, self.resident = conf_dir, resident

    def processes(self) -> List[ProcessInfo]:
        found = snapshot()
        sizes: Dict[int, int] = {}
        if found is None:
            try:
                text = subprocess.run(ARGS, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                      timeout=TIMEOUT).stdout
            except (OSError, subprocess.SubprocessError):
                return []
            found, sizes = parse_tasklist(text), parse_tasklist_kib(text)
        argv = recorded_argv(self.conf_dir)
        out = []
        for name, pid in found:
            if pid == 0:
                continue                                 # the idle process
            kib = (sizes.get(pid) or working_set_kib(pid)) if self.resident else 0
            rec = argv.get(pid, [])
            same = bool(rec) and ntpath.basename(rec[0]).lower() == name.lower()     # not a pid Windows reused
            out.append(ProcessInfo(pid, kib, command_text(name, rec[1:] if same else [])))
        return out

    def alive(self, pid: int) -> bool:
        return is_alive(pid)
