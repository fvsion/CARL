"""One secret in the Windows Credential Manager (implements SecretStore, contract C-065), through advapi32 with ctypes
(CredWriteW, CredReadW, CredDeleteW). It is a generic credential of the user, kept for this computer. The secret is
stored as UTF-16, the way the Credential Manager's own tools store a password, so its window can show it to its
owner. Nothing here prints or logs the secret.

blob_of and secret_of are pure; the calls need Windows.
"""
from __future__ import annotations

import ctypes
from typing import Any, Callable, Optional

from ...domain.shared.errors import SecretStoreError
from ..common.token_file import PrivateFile
from .secrets import make_private

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168
ERROR_NO_SUCH_LOGON_SESSION = 1312               # a network (ssh) logon has no credential set
MAX_BLOB = 2560                                 # CRED_MAX_CREDENTIAL_BLOB_SIZE, bytes


def blob_of(secret: str) -> bytes:
    """The bytes the Credential Manager keeps for a secret."""
    return secret.encode("utf-16-le")


def last_error() -> int:
    return int(ctypes.get_last_error())                                 # type: ignore[attr-defined]


def secret_of(blob: bytes) -> str:
    """The secret in the bytes of a credential."""
    return blob.decode("utf-16-le", errors="replace")


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class _Credential(ctypes.Structure):
    _fields_ = [("Flags", ctypes.c_uint32), ("Type", ctypes.c_uint32), ("TargetName", ctypes.c_wchar_p),
                ("Comment", ctypes.c_wchar_p), ("LastWritten", _FileTime), ("CredentialBlobSize", ctypes.c_uint32),
                ("CredentialBlob", ctypes.c_void_p), ("Persist", ctypes.c_uint32), ("AttributeCount", ctypes.c_uint32),
                ("Attributes", ctypes.c_void_p), ("TargetAlias", ctypes.c_wchar_p), ("UserName", ctypes.c_wchar_p)]


def _advapi() -> Any:
    a = ctypes.WinDLL("advapi32", use_last_error=True)                  # type: ignore[attr-defined]
    vp = ctypes.c_void_p
    a.CredWriteW.argtypes = [vp, ctypes.c_uint32]
    a.CredWriteW.restype = ctypes.c_int
    a.CredReadW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, vp]
    a.CredReadW.restype = ctypes.c_int
    a.CredDeleteW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
    a.CredDeleteW.restype = ctypes.c_int
    a.CredFree.argtypes = [vp]
    a.CredFree.restype = None
    return a


class CredentialManager:
    """The secret under the target name `target` ("CARL/huggingface"); user: the credential's user name."""

    def __init__(self, target: str, user: str, api: Optional[Any] = None) -> None:
        self.target, self.user = target, user
        self._api = api

    def api(self) -> Any:
        if self._api is None:
            self._api = _advapi()
        return self._api

    def read(self) -> str:
        try:
            a = self.api()
        except (AttributeError, OSError):
            return ""
        found = ctypes.c_void_p()
        if not a.CredReadW(self.target, CRED_TYPE_GENERIC, 0, ctypes.byref(found)) or not found.value:
            return ""
        try:
            cred = _Credential.from_address(found.value)
            size = cred.CredentialBlobSize
            return secret_of(ctypes.string_at(cred.CredentialBlob, size)) if size and cred.CredentialBlob else ""
        finally:
            a.CredFree(found)

    def write(self, secret: str) -> None:
        blob = blob_of(secret)
        if len(blob) > MAX_BLOB:
            raise SecretStoreError("The token is too long for the Windows Credential Manager.")
        try:
            a = self.api()
        except (AttributeError, OSError):
            raise SecretStoreError("CARL could not reach the Windows Credential Manager.") from None
        buf = ctypes.create_string_buffer(blob, len(blob))
        cred = _Credential()
        cred.Type, cred.TargetName, cred.UserName = CRED_TYPE_GENERIC, self.target, self.user
        cred.CredentialBlobSize, cred.CredentialBlob = len(blob), ctypes.cast(buf, ctypes.c_void_p)
        cred.Persist = CRED_PERSIST_LOCAL_MACHINE
        if not a.CredWriteW(ctypes.byref(cred), 0):
            raise SecretStoreError("CARL could not keep the token in the Windows Credential Manager "
                                   f"(error {last_error()}).")

    def remove(self) -> None:
        try:
            a = self.api()
        except (AttributeError, OSError):
            raise SecretStoreError("CARL could not reach the Windows Credential Manager.") from None
        if not a.CredDeleteW(self.target, CRED_TYPE_GENERIC, 0):
            err = last_error()
            if err in (ERROR_NOT_FOUND, ERROR_NO_SUCH_LOGON_SESSION):         # no credential set: nothing is kept here
                return
            raise SecretStoreError("CARL could not remove the token from the Windows Credential Manager "
                                   f"(error {err}).")

    def where(self) -> str:
        return "the Windows Credential Manager"


class AclFile(PrivateFile):
    """The private file where the Credential Manager is not there for this logon: an ssh session has a network
    logon, and CredWrite answers ERROR_NO_SUCH_LOGON_SESSION (1312) to it (CredDelete too; remove above treats it as
    nothing kept). Windows has no file modes, so the file's privacy is its access list, left to the user, SYSTEM and
    the Administrators group (adapters/windows/secrets.py, as the API key's file). A file whose list cannot be set is
    removed again."""

    def __init__(self, path: str, label: str = "", private: Callable[[str], bool] = make_private) -> None:
        super().__init__(path, label)
        self.private = private

    def write(self, secret: str) -> None:
        super().write(secret)
        if not self.private(self.path):
            self.remove()
            raise SecretStoreError(f"CARL could not make the file {self.path} readable by you alone.")
