"""CARL's log filter for a llama-server that runs at -lv 4 (Phase 23.4.4 item 12: the RAM cache count).

    llama-server ... -lv 4 --log-timestamps --log-prefix 2>&1 | carl log-filter --port PORT LOG_FILE

The hidden command `log-filter` runs it (cli/start.py). The
server writes its output here, not to a --log-file. The filter writes the lines CARL keeps (the domain's
serverlog.keep_line: the default level's lines, every warning and error, the RAM cache's lines; not the per-request
detail of -lv 4) to LOG_FILE and to its own output (the terminal, or the launcher's console file), each line at once.
It also writes ~/models/logs/.log-PORT.json ({"log": LOG_FILE, "pid": its pid}): the dashboard finds the log there
(the server's command line has no --log-file) and sees when the filter stopped while the server runs.

It ends only when its input ends (the server stopped): Ctrl-C, a closed terminal (SIGHUP) and SIGTERM do not stop
it, so it keeps the server's last lines; the server's own stop closes the pipe. A write error (a full disk, a
closed terminal) does not stop it either: it goes on with the other output. Contract C-218.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from typing import BinaryIO, Callable, List, Optional

from ...domain.server.serverlog import keep_line
from .json_files import write_private

IGNORED = ("SIGINT", "SIGHUP", "SIGTERM", "SIGPIPE")     # SIGHUP and SIGPIPE do not exist on Windows


def pointer_path(home: str, port: int) -> str:
    """Where the filter says which log it writes: ~/models/logs/.log-PORT.json."""
    return os.path.join(home, "models", "logs", f".log-{port}.json")


def write_pointer(path: str, log: str) -> None:
    """The pointer file, replaced whole by the one atomic writer (mode 0644, as before)."""
    try:
        write_private(path, json.dumps({"log": log, "pid": os.getpid()}), mode=0o644, dir_mode=0o777)
    except OSError:
        pass                                # the dashboard then shows the console output


def run(src: BinaryIO, log: BinaryIO, out: Optional[BinaryIO], strip: Callable[[str], str] = lambda s: s) -> None:
    """Copy the kept lines of src to log and out, line by line. strip: the terminal colours out of a line before the
    rule reads it (ui/ansi.py strip_ansi; the lines are written with their colours)."""
    kept = True
    for raw in iter(src.readline, b""):
        text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        kept = keep_line(strip(text), kept)
        if not kept:
            continue
        for sink in (log, out):
            if sink is None:
                continue
            try:
                sink.write(raw if raw.endswith(b"\n") else raw + b"\n")
                sink.flush()
            except (OSError, ValueError):
                pass


def ignore_signals() -> None:
    """Ctrl-C, a closed terminal, SIGTERM and a closed output do not stop the filter: only the end of its input."""
    for name in IGNORED:
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), signal.SIG_IGN)


def main(argv: Optional[List[str]], home: str, strip: Callable[[str], str]) -> int:
    """The filter's command line: LOG_FILE --port PORT [--home HOME]; home: the pointer's home folder (the user's,
    from the composition), unless --home names another; strip: as run()'s."""
    ap = argparse.ArgumentParser(description="CARL's filter for the log of a llama-server at -lv 4.")
    ap.add_argument("log", help="the log file to write (appended)")
    ap.add_argument("--port", type=int, required=True, help="the server's port (for ~/models/logs/.log-PORT.json)")
    ap.add_argument("--home", default=None)                        # the pointer's home folder (default: the user's)
    a = ap.parse_args(argv)
    ignore_signals()
    write_pointer(pointer_path(a.home or home, a.port), os.path.abspath(a.log))
    try:
        os.makedirs(os.path.dirname(os.path.abspath(a.log)), exist_ok=True)
        log: Optional[BinaryIO] = open(a.log, "ab")
    except OSError:
        log = None
    try:
        if log:
            run(sys.stdin.buffer, log, sys.stdout.buffer, strip)
        else:
            run(sys.stdin.buffer, sys.stdout.buffer, None, strip)
    finally:
        if log:
            log.close()
    return 0
