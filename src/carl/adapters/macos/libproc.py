"""A process's physical footprint through one libproc call (proc_pid_rusage): what Activity Monitor shows as its
memory, with the Metal buffers, without mapped files. No `footprint` process."""
from __future__ import annotations

import ctypes
from typing import Callable, List, Optional

LIBPROC = "/usr/lib/libproc.dylib"
RUSAGE_INFO_V2 = 2                  # <sys/resource.h>: the first flavor with ri_phys_footprint


class RusageInfoV2(ctypes.Structure):
    """struct rusage_info_v2 of <sys/resource.h>."""
    _fields_ = [("ri_uuid", ctypes.c_uint8 * 16)] + [(n, ctypes.c_uint64) for n in (
        "ri_user_time", "ri_system_time", "ri_pkg_idle_wkups", "ri_interrupt_wkups", "ri_pageins", "ri_wired_size",
        "ri_resident_size", "ri_phys_footprint", "ri_proc_start_abstime", "ri_proc_exit_abstime",
        "ri_child_user_time", "ri_child_system_time", "ri_child_pkg_idle_wkups", "ri_child_interrupt_wkups",
        "ri_child_pageins", "ri_child_elapsed_abstime", "ri_diskio_bytesread", "ri_diskio_byteswritten")]


RusageCall = Callable[[int, int, int], int]         # proc_pid_rusage(pid, flavor, buffer address) -> 0 or -1
_rusage_fn: List[Optional[RusageCall]] = []         # libproc's proc_pid_rusage, looked up once (None: not available)


def _proc_pid_rusage() -> Optional[RusageCall]:
    """libproc's proc_pid_rusage, or None (not macOS: there is no such library; or no libproc)."""
    if not _rusage_fn:
        fn: Optional[RusageCall] = None
        try:
            c = ctypes.CDLL(LIBPROC, use_errno=True).proc_pid_rusage
            c.argtypes, c.restype = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p], ctypes.c_int
            fn = c
        except (OSError, AttributeError):
            fn = None
        _rusage_fn.append(fn)
    return _rusage_fn[0]


def phys_footprint(pid: int, call: Optional[RusageCall] = None) -> Optional[int]:
    """pid's physical footprint in bytes, or None when it can't be read: another user's process, not macOS.
    call: proc_pid_rusage (a test gives its own)."""
    fn = call or _proc_pid_rusage()
    if fn is None:
        return None
    buf = RusageInfoV2()
    try:
        if fn(pid, RUSAGE_INFO_V2, ctypes.addressof(buf)) != 0:
            return None
    except (OSError, ctypes.ArgumentError):
        return None
    return int(buf.ri_phys_footprint) or None
