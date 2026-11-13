"""Ports of the processes: the process list, starting and stopping, the server's runner and keeping the
computer awake."""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Mapping, Optional, Protocol, Sequence

if TYPE_CHECKING:                       # domain/tune imports the ports: no import cycle at run time
    from ..domain.hardware.processes import ProcessInfo


class ProcessTable(Protocol):
    """The running processes. Contract C-018."""

    def processes(self) -> List[ProcessInfo]:
        """Every process: pid, resident memory and command ([] when the list can't be read)."""
        ...

    def alive(self, pid: int) -> bool: ...


class ProcessControl(Protocol):
    """Starting and stopping processes CARL owns. Contract C-019."""

    def spawn(self, argv: Sequence[str], env: Mapping[str, str], log_path: str, new_session: bool) -> int:
        """Start argv with its output in log_path; its pid. new_session: it outlives the caller's terminal."""
        ...

    def stop(self, pid: int, timeout: float) -> bool:
        """Ask the process (and its group) to end, then end it after timeout; True when it is gone."""
        ...

    def wait(self, pid: int, timeout: float) -> Optional[int]:
        """The exit code of a child, None when it still runs after timeout."""
        ...


class ServerRunner(Protocol):
    """Runs llama-server (argv: the program first) with its output through the log filter. Contract C-020."""

    def run(self, argv: Sequence[str], env: Mapping[str, str], log_path: str, port: int) -> int:
        """In the foreground (MONITOR=0). log_path: the log the filter writes for the server on port ("": no
        filter). Where the OS allows it the caller becomes the server and this does not return; otherwise the
        server's exit code."""
        ...

    def start(self, argv: Sequence[str], env: Mapping[str, str], log_path: str, port: int, console: str) -> int:
        """In the background, apart from the terminal's signals (an interactive start), its output in the console
        file (through the filter when log_path is given); the server's pid."""
        ...


class KeepAwake(Protocol):
    """Keeps the computer from sleeping while the server runs. Contract C-021."""

    def hold(self, pid: int) -> None:
        """Stay awake while the process runs."""
        ...

    def release(self) -> None: ...
