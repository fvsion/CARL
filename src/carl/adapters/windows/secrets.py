"""The server's API key on Windows (implements KeyStore, contract C-026, over the common key file): Windows has no
file modes, so the key's privacy is its access list. After the key file is made or kept, its access list is read
(GetNamedSecurityInfoW; by SID, so it reads the same in every language of Windows) and, when anyone other than the
user, SYSTEM and the Administrators group may open it, it is set to those three alone: `icacls KEY /inheritance:r
/grant:r *SID:(F) ... /remove:g *OTHER`. A key whose list cannot be read or set keeps it, with one warning.

extra_sids is pure: it takes the list's SIDs and the user's.
"""
from __future__ import annotations

import ctypes
import subprocess
from typing import Any, Callable, List, Optional, Sequence

from ...ports import KeyStore

SYSTEM_SID = "S-1-5-18"
ADMINISTRATORS_SID = "S-1-5-32-544"
SE_FILE_OBJECT = 1
DACL_SECURITY_INFORMATION = 0x4
ACCESS_ALLOWED_ACE_TYPE = 0
INHERIT_ONLY_ACE = 0x08
TOKEN_QUERY = 0x0008
TOKEN_USER = 1
TIMEOUT = 15
NOT_PRIVATE = ("warning: CARL could not make the API key {path} readable by you alone. Check who may read it with "
               "icacls.")


def extra_sids(sids: Sequence[str], user_sid: str) -> List[str]:
    """The SIDs that may open the file besides the user, SYSTEM and the Administrators group (each once)."""
    allowed = {user_sid, SYSTEM_SID, ADMINISTRATORS_SID}
    return list(dict.fromkeys(s for s in sids if s not in allowed))


def icacls_argv(path: str, user_sid: str, extra: Sequence[str]) -> List[str]:
    """The icacls command that leaves the file to the user, SYSTEM and the Administrators group (no inherited
    entries)."""
    argv = ["icacls", path, "/inheritance:r", "/grant:r"] + [f"*{s}:(F)" for s in (user_sid, SYSTEM_SID,
                                                                                    ADMINISTRATORS_SID)]
    for sid in extra:
        argv += ["/remove:g", f"*{sid}"]
    return argv


class _Ace(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ubyte), ("flags", ctypes.c_ubyte), ("size", ctypes.c_ushort),
                ("mask", ctypes.c_uint32), ("sid_start", ctypes.c_uint32)]


class _Acl(ctypes.Structure):
    _fields_ = [("revision", ctypes.c_ubyte), ("sbz1", ctypes.c_ubyte), ("size", ctypes.c_ushort),
                ("count", ctypes.c_ushort), ("sbz2", ctypes.c_ushort)]


def _advapi() -> Any:
    a = ctypes.WinDLL("advapi32", use_last_error=True)                  # type: ignore[attr-defined]
    vp = ctypes.c_void_p
    a.GetNamedSecurityInfoW.argtypes = [ctypes.c_wchar_p, ctypes.c_int, ctypes.c_uint32, vp, vp, vp, vp, vp]
    a.GetNamedSecurityInfoW.restype = ctypes.c_uint32
    a.ConvertSidToStringSidW.argtypes = [vp, vp]
    a.OpenProcessToken.argtypes = [vp, ctypes.c_uint32, vp]
    a.GetTokenInformation.argtypes = [vp, ctypes.c_int, vp, ctypes.c_uint32, vp]
    return a


def _sid_text(a: Any, sid: int) -> str:
    out = ctypes.c_wchar_p()
    if not a.ConvertSidToStringSidW(ctypes.c_void_p(sid), ctypes.byref(out)):
        return ""
    try:
        return out.value or ""
    finally:
        ctypes.WinDLL("kernel32").LocalFree(out)                        # type: ignore[attr-defined]


def file_sids(path: str) -> Optional[List[str]]:
    """The SIDs that the file's access list allows in (allow entries only); None when it cannot be read."""
    try:
        a = _advapi()
        k = ctypes.WinDLL("kernel32")                                   # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return None
    dacl, sd = ctypes.c_void_p(), ctypes.c_void_p()
    if a.GetNamedSecurityInfoW(path, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION, None, None, ctypes.byref(dacl),
                               None, ctypes.byref(sd)) != 0:
        return None
    try:
        if not dacl.value:
            return ["S-1-1-0"]                                          # no list at all: everyone may open it
        acl = _Acl.from_address(dacl.value)
        out, at = [], dacl.value + ctypes.sizeof(_Acl)
        for _ in range(acl.count):
            ace = _Ace.from_address(at)
            if ace.type == ACCESS_ALLOWED_ACE_TYPE and not ace.flags & INHERIT_ONLY_ACE:
                out.append(_sid_text(a, at + _Ace.sid_start.offset))
            at += ace.size
        return [s for s in out if s]
    finally:
        k.LocalFree(sd)


def user_sid() -> str:
    """The SID of the user this process runs as ("" when it cannot be read)."""
    try:
        a = _advapi()
        k = ctypes.WinDLL("kernel32")                                   # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return ""
    k.GetCurrentProcess.restype = ctypes.c_void_p
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    token = ctypes.c_void_p()
    if not a.OpenProcessToken(k.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
        return ""
    try:
        size = ctypes.c_uint32(0)
        a.GetTokenInformation(token, TOKEN_USER, None, 0, ctypes.byref(size))
        buf = ctypes.create_string_buffer(size.value or 1)
        if not a.GetTokenInformation(token, TOKEN_USER, buf, size, ctypes.byref(size)):
            return ""
        return _sid_text(a, ctypes.c_void_p.from_buffer(buf).value or 0)
    finally:
        k.CloseHandle(token)


def make_private(path: str, sids: Callable[[str], Optional[List[str]]] = file_sids,
                 user: Callable[[], str] = user_sid,
                 run: Callable[[List[str]], int] = lambda argv: subprocess.run(
                     argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     timeout=TIMEOUT).returncode) -> bool:
    """Leave the file to the user, SYSTEM and the Administrators group; True when its list says so (also when it
    already did)."""
    me = user()
    found = sids(path)
    if not me or found is None:
        return False
    extra = extra_sids(found, me)
    if not extra:
        return True
    try:
        run(icacls_argv(path, me, extra))
    except (OSError, subprocess.SubprocessError):
        return False
    after = sids(path)
    return after is not None and not extra_sids(after, me)


class PrivateKeyFile:
    """A key store whose file is left to the user, SYSTEM and the Administrators group after each ensure. inner: the
    common key file; private: the access list's check and fix; note: where the one warning goes."""

    def __init__(self, inner: KeyStore, note: Callable[[str], None],
                 private: Callable[[str], bool] = make_private) -> None:
        self.inner, self.note, self.private = inner, note, private

    def path(self) -> str:
        return self.inner.path()

    def read(self) -> str:
        return self.inner.read()

    def ensure(self) -> str:
        path = self.inner.ensure()
        if not self.private(path):
            self.note(NOT_PRIVATE.format(path=path))
        return path
