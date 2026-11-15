"""What the background jobs tell the dashboard (contract C-131). The jobs run on threads of their own; they never
write the dashboard's state. They post events to the JobNotifier, which the dashboard applies on its own thread
(dashboard/actions/notifier.py): at once when the post comes from that thread, else before the next frame, in the
order they were posted. The dashboard's answer to a question it was asked (wait for the end of a turn) is read back
through waiting().

The texts are the jobs'; how they look is the dashboard's: a toast's `red` part is shown red, before its text."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, Tuple, Union

from ...domain.clients.package import Outcome
from ..model_list import HFFile
from .records import Download, Drain, HFLookup, InstallRun, PackageRun, TuneRun

JOB_SLOTS = ("dl", "tune", "install", "package", "hf", "drain")     # the dashboard's place of each job's record


@dataclass(frozen=True)
class Toast:
    """A message for the message line, for secs seconds: red (shown red) then text; error: the line is a failure's."""
    text: str
    secs: float = 6
    error: bool = False
    red: str = ""


@dataclass(frozen=True)
class Status:
    """What a restart does now (None: nothing; the message line shows it with its time); since: when it began
    (None: unchanged)."""
    text: Optional[str]
    since: Optional[float] = None


@dataclass(frozen=True)
class StartFailed:
    """The launcher's output of the last start, which failed (the dashboard shows its error lines; "": a new start
    clears them)."""
    console: str = ""


@dataclass(frozen=True)
class Settled:
    """A restart or the start after Auto-tune ended; clear_pending: it started the values chosen, or a failed start
    put config.json back (A46), so the Server panel reads them again (a restart that stopped before it started keeps
    them). The configs' update asked for after the restart is
    done or dropped."""
    clear_pending: bool = True


@dataclass(frozen=True)
class Job:
    """A job's record (None: none) in its slot of JOB_SLOTS."""
    slot: str
    record: Union[Download, TuneRun, InstallRun, PackageRun, HFLookup, Drain, None]


@dataclass(frozen=True)
class Found:
    """The GGUF files of a Hugging Face repo, each (file, bytes, sha256): the dashboard offers them to download."""
    repo: str
    files: Tuple[HFFile, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AskTuneStop:
    """Auto-tune needs the GPU: the dashboard asks before the server (pid) stops; what: what it measures."""
    what: str
    pid: int


@dataclass(frozen=True)
class RevealFailed:
    """The file manager could not show the file at path (the dashboard names its file manager)."""
    path: str


@dataclass(frozen=True)
class Handoff:
    """The slot handoff after the last start (C-138): what went back, or why nothing did (the Caching panel's line
    under Keep slots)."""
    text: str


@dataclass(frozen=True)
class PackageDone:
    """The client package of run is made (or not: its outcome's error)."""
    run: PackageRun
    outcome: Outcome


JobEvent = Union[Toast, Status, StartFailed, Settled, Job, Found, AskTuneStop, PackageDone, RevealFailed, Handoff]


class JobNotifier(Protocol):
    """The dashboard, as the jobs see it. Contract C-131."""

    def post(self, event: JobEvent) -> None:
        """Tell the dashboard: safe from any thread; applied on the dashboard's thread, in order."""
        ...

    def waiting(self) -> Optional[Drain]:
        """The step waiting for the end of a turn as the person left it (None: none, or they cancelled it)."""
        ...


def toast(text: str, secs: float = 6, error: bool = False) -> Toast:
    return Toast(text, secs, error)


def failure(red: str, text: str = "", secs: float = 6) -> Toast:
    """A failure: red, then text (which starts with its own space when it follows)."""
    return Toast(text, secs, True, red)

