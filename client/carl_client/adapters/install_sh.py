"""The setup's config step run again (the Installer port): the step of the setup (carl_client.app.install), in this
process (Phase W: no bash), with its backups and its merge rules. Its output goes to the sync's log
(~/.config/carl/client-sync.log: the run's title first); it reads no terminal (a missing key stops it)."""
from __future__ import annotations

import io
import os
import time
import traceback
from typing import IO, Callable, Mapping, Optional

Step = Callable[..., int]           # carl_client.app.install.step(environ, here, out, err, argv, stdin)


class InstallSh:
    """here: the client's folder; log: the sync's log file; step: the config step (the sync's build passes
    carl_client.app.install.step)."""

    def __init__(self, here: str, log: str, step: Step) -> None:
        self.here = here
        self._log = log
        self.step = step

    @property
    def log(self) -> str:
        return self._log

    def run(self, env: Mapping[str, str], title: str) -> int:
        os.makedirs(os.path.dirname(self._log), exist_ok=True)
        with open(self._log, "w", encoding="utf-8", newline="\n") as log:
            log.write(f"== {time.strftime('%Y-%m-%d %H:%M:%S')}: {title}\n")
            log.flush()
            return run_logged(self.step, dict(env), self.here, log)


def run_logged(step: Step, env: Mapping[str, str], here: str, log: IO[str],
               stdin: Optional[IO[str]] = None) -> int:
    """The step with both its streams on log and no input; its exit code (SystemExit gives its code; an error of the
    step is written to the log and gives 1, as a failing config step did)."""
    try:
        return int(step(env, here, log, log, [], stdin if stdin is not None else io.StringIO("")))
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except Exception:                                   # noqa: BLE001 (the service goes on; the log has the error)
        log.write(traceback.format_exc())
        return 1
