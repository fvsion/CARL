"""Starting, waiting for and stopping the processes CARL owns on Windows (implements ProcessControl, contract C-019),
and the Win32 calls the other Windows adapters share. Windows has no process groups to signal, so:

  start   a process CARL starts gets a hidden console of its own (CREATE_NO_WINDOW). Never CREATE_NEW_PROCESS_GROUP,
          whose processes ignore Ctrl-C, nor DETACHED_PROCESS, which has no console for a console event to reach.
          CREATE_BREAKAWAY_FROM_JOB takes it out of the caller's job (an ssh session's), so it outlives the session;
          when the job forbids that, the start goes on and says once that it ends with the session. Before it starts
          one, CARL clears the "ignore Ctrl-C" flag a process inherits (an ssh session sets it), because the child
          inherits it too and the console stop would then do nothing (the stop experiment, 2026-10-26).
  stop    the stop helper (stop_helper.py) sends Ctrl-C to the process's console, so llama-server ends cleanly; after
          the timeout, TerminateProcess (decision 7).
  alive   a process handle and the creation time: a pid that Windows gave to a new process is not the old one.
  trees   a Job object that ends what is in it when CARL's handle closes (the foreground start: the server and its
          filter end with CARL, as the server is CARL's process on macOS and Linux).

Every Win32 call is made through ctypes when it runs, never at import (rule R4: the module imports on every OS).
"""
from __future__ import annotations

import ctypes
import subprocess
import time
from typing import IO, Any, Callable, Dict, Mapping, Optional, Sequence, Union

from ...domain.shared.contracts import require
from . import stop_helper

CREATE_NO_WINDOW = 0x08000000
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NEW_PROCESS_GROUP = 0x00000200         # never used: its processes ignore Ctrl-C
DETACHED_PROCESS = 0x00000008                 # never used: no console, so no console event reaches it
ERROR_ACCESS_DENIED = 5
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x102
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
POLL = 0.05
KILL_WAIT = 5.0                               # seconds for a process to go after TerminateProcess
BREAKAWAY_NOTE = ("note: this session does not let a program outlive it, so the server ends when this session "
                  "ends.")

Std = Union[int, IO[Any], None]


class FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

    def value(self) -> int:
        return (self.high << 32) | self.low


class _Counters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes",
                                               "other_bytes")]


class _BasicLimits(ctypes.Structure):
    _fields_ = [("per_process_time", ctypes.c_int64), ("per_job_time", ctypes.c_int64),
                ("flags", ctypes.c_uint32), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
                ("active", ctypes.c_uint32), ("affinity", ctypes.c_size_t), ("priority", ctypes.c_uint32),
                ("scheduling", ctypes.c_uint32)]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("basic", _BasicLimits), ("io", _Counters), ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]


_K32: Optional[Any] = None


def kernel32() -> Any:
    """kernel32 with the signatures the Windows adapters use (loaded at the first call, on Windows only)."""
    global _K32
    if _K32 is None:
        k = ctypes.WinDLL("kernel32", use_last_error=True)          # type: ignore[attr-defined]
        h, dw, b, vp = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_void_p
        for name, res, args in (
                ("OpenProcess", h, [dw, b, dw]), ("CloseHandle", b, [h]),
                ("GetProcessTimes", b, [h, vp, vp, vp, vp]), ("WaitForSingleObject", dw, [h, dw]),
                ("GetExitCodeProcess", b, [h, vp]), ("TerminateProcess", b, [h, ctypes.c_uint]),
                ("SetConsoleCtrlHandler", b, [vp, b]), ("CreateJobObjectW", h, [vp, ctypes.c_wchar_p]),
                ("SetInformationJobObject", b, [h, ctypes.c_int, vp, dw]), ("AssignProcessToJobObject", b, [h, h]),
                ("TerminateJobObject", b, [h, ctypes.c_uint]), ("GetShortPathNameW", dw,
                                                                [ctypes.c_wchar_p, ctypes.c_wchar_p, dw])):
            fn = getattr(k, name)
            fn.restype, fn.argtypes = res, args
        _K32 = k
    return _K32


