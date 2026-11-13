"""This Windows computer (implements HostInfo, contract C-015): its RAM (GlobalMemoryStatusEx) and its processor's
name (the registry's ProcessorNameString), for the reports and the Auto-tune record; and the console's VT mode, so
the start's colours show in cmd.exe and PowerShell (the bootstrap leaves it to the Windows adapters, rule R4).
"""
from __future__ import annotations

import ctypes
from typing import Any, Callable, Optional

CPU_KEY = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
CPU_VALUE = "ProcessorNameString"
STD_OUTPUT_HANDLE, STD_ERROR_HANDLE = -11, -12
ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004


class _MemoryStatus(ctypes.Structure):
    _fields_ = [("length", ctypes.c_uint32), ("load", ctypes.c_uint32)] + [
        (n, ctypes.c_uint64) for n in ("total_phys", "avail_phys", "total_page", "avail_page", "total_virtual",
                                       "avail_virtual", "avail_extended")]


def ram_bytes() -> int:
    """Installed RAM (0 when unknown)."""
    try:
        k = ctypes.WinDLL("kernel32")                                   # type: ignore[attr-defined]
        st = _MemoryStatus()
        st.length = ctypes.sizeof(_MemoryStatus)
        return int(st.total_phys) if k.GlobalMemoryStatusEx(ctypes.byref(st)) else 0
    except (AttributeError, OSError):
        return 0


def free_bytes() -> int:
    """The RAM free now, the standby list included (GlobalMemoryStatusEx's available memory; 0 when unknown)."""
    try:
        k = ctypes.WinDLL("kernel32")                                   # type: ignore[attr-defined]
        st = _MemoryStatus()
        st.length = ctypes.sizeof(_MemoryStatus)
        return int(st.avail_phys) if k.GlobalMemoryStatusEx(ctypes.byref(st)) else 0
    except (AttributeError, OSError):
        return 0


def cpu_name() -> str:
    """The processor's name ("" when unknown)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, CPU_KEY) as key:
            return str(winreg.QueryValueEx(key, CPU_VALUE)[0]).strip()
    except (ImportError, OSError):
        return ""


def machine_text(cpu: str, ram: int) -> str:
    """The processor and the RAM in words, as a Mac's record writes them ("AMD Ryzen 9 3900X 128 GB")."""
    return f"{cpu} {ram // 2 ** 30} GB" if cpu or ram else ""


class WindowsHostInfo:
    def __init__(self, ram: Callable[[], int] = ram_bytes, cpu: Callable[[], str] = cpu_name) -> None:
        self._ram, self._cpu = ram, cpu

    def ram_bytes(self) -> int:
        return self._ram()

    def cpu_name(self) -> str:
        """The processor's name alone (Auto-tune's first line)."""
        return self._cpu()

    def machine(self) -> str:
        return machine_text(self.cpu_name(), self.ram_bytes())


def vt_output(k: Optional[Any] = None) -> bool:
    """Turn on VT processing for stdout and stderr when they are a console (colours and cursor codes show); True
    when at least one is on. Nothing changes for a pipe or a file."""
    try:
        k = k or ctypes.WinDLL("kernel32")                              # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False
    k.GetStdHandle.restype = ctypes.c_void_p
    k.GetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k.SetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    on = False
    for which in (STD_OUTPUT_HANDLE, STD_ERROR_HANDLE):
        h = k.GetStdHandle(which)
        mode = ctypes.c_uint32()
        if h and k.GetConsoleMode(h, ctypes.byref(mode)):
            on = bool(k.SetConsoleMode(h, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)) or on
    return on
