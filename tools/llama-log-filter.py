#!/usr/bin/env python3
"""CARL's log filter for a llama-server that runs at -lv 4 (Phase 23.4.4 item 12: the RAM cache count).

    llama-server ... -lv 4 --log-timestamps --log-prefix 2>&1 | llama-log-filter.py --port PORT LOG_FILE

The server writes its output here, not to a --log-file. The filter writes the lines CARL keeps (carl_core's
serverlog.keep_line: the default level's lines, every warning and error, the RAM cache's lines; not the per-request
detail of -lv 4) to LOG_FILE and to its own output (the terminal, or the launcher's console file), each line at once.
It also writes ~/models/logs/.log-PORT.json ({"log": LOG_FILE, "pid": its pid}): the dashboard finds the log there (the server's command line has no --log-file) and sees when the filter
stopped while the server runs.

It ends only when its input ends (the server stopped): Ctrl-C, a closed terminal (SIGHUP) and SIGTERM do not stop
it, so it keeps the server's last lines; the server's own stop closes the pipe. A write error (a full disk, a
closed terminal) does not stop it either: it goes on with the other output.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from typing import BinaryIO, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from carl_core.domain.serverlog import keep_line  # noqa: E402  (after the path set-up above)


def pointer_path(home: str, port: int) -> str:
    """Where the filter says which log it writes: ~/models/logs/.log-PORT.json."""
    return os.path.join(home, "models", "logs", f".log-{port}.json")


def write_pointer(path: str, log: str) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"log": log, "pid": os.getpid()}, f)
        os.replace(tmp, path)
    except OSError:
        pass                                # the dashboard then shows the console output


def run(src: BinaryIO, log: BinaryIO, out: Optional[BinaryIO]) -> None:
    """Copy the kept lines of src to log and out, line by line."""
    kept = True
    for raw in iter(src.readline, b""):
        text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        kept = keep_line(_plain(text), kept)
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


def _plain(text: str) -> str:
    """The line without ANSI colour codes (llama.cpp colours its output on a terminal)."""
    out, i = [], 0
    while i < len(text):
        if text[i] == "\x1b" and i + 1 < len(text) and text[i + 1] == "[":
            j = i + 2
            while j < len(text) and not ("@" <= text[j] <= "~"):
                j += 1
            i = j + 1
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="CARL's filter for the log of a llama-server at -lv 4.")
    ap.add_argument("log", help="the log file to write (appended)")
    ap.add_argument("--port", type=int, required=True, help="the server's port (for ~/models/logs/.log-PORT.json)")
    ap.add_argument("--home", default=os.path.expanduser("~"))
    a = ap.parse_args(argv)
    for sig in (signal.SIGINT, signal.SIGHUP, signal.SIGTERM):
        signal.signal(sig, signal.SIG_IGN)
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)
    write_pointer(pointer_path(a.home, a.port), os.path.abspath(a.log))
    try:
        os.makedirs(os.path.dirname(os.path.abspath(a.log)), exist_ok=True)
        log: Optional[BinaryIO] = open(a.log, "ab")
    except OSError:
        log = None
    try:
        run(sys.stdin.buffer, log, sys.stdout.buffer) if log else run(sys.stdin.buffer, sys.stdout.buffer, None)
    finally:
        if log:
            log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
