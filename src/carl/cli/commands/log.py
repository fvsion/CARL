"""`carl log [-f] [FILE]`: a llama-server log as plain text, without its colour codes (the server keeps its colours,
so the log file holds them too). It replaces tools/llama-log.sh, on every OS.

  carl log            the log of the server on PORT (the log filter's ~/models/logs/.log-PORT.json names it), else
                      ~/models/logs/llama-server-latest.log
  carl log -f         its last 50 lines, then each new line (as tail -F: a log that is replaced or cut is read again
                      from its start); Ctrl-C ends it (exit 130)
  carl log [-f] FILE  another log

No log: one line on stderr and exit 1. Contract C-218 (the log files), C-420 (the command).
"""
from __future__ import annotations

import os
import sys
import time
from collections import deque
from typing import IO, List, Optional, Tuple

from ...domain.shared.ansi import strip_ansi
from .base import Cli

TAIL_LINES = 50
POLL_S = 0.5


def run(cli: Cli, argv: List[str], sleep=time.sleep, polls: Optional[int] = None) -> int:
    """polls: how many times -f looks for new lines (None: until Ctrl-C; a test gives a number)."""
    follow = argv[:1] == ["-f"]
    args = argv[1:] if follow else argv
    path = args[0] if args else cli.tools.log_file()
    if not os.path.exists(path):
        print(f"no log at {path}", file=sys.stderr)
        return 1
    try:
        if not follow:
            with open(path, encoding="utf-8", errors="replace", newline="") as f:
                for line in f:
                    write(line)
            return 0
        return tail(path, sleep, polls)
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:                         # carl log | head: the reader ended
        devnull = os.open(os.devnull, os.O_WRONLY)  # no second error when Python flushes stdout at its exit
        os.dup2(devnull, sys.stdout.fileno())
        return 0


def write(line: str) -> None:
    sys.stdout.write(strip_ansi(line))
    sys.stdout.flush()


def tail(path: str, sleep, polls: Optional[int]) -> int:
    """The last TAIL_LINES lines, then the new ones; a file that is replaced (another inode) or cut is read again."""
    f, ident = _open(path)
    if f is not None:
        for line in deque(f, TAIL_LINES):
            write(line)
    n = 0
    while polls is None or n < polls:
        n += 1
        if f is not None:
            for line in f:
                write(line)
        sleep(POLL_S)
        now = _ident(path)
        if now is None:
            continue
        if f is None or now[0] != ident[0] or now[1] < f.tell():
            if f is not None:
                f.close()
            f, ident = _open(path)
    if f is not None:
        f.close()
    return 0


def _ident(path: str) -> Optional[Tuple[int, int]]:
    """(inode, size) of the file, None when it is gone."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_ino, st.st_size


def _open(path: str) -> Tuple[Optional[IO[str]], Tuple[int, int]]:
    try:
        f = open(path, encoding="utf-8", errors="replace", newline="")
        st = os.fstat(f.fileno())
    except OSError:
        return None, (0, 0)
    return f, (st.st_ino, st.st_size)