def _open(pid: int, access: int) -> Optional[int]:
    """A handle to the process, None when it cannot be opened (gone, or not ours to open)."""
    try:
        handle = kernel32().OpenProcess(access, False, pid)
    except (AttributeError, OSError):
        return None
    return handle or None


def creation_time(pid: int) -> Optional[int]:
    """The process's creation time (100 ns since 1601, a FILETIME), None when it cannot be read."""
    h = _open(pid, PROCESS_QUERY_LIMITED_INFORMATION)
    if h is None:
        return None
    k = kernel32()
    try:
        created, ended, kern, user = FileTime(), FileTime(), FileTime(), FileTime()
        if not k.GetProcessTimes(h, ctypes.byref(created), ctypes.byref(ended), ctypes.byref(kern),
                                 ctypes.byref(user)):
            return None
        return created.value()
    finally:
        k.CloseHandle(h)


def is_alive(pid: int, created: Optional[int] = None) -> bool:
    """The process runs, and, when created is given, it is the one created then (not a reused pid). A process CARL
    may not open (another user's) counts as running."""
    if pid <= 0:
        return False
    h = _open(pid, PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE)
    if h is None:
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED   # type: ignore[attr-defined]
    k = kernel32()
    try:
        if k.WaitForSingleObject(h, 0) != WAIT_TIMEOUT:
            return False
    finally:
        k.CloseHandle(h)
    return created is None or creation_time(pid) in (None, created)


def terminate(pid: int, created: Optional[int] = None) -> bool:
    """TerminateProcess, when the pid is still the process created then; True when it was asked to end."""
    if created is not None and creation_time(pid) not in (None, created):
        return False
    h = _open(pid, PROCESS_TERMINATE)
    if h is None:
        return False
    k = kernel32()
    try:
        return bool(k.TerminateProcess(h, 1))
    finally:
        k.CloseHandle(h)


def clear_ctrl_c_ignore() -> None:
    """Handle Ctrl-C again in this process (SetConsoleCtrlHandler(NULL, FALSE)): the processes it starts inherit it,
    so the stop helper's Ctrl-C reaches them. Python's own Ctrl-C handler stays."""
    try:
        kernel32().SetConsoleCtrlHandler(None, False)
    except (AttributeError, OSError):
        pass


def short_path(path: str) -> str:
    """The 8.3 short form of an existing path (GetShortPathNameW); the path itself when there is none (8.3 names
    turned off on the volume) or it cannot be read."""
    try:
        k = kernel32()
        size = k.GetShortPathNameW(path, None, 0)
        if not size:
            return path
        buf = ctypes.create_unicode_buffer(size)
        return buf.value if k.GetShortPathNameW(path, buf, size) else path
    except (AttributeError, OSError):
        return path


class Job:
    """A Job object whose processes end when its last handle closes (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE): the
    foreground start's server and filter, so they end with CARL. No job when Windows refuses one (it says nothing:
    the server then outlives CARL, as before)."""

    def __init__(self) -> None:
        self.handle: Optional[int] = None
        try:
            k = kernel32()
            h = k.CreateJobObjectW(None, None)
            if not h:
                return
            info = _ExtendedLimits()
            info.basic.flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if k.SetInformationJobObject(h, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info),
                                         ctypes.sizeof(info)):
                self.handle = h
            else:
                k.CloseHandle(h)
        except (AttributeError, OSError):
            self.handle = None

    def add(self, pid: int) -> bool:
        if self.handle is None:
            return False
        h = _open(pid, PROCESS_SET_QUOTA | PROCESS_TERMINATE)
        if h is None:
            return False
        k = kernel32()
        try:
            return bool(k.AssignProcessToJobObject(self.handle, h))
        finally:
            k.CloseHandle(h)

    def end(self) -> None:
        """End every process in the job, and close it."""
        if self.handle is not None:
            k = kernel32()
            k.TerminateJobObject(self.handle, 1)
            k.CloseHandle(self.handle)
            self.handle = None

    def close(self) -> None:
        """Close the job's handle (what is in it ends)."""
        if self.handle is not None:
            kernel32().CloseHandle(self.handle)
            self.handle = None


