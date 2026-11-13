"""Runs llama-server on macOS and Linux (implements ServerRunner, contract C-020): host/common.sh run_server.

run (MONITOR=0, or not in a terminal): the start becomes the server. With a log, the log filter starts first on a
pipe (os.pipe, the filter, dup2 of the pipe onto stdout and stderr), then this process execs llama-server
(os.execvpe): its PID is the caller's, which the dashboard's jobs, Auto-tune and the benches stop it by. The filter
is in the caller's process group, so a stop of that group ends both; it keeps the caller's stdout and stderr.

start (an interactive start): llama-server in the background, in a process group of its own and deaf to a closed
terminal (the launcher's `set -m` and nohup), in the terminal's session, its output in the console file (through
the filter, which joins the server's group, when there is a log). The caller then runs the dashboard with the
server's pid; a stop of the server's group ends the server and the filter.

The filter is the hidden command `carl log-filter` (adapters/common/log_filter.py), run by python3 from PATH with the
clone's carl.py, as the launcher ran its filter.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import List, Mapping, Sequence

from .process import default_signals, own_group

PYTHON = "python3"                                   # from PATH, as the bash launcher called it


class PosixServerRunner:
    """carl_py: the clone's carl.py (the filter's entry)."""

    def __init__(self, carl_py: str) -> None:
        self.carl_py = carl_py
        self.started: List["subprocess.Popen[bytes]"] = []     # start's server and filter (the dashboard reaps them)

    def filter_argv(self, log_path: str, port: int) -> List[str]:
        return [PYTHON, self.carl_py, "log-filter", "--port", str(port), log_path]

    def run(self, argv: Sequence[str], env: Mapping[str, str], log_path: str, port: int) -> int:
        sys.stdout.flush()
        sys.stderr.flush()
        if log_path:
            r, w = os.pipe()
            try:
                subprocess.Popen(self.filter_argv(log_path, port), stdin=r, env=dict(env))
            finally:
                os.close(r)
            os.dup2(w, 1)
            os.dup2(w, 2)
            os.close(w)
        default_signals()
        try:
            os.execvpe(argv[0], list(argv), dict(env))
        except OSError as e:
            print(f"error: CARL cannot run {argv[0]}: {e.strerror or e}.", file=sys.stderr)
        return 127

    def start(self, argv: Sequence[str], env: Mapping[str, str], log_path: str, port: int, console: str) -> int:
        sys.stdout.flush()
        sys.stderr.flush()
        out = os.open(console, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o666)
        try:
            if not log_path:
                server = subprocess.Popen(list(argv), env=dict(env), stdout=out, stderr=subprocess.STDOUT,
                                          preexec_fn=own_group)
                self.started.append(server)
                return server.pid
            r, w = os.pipe()
            try:
                server = subprocess.Popen(list(argv), env=dict(env), stdout=w, stderr=subprocess.STDOUT,
                                          preexec_fn=own_group)
            finally:
                os.close(w)
            self.started.append(server)
            try:
                self.started.append(subprocess.Popen(self.filter_argv(log_path, port), stdin=r, stdout=out,
                                                     stderr=subprocess.STDOUT, env=dict(env),
                                                     preexec_fn=lambda: os.setpgid(0, server.pid)))
            finally:
                os.close(r)
            return server.pid
        finally:
            os.close(out)
