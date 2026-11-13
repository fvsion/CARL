"""The records of the dashboard's background jobs: a download, an Auto-tune run, the client installer, the client
package, a Hugging Face lookup and a step that waits for the end of a client's turn. The jobs make them and change
them; the dashboard shows them (dashboard/state.py keeps the ones shown now). Plain data, and Child: a process CARL
started, over the ProcessControl port (contract C-019)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Protocol, Tuple

from ...domain.clients.package import Outcome
from ...domain.tune.progress import TuneProgress, parse_line
from ...ports import ProcessControl
from ..model_list import HFFile

TUNE_ALL = "*all*"                 # Auto-tune's choice "every downloaded model" (carl tune all)


class Process(Protocol):
    """A child process (Child, or subprocess.Popen in a test)."""
    pid: int

    @property
    def returncode(self) -> Optional[int]: ...
    def poll(self) -> Optional[int]: ...


class Child:
    """A process the jobs started (ProcessControl.spawn): poll() its exit code (None while it runs), wait() up to a
    timeout."""

    def __init__(self, control: ProcessControl, pid: int) -> None:
        self.control, self.pid = control, pid
        self._code: Optional[int] = None

    @property
    def returncode(self) -> Optional[int]:
        return self._code

    def poll(self) -> Optional[int]:
        return self.wait(0)

    def wait(self, timeout: float) -> Optional[int]:
        if self._code is None:
            self._code = self.control.wait(self.pid, timeout)
        return self._code


@dataclass
class Drain:
    """A step that stops the model (Stop, Apply, Auto-tune, a router load) asked for while a client's
    turn runs: CARL asks first (now, or wait for the end of the turn), then runs it once no turn runs
    and the turn's save is on disk."""
    what: str                       # "stop the server", "apply the settings", ...
    go: Callable[[], None]
    busy: List[int]                 # the slots writing a reply
    turns: List[int] = field(default_factory=list)     # the slots where a client's turn runs
    saving: bool = False            # a save file is being written
    waiting: bool = False
    since: float = 0.0              # when the wait started
    checked: float = 0.0            # the last look at the slots
    idle: int = 0                   # looks in a row with every slot idle


@dataclass
class HFLookup:
    """A Hugging Face repo being looked up, or its GGUF files."""
    repo: str
    status: str = ""
    files: List[HFFile] = field(default_factory=list)


@dataclass
class Download:
    """A model download (carl download): progress from the file's size (and the other files it writes: a model's MTP
    drafter)."""
    name: str
    path: Optional[str]
    total: int
    proc: Process
    log: str
    more: List[str] = field(default_factory=list)                   # other files it writes (counted in have)
    hist: List[Tuple[float, int]] = field(default_factory=list)     # (time, bytes) of the last samples
    have: int = 0
    rate: float = 0.0               # bytes per second
    tail: List[str] = field(default_factory=list)                   # the last line of its output
    done: bool = False


@dataclass
class TuneRun:
    """An Auto-tune run (carl tune)."""
    model: str
    proc: Process
    log: str
    restart: bool                   # it stopped the server: start it again afterwards
    lines: List[str] = field(default_factory=list)                  # its output so far
    done: bool = False

    @property
    def events(self) -> List[TuneProgress]:
        """Its output as events (domain/tune/progress.py): the steps, the notes, the end."""
        return [parse_line(x) for x in self.lines]


@dataclass
class InstallRun:
    """carl install from the Connect tab (carl.py install): clients and configs, or
    the configs only."""
    what: str                       # "clients and configs" | "configs"
    proc: Process
    log: str
    lines: List[str] = field(default_factory=list)                  # its output so far
    done: bool = False


@dataclass
class PackageRun:
    """The client package from the Connect tab (application/package.py, the code of
    carl package): it runs in a thread; outcome is set when it is done."""
    started: float
    outcome: Optional[Outcome] = None
    done: bool = False
