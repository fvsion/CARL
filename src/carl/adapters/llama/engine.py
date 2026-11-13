"""The llama.cpp engine (WCLI, decision A39): the EngineAdapter of llama-server. It builds llama-server's argv from a
launch plan (domain/server/argv.py), reads its version and update hint from the LlamaCpp port, and shows its own help
after CARL's head, and the option names that help lists (the start refuses another option, WFIN). llama.cpp is the
only engine today; another engine is another adapter."""
from __future__ import annotations

import subprocess
import sys
from typing import FrozenSet, List

from ...domain.server import argv as server_argv
from ...domain.server.dispatch_text import ENGINE_HELP_HEAD
from ...domain.server.engines import LLAMA_CPP
from ...domain.server.launch_plan import LaunchPlan, options_in_help
from ...ports import LlamaCpp

HELP_TIMEOUT = 30.0                 # a first run of a CUDA build loads the driver (as for --version: version_probe.py)


class LlamaCppEngine:
    """llama-server. server: its program, a path or the name for the OS to look up (the locator's answer, else
    llama-server); llama: the LlamaCpp port that reads its version and says how to update it."""

    def __init__(self, llama: LlamaCpp, server: str) -> None:
        self.llama, self.server = llama, server

    def engine_name(self) -> str:
        return LLAMA_CPP

    def program(self) -> str:
        return self.server

    def server_argv(self, plan: LaunchPlan) -> List[str]:
        return server_argv.server_argv(plan)

    def router_argv(self, plan: LaunchPlan, preset: str) -> List[str]:
        return server_argv.router_argv(plan, preset)

    def version_text(self) -> str:
        return self.llama.version_text()

    def update_hint(self) -> str:
        return self.llama.update_hint()

    def show_help(self) -> int:
        """CARL's head, then llama-server's own help (--help); its exit code (127 when it cannot run)."""
        for line in ENGINE_HELP_HEAD:
            print(line, flush=True)
        sys.stderr.flush()
        try:
            return subprocess.run([self.server, "--help"]).returncode
        except OSError:
            print("error: CARL cannot find llama-server.", file=sys.stderr, flush=True)
            return 127

    def option_names(self) -> FrozenSet[str]:
        """The option names of llama-server's own help (--help, read whole); empty when it cannot run, fails or lists
        none."""
        try:
            p = subprocess.run([self.server, "--help"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                               timeout=HELP_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            return frozenset()
        return options_in_help(p.stdout) if p.returncode == 0 else frozenset()