def creation_flags(hidden: bool, breakaway: bool) -> int:
    """The creation flags of a process CARL starts: a hidden console of its own, out of the caller's job. Never
    CREATE_NEW_PROCESS_GROUP or DETACHED_PROCESS (the module's note)."""
    return (CREATE_NO_WINDOW if hidden else 0) | (CREATE_BREAKAWAY_FROM_JOB if breakaway else 0)


def popen(argv: Sequence[str], env: Optional[Mapping[str, str]], stdin: Std, stdout: Std, stderr: Std,
          hidden: bool = True, breakaway: bool = True, note: Optional[Callable[[str], None]] = None,
          ) -> "subprocess.Popen[bytes]":
    """Start argv: with a hidden console of its own (hidden), out of the caller's job (breakaway). When the job
    forbids breakaway, it starts in the job, and note gets BREAKAWAY_NOTE."""
    flags = creation_flags(hidden, breakaway)
    clear_ctrl_c_ignore()
    kw: Dict[str, Any] = dict(env=None if env is None else dict(env), stdin=stdin, stdout=stdout, stderr=stderr)
    try:
        return subprocess.Popen(list(argv), creationflags=flags, **kw)
    except OSError as e:
        if not breakaway or getattr(e, "winerror", None) != ERROR_ACCESS_DENIED:
            raise
    if note is not None:
        note(BREAKAWAY_NOTE)
    return subprocess.Popen(list(argv), creationflags=flags & ~CREATE_BREAKAWAY_FROM_JOB, **kw)


class WindowsProcesses:
    """spawn, wait and stop on Windows. started: the creation time of each pid a start recorded (server-PORT.json's
    "started"), so a stop never ends a process that took over a pid; helper: sends Ctrl-C to a process's console
    (the stop helper in a child Python); note: where the one breakaway note goes."""

    def __init__(self, started: Optional[Callable[[int], Optional[int]]] = None,
                 helper: Optional[Callable[[int], bool]] = None, note: Optional[Callable[[str], None]] = None) -> None:
        self.children: Dict[int, "subprocess.Popen[bytes]"] = {}
        self.created: Dict[int, int] = {}
        self.started = started
        self.helper = helper or stop_helper.send_ctrl_c
        self.note = note

    def spawn(self, argv: Sequence[str], env: Mapping[str, str], log_path: str, new_session: bool) -> int:
        with open(log_path, "ab") as log:
            p = popen(argv, env, subprocess.DEVNULL, log, subprocess.STDOUT, breakaway=new_session, note=self.note)
        self.children[p.pid] = p
        c = creation_time(p.pid)
        if c is not None:
            self.created[p.pid] = c
        return p.pid

    def wait(self, pid: int, timeout: float) -> Optional[int]:
        require(pid > 0, "C-019", "the pid of a process CARL started")
        child = self.children.get(pid)
        if child is not None:
            try:
                return child.wait(timeout)
            except subprocess.TimeoutExpired:
                return None
        end = time.monotonic() + timeout
        while self.alive(pid):
            if time.monotonic() >= end:
                return None
            time.sleep(POLL)
        return 0                                        # not ours to read: gone

    def stop(self, pid: int, timeout: float) -> bool:
        """Ctrl-C to the process's console (llama-server ends cleanly), then TerminateProcess after timeout."""
        require(pid > 0, "C-019", "the pid of a process CARL started")
        if not self.alive(pid):
            return True
        self.helper(pid)
        if self._gone_within(pid, timeout):
            return True
        terminate(pid, self._created(pid))
        return self._gone_within(pid, KILL_WAIT)

    def alive(self, pid: int) -> bool:
        child = self.children.get(pid)
        if child is not None:
            return child.poll() is None
        return is_alive(pid, self._created(pid))

    def _created(self, pid: int) -> Optional[int]:
        if pid in self.created:
            return self.created[pid]
        return self.started(pid) if self.started is not None else None

    def _gone_within(self, pid: int, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while self.alive(pid):
            if time.monotonic() >= end:
                return False
            time.sleep(POLL)
        return True
