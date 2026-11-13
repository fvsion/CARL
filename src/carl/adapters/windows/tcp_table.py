"""The TCP listeners of a Windows computer (implements PortProbe, contract C-017): GetExtendedTcpTable for IPv4 and
IPv6 (the listeners and their owner pids, in any language of Windows), else `netstat -ano -p TCP` and `-p TCPv6`;
and a port's check by an exclusive bind (SO_EXCLUSIVEADDRUSE: on Windows a plain bind can share a port another
program holds with SO_REUSEADDR).

parse_table_v4, parse_table_v6 and parse_listeners are pure: they take the call's buffer or the command's text.
listeners() is also the GPU limit's (W6.1: the pid of CARL's own server, whose VRAM it adds back).
"""
from __future__ import annotations

import ctypes
import socket
import struct
import subprocess
from typing import Dict, Optional, Set

ARGS = ["netstat", "-ano", "-p", "TCP"]                # Windows' own netstat, by its name
ARGS6 = ["netstat", "-ano", "-p", "TCPv6"]
AF_INET, AF_INET6 = 2, 23                             # Windows' values (socket.AF_INET6 differs on a Mac)
TCP_TABLE_OWNER_PID_LISTENER = 3
TCP_TABLE_OWNER_PID_ALL = 5                           # every socket and its state (the dashboard's connections)
NO_ERROR, ERROR_INSUFFICIENT_BUFFER = 0, 122
ROW_V4 = struct.Struct("<6I")                         # state, local addr, local port, remote addr, remote port, pid
ROW_V6 = struct.Struct("<16sII16sIIII")               # local addr, scope, port, remote addr, scope, port, state, pid
TIMEOUT = 15


def _port(dw: int) -> int:
    """A table's port: the low 16 bits, in network order."""
    return ((dw & 0xFF) << 8) | ((dw >> 8) & 0xFF)


def parse_table_v4(buf: bytes) -> Dict[int, int]:
    """{port: pid} of a MIB_TCPTABLE_OWNER_PID (a count, then rows of six DWORDs)."""
    out: Dict[int, int] = {}
    if len(buf) < 4:
        return out
    (count,) = struct.unpack_from("<I", buf, 0)
    for i in range(count):
        at = 4 + i * ROW_V4.size
        if at + ROW_V4.size > len(buf):
            break
        _state, _addr, port, _raddr, _rport, pid = ROW_V4.unpack_from(buf, at)
        out.setdefault(_port(port), pid)
    return out


def parse_table_v6(buf: bytes) -> Dict[int, int]:
    """{port: pid} of a MIB_TCP6TABLE_OWNER_PID (a count, then rows of 56 bytes)."""
    out: Dict[int, int] = {}
    if len(buf) < 4:
        return out
    (count,) = struct.unpack_from("<I", buf, 0)
    for i in range(count):
        at = 4 + i * ROW_V6.size
        if at + ROW_V6.size > len(buf):
            break
        _addr, _scope, port, _raddr, _rscope, _rport, _state, pid = ROW_V6.unpack_from(buf, at)
        out.setdefault(_port(port), pid)
    return out


def parse_listeners(netstat_output: str) -> Dict[int, int]:
    """{port: pid} of the TCP listeners ("  TCP    127.0.0.1:9275    0.0.0.0:0    LISTENING       14240"; the state
    word is in the system's language, so a listener is a line whose remote port is 0)."""
    out: Dict[int, int] = {}
    for line in netstat_output.splitlines():
        f = line.split()
        if len(f) >= 5 and f[0].upper() == "TCP" and f[2].endswith(":0") and f[-1].isdigit():
            port = f[1].rsplit(":", 1)[-1]
            if port.isdigit():
                out.setdefault(int(port), int(f[-1]))
    return out


def _table(family: int, kind: int = TCP_TABLE_OWNER_PID_LISTENER) -> Optional[bytes]:
    """GetExtendedTcpTable's listeners of one family (kind: TCP_TABLE_OWNER_PID_ALL for every socket); None when the
    call cannot run."""
    try:
        get = ctypes.WinDLL("iphlpapi").GetExtendedTcpTable          # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return None
    get.restype = ctypes.c_uint32
    get.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_int, ctypes.c_uint32,
                    ctypes.c_int, ctypes.c_uint32]
    size = ctypes.c_uint32(0)
    for _ in range(4):                                 # the table can grow between the two calls
        buf = ctypes.create_string_buffer(size.value or 1)
        rc = get(buf, ctypes.byref(size), False, family, kind, 0)
        if rc == NO_ERROR:
            return buf.raw[:size.value] if size.value else buf.raw
        if rc != ERROR_INSUFFICIENT_BUFFER:
            return None
    return None


def _netstat(args: list) -> Dict[int, int]:
    try:
        text = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=TIMEOUT).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    return parse_listeners(text)


def listeners() -> Dict[int, int]:
    """{port: pid} of this computer's TCP listeners, IPv4 and IPv6 ({} when neither the call nor netstat runs)."""
    v4, v6 = _table(AF_INET), _table(AF_INET6)
    if v4 is None and v6 is None:
        out = _netstat(ARGS)
        for port, pid in _netstat(ARGS6).items():
            out.setdefault(port, pid)
        return out
    out = parse_table_v4(v4 or b"")
    for port, pid in parse_table_v6(v6 or b"").items():
        out.setdefault(port, pid)
    return out


def exclusive_bind_free(host: str, port: int) -> bool:
    """True when CARL can bind the port on host for itself alone (SO_EXCLUSIVEADDRUSE where the OS has it); the
    socket is closed at once."""
    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM) as s:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            s.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


class WindowsPorts:
    """The TCP listeners of this computer, read again at each question (implements PortProbe)."""

    def listening(self) -> Set[int]:
        return set(listeners())

    def owner(self, port: int) -> Optional[int]:
        return listeners().get(port)

    def free(self, host: str, port: int) -> bool:
        return exclusive_bind_free(host, port)
