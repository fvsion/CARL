"""The dashboard's view of the computer: memory, CPU, power and the GPUs; the server's sockets, process and files."""
from __future__ import annotations

from typing import Callable, List, Optional, Protocol, Tuple

from ..domain.server.snapshot import ProcInfo, SlowStats
from ..domain.server.snapshot import SystemStats as MemoryStats
from ..domain.shared.types import JsonObject


class SystemStats(Protocol):
    """One sample of the computer's state. Contract C-033."""

    def sample(self, server_pid: Optional[int]) -> JsonObject:
        """The same fields on every OS (RAM, swap, CPU %, power, each GPU's memory and use, the server's RAM and
        VRAM), plus the extras this OS has; a field it can't tell is left out."""
        ...


class TcpTable(Protocol):
    """The TCP sockets of this computer as one read saw them (the dashboard reads them at most once a refresh).
    Contract C-039."""

    def listen_host(self, port: int) -> Optional[str]:
        """The address a server on port listens on (all addresses: 127.0.0.1), None when nothing listens."""
        ...

    def listen_pid(self, port: int) -> Optional[int]:
        """The pid listening on port, None when nothing does or the owner can't be told."""
        ...

    def connections(self, port: int, pid: int) -> List[Tuple[str, str]]:
        """(client address, client port) of each connection to pid's port."""
        ...

    def next_free_port(self, port: int) -> Optional[int]:
        """The first port after port that nothing listens on (up to 100 on), None when there is none."""
        ...


class ServerFiles(Protocol):
    """The files the dashboard reads of the server it watches: its log, its console output, the pointer of CARL's log
    filter and its model file. Read only. Contract C-053."""

    def size(self, path: str) -> Optional[int]:
        """The size in bytes of the regular file at path, None when there is none."""
        ...

    def read_from(self, path: str, pos: int) -> Optional[Tuple[int, int, str]]:
        """(start, end, text): what path holds from pos to its end (from 0 when the file is shorter than pos: it was
        cut), as text without terminal colours and control characters; end is where the next read starts. None when
        it can't be read."""
        ...

    def started(self, path: str) -> Optional[float]:
        """When a log started (epoch seconds), from its name or the name of the file it links to
        (llama-server-YYYYMMDD-HHMMSS.log); None when the name does not say."""
        ...

    def pointer(self, home: str, port: int) -> JsonObject:
        """What CARL's log filter wrote for the server on port (its log and its pid), {} when nothing can be read."""
        ...


class ServerWatch(Protocol):
    """What the dashboard reads of this computer and of the server it watches, at each refresh. Contract C-038."""

    def sockets(self) -> TcpTable:
        """The TCP sockets now (each call reads them again)."""
        ...

    def process(self, pid: int) -> Optional[ProcInfo]:
        """The process's memory, CPU, run time and command line; None when it can't be read."""
        ...

    def alive(self, pid: Optional[int]) -> bool:
        """True while the process runs (a child of this process that ended is reaped first)."""
        ...

    def kept_awake(self, pid: int) -> bool:
        """True when the computer is kept awake for the process."""
        ...

    def kill(self, pid: int) -> None:
        """End the process at once (a process already gone is fine)."""
        ...

    def memory(self) -> MemoryStats:
        """The memory, swap, GPU, power and load now."""
        ...

    def slow(self, server_pid: Callable[[], Optional[int]], disk_path: str) -> SlowStats:
        """The slow stats (seconds to read): the thermal state, sleep and wake since the server started, the free
        disk of disk_path. server_pid is asked after the thermal state."""
        ...

    def read_key(self, path: str) -> str:
        """The server's API key in path, "" when there is none."""
        ...

    def files(self) -> ServerFiles:
        """The server's files (its log, its console output, its model)."""
        ...
