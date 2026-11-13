"""The jobs' notifier (JobNotifier, contract C-131): what the background jobs report, applied to the UI state on the
dashboard's own thread. A report from that thread (a key, a click, the loop's poll) is applied at once; one from a
job's thread waits in a queue (under a lock) until drain(), which the loop calls before each frame (App.drain_events).
So only the dashboard's thread writes UIState, in the order the reports came.

Here the reports get their looks: a toast's red part, the Hugging Face file picker, the question before Auto-tune
stops the server, the launcher's error lines of a start that failed, and the file manager's name."""
from __future__ import annotations

import re
import threading
from collections import deque
from typing import Deque, List, Optional

from ...application.server_jobs.notifier import (JOB_SLOTS, AskTuneStop, Found, Job, JobEvent, PackageDone,
                                                 RevealFailed, Settled, StartFailed, Status, Toast)
from ...application.server_jobs.records import Drain
from ...domain.server.snapshot import clean
from ...domain.shared.units import file_size
from ...domain.shared.ansi import strip_ansi
from ...ui.fmt import DIM, R, RED, row
from ...ui.words import file_manager
from ..act import Verb, act
from ..state import Confirm, Picker, UIState

_REFUSAL = re.compile(r"^(error|refused)\b", re.I)


def launcher_errors(text: Optional[str], most: int = 8, tail: bool = True) -> List[str]:
    """What the launcher said when it refused or failed a start (text: its console output): its first "error:" (or
    "Refused") line and the indented lines after it (the reason comes first, the advice after), at most `most` lines;
    else its last 3 lines (tail=False: nothing, so a normal stop is not shown as a failure). [] when there is no
    output."""
    lines = [clean(strip_ansi(x)).rstrip() for x in (text or "").splitlines()]
    for i, x in enumerate(lines):
        if _REFUSAL.match(x.strip()):
            out = [x.strip()]
            for y in lines[i + 1:]:
                if not y.strip() or not y[:1].isspace():
                    break
                out.append(y.strip())
            return out[:most]
    return [x for x in lines if x.strip()][-3:] if tail else []


class UiNotifier:
    """JobNotifier over the dashboard's UIState. The thread that makes it is the dashboard's."""

    def __init__(self, ui: UIState) -> None:
        self.ui = ui
        self.owner = threading.get_ident()
        self._lock = threading.Lock()
        self._queue: Deque[JobEvent] = deque()

    def post(self, event: JobEvent) -> None:
        if threading.get_ident() != self.owner:
            with self._lock:
                self._queue.append(event)
            return
        self.drain()                            # what came before it first
        self.apply(event)

    def waiting(self) -> Optional[Drain]:
        return self.ui.drain

    def drain(self) -> None:
        """Apply the reports the jobs' threads posted, in order (on the dashboard's thread)."""
        while True:
            with self._lock:
                if not self._queue:
                    return
                event = self._queue.popleft()
            self.apply(event)

    def apply(self, e: JobEvent) -> None:
        ui = self.ui
        if isinstance(e, Toast):
            ui.toast(f"{RED}{e.red}{R}{e.text}" if e.red else e.text, e.secs, error=e.error)
        elif isinstance(e, Status):
            ui.restart = e.text
            if e.since is not None:
                ui.restart_t = e.since
        elif isinstance(e, StartFailed):
            ui.start_error = launcher_errors(e.console)
        elif isinstance(e, Settled):
            if e.clear_pending:
                ui.pending = None
            ui.install_after_restart = False
        elif isinstance(e, Job) and e.slot in JOB_SLOTS:
            setattr(ui, e.slot, e.record)
            if e.slot == "install" and e.record is not None:     # its output takes the place of the config preview
                ui.install_shown, ui.package_shown = True, False
            elif e.slot == "package" and e.record is not None:
                ui.package_shown, ui.install_shown = True, False
        elif isinstance(e, Found):
            ui.picker = Picker(title=f"HUGGING FACE  {e.repo}", on_pick="pickhf", noun="GGUF files",
                               header=f"{DIM}{'':2}{'file':<60} {'download':>9}  checksum{R}",
                               foot="Enter downloads it to the models folder. You can stop and continue the download. "
                                    "CARL checks a file that has a checksum (known); it cannot check one with none.",
                               items=[(f, f"{f:<60} {file_size(b):>9}  "
                                          f"{DIM}{'known' if sha else 'none'}{R}")
                                      for f, b, sha in e.files])
        elif isinstance(e, AskTuneStop):
            ui.confirm2 = Confirm("STOP THE SERVER FOR AUTO-TUNE?", [
                "Auto-tune needs the GPU for itself. It stops the server now, measures, then starts the server again "
                "with the saved settings. Requests in progress stop.", "",
                row("measures", e.what), *([row("server", f"pid {e.pid}")] if ui.full else [])], act(Verb.TYES),
                yes_label="Stop the server and tune")
        elif isinstance(e, PackageDone):
            e.run.outcome, e.run.done = e.outcome, True
        elif isinstance(e, RevealFailed):
            ui.toast(f"CARL cannot open the {file_manager()}. The zip is {e.path}", 10, error=True)
