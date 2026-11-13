"""The stop of a server CARL started (contract C-117): the sessions the dashboard recorded are saved through the
server's API first (POST /slots/ID?action=save, into the slot folder), then the server is asked to end and is ended
after the timeout (ProcessControl: SIGTERM to its group, then SIGKILL; the Windows stop is W6.4's). A save that
fails does not keep the server running: the stop is what the user asked for. A detach does not come here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Sequence, Tuple

from ..domain.shared.contracts import require
from ..ports import LlamaServerApi, ProcessControl

STOP_TIMEOUT = 30.0                     # seconds for the server to exit before it is ended
SAVE_TIMEOUT = 60.0                     # seconds for one slot's save


@dataclass
class StopResult:
    saved: List[str] = field(default_factory=list)       # the files the server saved
    failed: List[str] = field(default_factory=list)      # the saves that got no answer
    stopped: bool = False                                 # the process is gone


class StopServer:
    def __init__(self, api: LlamaServerApi, control: ProcessControl) -> None:
        self.api, self.control = api, control

    def stop(self, pid: int, saves: Sequence[Tuple[int, str]] = (), timeout: float = STOP_TIMEOUT) -> StopResult:
        """Save each (slot id, file name) of saves, then stop the process pid (one CARL started)."""
        require(pid > 0, "C-117", "the pid of a server CARL started")
        out = StopResult()
        for slot, name in saves:
            require(slot >= 0 and bool(name) and "/" not in name and "\\" not in name, "C-117",
                    "a slot id and a file name in the slot folder")
            try:
                self.api.post(f"/slots/{slot}?action=save", {"filename": name}, SAVE_TIMEOUT)
                out.saved.append(name)
            except (OSError, ValueError):
                out.failed.append(name)
        out.stopped = self.control.stop(pid, timeout)
        return out
