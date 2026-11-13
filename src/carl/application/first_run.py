"""The first run (W3.4): what `carl` does with no command (host/serve.sh's no-argument branch), over the ports.

  1. A llama-server on the port (PORT, else 9275) is attached to: the dashboard opens. Another program there is not
     a server of CARL's.
  2. The tools CARL needs (Dependencies; SKIP_DEPS=1 skips). On a computer whose profile has no check, none.
  3. A downloaded model: the start follows (with a line that names the saved settings).
  4. No model yet: Auto fit's pick from the whole catalogue and the size to download. On a terminal the user is
     asked; yes downloads it and the start follows. No answer, no terminal, or nothing that fits: the dashboard
     opens without a server (its Settings tab starts one later).

The result is a Step: the dashboard, the start or an exit code. The caller (cli/main.py) runs it. Every text is the
bash dispatch's (domain/server/dispatch_text.py). OS-free (R5): what no port covers yet (the model list, Auto fit's
pick, the download: carl.py fit --pick-default and carl.py download NAME) comes in as FirstRunTools from the
composition.
Contract C-120.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Mapping, Optional

from ..domain.server import dispatch_text as dt
from ..domain.server.launch_plan import is_port
from ..domain.shared.contracts import ensure
from ..domain.shared.platform_words import PlatformWords
from ..ports import Dependencies, PortProbe, ProcessTable
from .start_server import LLAMA_SERVER

DASHBOARD, START, EXIT = "dashboard", "start", "exit"
STEPS = (DASHBOARD, START, EXIT)
NO = ("N", "n")


@dataclass(frozen=True)
class Step:
    """What follows the first run: the dashboard (attached, or without a server), the start, or the end with code."""
    kind: str
    code: int = 0


@dataclass
class FirstRunTools:
    """What a first run needs that no port covers yet (the composition fills it in):
      out             a line on stdout (flushed);
      ask             a prompt on stderr and the line typed, None at the end of the input;
      is_tty          the terminal (fd 0 or 1);
      downloaded      the names of the downloaded models;
      pick_default    Auto fit's pick from the whole catalogue; None when it failed or nothing fits (it said why);
      model_bytes     a model's size in bytes, as text ("" when unknown);
      download        a model's download, in this terminal; its exit code."""
    out: Callable[[str], None]
    ask: Callable[[str], Optional[str]]
    is_tty: Callable[[int], bool]
    downloaded: Callable[[], List[str]]
    pick_default: Callable[[], Optional[str]]
    model_bytes: Callable[[str], str]
    download: Callable[[str], int]


class FirstRun:
    """One first run. env: the environment (SKIP_DEPS); deps: None where the profile has no check yet."""

    def __init__(self, words: PlatformWords, env: Mapping[str, str], probe: PortProbe, table: ProcessTable,
                 deps: Optional[Dependencies], tools: FirstRunTools) -> None:
        self.words, self.env, self.probe, self.table, self.deps, self.t = words, env, probe, table, deps, tools

    def run(self, port: str) -> Step:
        """The first run for the server port PORT (the text of PORT or CARL's default; not always a number)."""
        step = self._run(port)
        ensure(step.kind in STEPS, "C-120", "the first run ends in the dashboard, the start or an exit")
        return step

    def _run(self, port: str) -> Step:
        t = self.t
        if self._llama_on(port):
            return Step(DASHBOARD)
        if self.env.get("SKIP_DEPS", "0") != "1" and self.deps is not None:
            if not self.deps.offer(t.is_tty(0) and t.is_tty(1)):
                return Step(EXIT, 1)
        t.out(dt.LOOKING)
        if not t.downloaded():
            step = self._offer()
            if step is not None:
                return step
        t.out(dt.starting(self.words))
        return Step(START)

    def _llama_on(self, port: str) -> bool:
        """Only a llama-server counts: another program on the port is not one (a port that is no number has no
        owner)."""
        if not is_port(port):
            return False
        pid = self.probe.owner(int(port))
        if pid is None:
            return False
        return any(LLAMA_SERVER in p.command for p in self.table.processes() if p.pid == pid)

    def _offer(self) -> Optional[Step]:
        """No model is downloaded: Auto fit's pick, and the offer to download it. None: it is downloaded, start."""
        t, words = self.t, self.words
        t.out(dt.best_model(words))
        name = t.pick_default()
        if not name:
            t.out(dt.no_fit(words))
            return Step(DASHBOARD)
        t.out(dt.chosen(name, dt.gb_text(t.model_bytes(name)), words))
        answer = "n"
        if t.is_tty(0):
            typed = t.ask(dt.DOWNLOAD_PROMPT)
            if typed is None:                    # the end of the input: the launcher's read failed and it ended
                return Step(EXIT, 1)
            answer = typed
        if t.is_tty(0) and not answer.startswith(NO):
            return Step(EXIT, 1) if t.download(name) != 0 else None
        t.out(dt.offline(words))
        return Step(DASHBOARD)
