"""A file extended as a sparse file on NTFS (the stub of a GGUF header, adapters/llama/gguf_stub.py; decision A67). Python's
truncate past the end writes zeros there (the C runtime's _chsize), even with the sparse flag: the file is marked sparse
(FSCTL_SET_SPARSE) and extended with SetEndOfFile, which leaves a hole (results/WESTc/e-tests.md). The Windows modules are
imported when it runs, so the composition can name it on any OS (the tests build every profile)."""
from __future__ import annotations

from typing import BinaryIO

FSCTL_SET_SPARSE = 0x900C4


def extend_sparse(f: BinaryIO, size: int) -> None:
    """f marked sparse and extended to size bytes. Raises OSError."""
    import ctypes
    import msvcrt
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)        # type: ignore[attr-defined]
    k32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID,
                                    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    k32.SetFilePointerEx.argtypes = [wintypes.HANDLE, ctypes.c_longlong, ctypes.c_void_p, wintypes.DWORD]
    k32.SetEndOfFile.argtypes = [wintypes.HANDLE]
    h = msvcrt.get_osfhandle(f.fileno())                        # type: ignore[attr-defined]
    got = wintypes.DWORD(0)
    if not k32.DeviceIoControl(h, FSCTL_SET_SPARSE, None, 0, None, 0, ctypes.byref(got), None):
        raise OSError(ctypes.get_last_error(), "FSCTL_SET_SPARSE failed")      # type: ignore[attr-defined]
    if not (k32.SetFilePointerEx(h, size, None, 0) and k32.SetEndOfFile(h)):
        raise OSError(ctypes.get_last_error(), "SetEndOfFile failed")         # type: ignore[attr-defined]
