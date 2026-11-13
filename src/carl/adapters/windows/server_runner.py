"""Runs llama-server on Windows (implements ServerRunner, contract C-020). Windows has no exec and no process
groups, so the server is always a child of its own (adapters/windows/process.py: a hidden console, out of the
caller's job), with its output on a pipe to the log filter (`carl log-filter`, run by this Python):

  run    (MONITOR=0, or not in a terminal) the server and the filter end with CARL (a Job object), the filter writes
         to this terminal, and CARL waits for the server. Ctrl-C reaches CARL alone (the server has its own console):
         CARL stops the server with the console stop (WindowsProcesses.stop), then gives its exit code.
  start  (an interactive start) the server and the filter both have hidden consoles of their own and write the
         console file; a closed window or an ended ssh session leaves them running. follow() then shows the console
         file in this terminal until the server ends; Ctrl-C there stops it (the dashboard takes this place when it
         runs on Windows).

Each start records its process in server-PORT.json, on Windows only: "pid", "started" (the process's creation time,
a FILETIME, so a pid Windows gave to another process is not taken for the server), "argv" (the dashboard and the
process table read it: Windows does not show another process's command line) and "log".

llama-server cannot open a path with a letter outside ASCII (W0.3: its --api-key-file under a home with an accent;
decision A3). ascii_paths gives such paths as their 8.3 short form; a key file that has no short form (8.3 names off
on the volume) goes to the server as LLAMA_API_KEY in its environment instead, so the key never shows in its
command line.
"""
from __future__ import annotations

import ntpath
import os
import subprocess
import sys
import threading
import time
from typing import BinaryIO, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ..common import runtime_files
from ..common.key_store import read_key
from .process import Job, WindowsProcesses, creation_time, popen, short_path, terminate

KEY_FLAG = "--api-key-file"
KEY_ENV = "LLAMA_API_KEY"
STOP_TIMEOUT = 30.0                       # seconds for the server to end after Ctrl-C, before TerminateProcess
FILTER_WAIT = 10.0                        # seconds for the filter to write the server's last lines
POLL = 0.25
RECORD_KEYS = ("pid", "started", "argv", "log")
FOLLOWING = ("The server runs in the background (process {pid}). Its output follows here. Press Ctrl-C to stop it; "
             "closing this window leaves it running.")
STOPPING = "Stopping the server."
STOPPED = "The server stopped."
FIREWALL = ("note: the server listens on {host}, so Windows may ask whether llama-server may use the network. Allow it "
            "for private networks, or other computers cannot reach it. CARL adds no firewall rule.")


def ascii_path(path: str, short: Callable[[str], str], exists: Callable[[str], bool]) -> Optional[str]:
    """path in ASCII letters: its short form, or its folder's short form and its own name (a file not made yet);
    None when there is none."""
    if exists(path):
        s = short(path)
        return s if s.isascii() else None
    folder, name = ntpath.split(path)
    if folder and name.isascii() and exists(folder):
        s = short(folder)
        return ntpath.join(s, name) if s.isascii() else None
    return None


def ascii_paths(argv: Sequence[str], env: Mapping[str, str], short: Callable[[str], str] = short_path,
                exists: Callable[[str], bool] = os.path.exists, read: Callable[[str], str] = read_key,
                ) -> Tuple[List[str], Dict[str, str]]:
    """The server's argv and environment with every path argument in ASCII letters. The program itself needs none
    (Windows starts it by its full name). A key file with no ASCII form leaves the argv; its key goes into the
    environment."""
    out, new_env = [argv[0]], dict(env)
    for a in argv[1:]:
        out.append(a if a.isascii() else (ascii_path(a, short, exists) or a))
    if KEY_FLAG in out[1:-1]:
        i = out.index(KEY_FLAG, 1)
        if not out[i + 1].isascii():
            key = read(out[i + 1])
            if key:
                new_env[KEY_ENV] = key
                del out[i:i + 2]
    return out, new_env


def lan_host(argv: Sequence[str]) -> Optional[str]:
    """The address of --host when it is not this computer's loopback (a LAN bind: the firewall's prompt), else None."""
    args = list(argv)
    if "--host" not in args[:-1]:
        return None
    host = args[args.index("--host") + 1]
    return None if host.startswith("127.") or host in ("localhost", "::1") else host


def recorded(conf_dir: str, port: int, pid: int, started: Optional[int], argv: Sequence[str], log: str) -> bool:
    """The process of a start into the port's server-PORT.json (False when there is no file: nothing is made)."""
    doc = runtime_files.read(conf_dir, port)
    if not doc:
        return False
    for key in RECORD_KEYS:
        doc.pop(key, None)
    doc.update({"pid": int(pid), "started": started, "argv": list(argv), "log": log})
    runtime_files.write_doc(conf_dir, port, doc)
    return True


def recorded_start(conf_dir: str, pid: int) -> Optional[int]:
    """The creation time a start recorded for the server with this pid (None when no server-PORT.json has it)."""
    import glob
    for path in glob.glob(os.path.join(glob.escape(conf_dir), "server-*.json")):
        name = os.path.basename(path)[len("server-"):-len(".json")]
        if not name.isdigit():
            continue
        doc = runtime_files.read(conf_dir, int(name))
        if doc.get("pid") == pid and isinstance(doc.get("started"), int):
            return int(doc["started"])
    return None


def _copy(src: int, sink: BinaryIO) -> None:
    """The server's output to this terminal, line by line, until the pipe ends (a run without the filter)."""
    with os.fdopen(src, "rb") as f:
        for line in iter(f.readline, b""):
            try:
                sink.write(line)
                sink.flush()
            except (OSError, ValueError):
                pass


class WindowsServerRunner:
    """carl_py: the clone's carl.py (the filter's entry); conf_dir: the settings folder (server-PORT.json); python:
    the Python that runs the filter; processes: the stop (WindowsProcesses); out, note: this terminal's lines and
    the notes on stderr; paths: ascii_paths (the tests give their own)."""

    def __init__(self, carl_py: str, conf_dir: str, python: str = "",
                 processes: Optional[WindowsProcesses] = None, out: Optional[Callable[[str], None]] = None,
                 note: Optional[Callable[[str], None]] = None,
                 paths: Callable[[Sequence[str], Mapping[str, str]], Tuple[List[str], Dict[str, str]]] = ascii_paths,
                 ) -> None:
        self.carl_py, self.conf_dir, self.python = carl_py, conf_dir, python or sys.executable
        self.note = note or (lambda line: print(line, file=sys.stderr, flush=True))
        self.out = out or (lambda line: print(line, flush=True))
        self.processes = processes or WindowsProcesses(lambda pid: recorded_start(conf_dir, pid), note=self.note)
        self.paths = paths
        self.started: Dict[int, "subprocess.Popen[bytes]"] = {}    # start's server, by pid (follow reads its code)
        self.filters: List["subprocess.Popen[bytes]"] = []

    def filter_argv(self, log_path: str, port: int) -> List[str]:
        return [self.python, self.carl_py, "log-filter", "--port", str(port), log_path]

    def run(self, argv: Sequence[str], env: Mapping[str, str], log_path: str, port: int) -> int:
        sys.stdout.flush()
        sys.stderr.flush()
        args, server_env = self._prepare(argv, env)
        job = Job()
        r, w = os.pipe()
        try:
            server = popen(args, server_env, subprocess.DEVNULL, w, subprocess.STDOUT, note=self.note)
        except OSError as e:
            os.close(r)
            self.note(f"error: CARL cannot run {argv[0]}: {e.strerror or e}.")
            return 127
        finally:
            os.close(w)
        job.add(server.pid)
        self.processes.children[server.pid] = server
        copier: Optional[threading.Thread] = None
        filt: Optional["subprocess.Popen[bytes]"] = None
        if log_path:
            try:
                filt = popen(self.filter_argv(log_path, port), dict(env), r, None, None, hidden=False)
                job.add(filt.pid)
            finally:
                os.close(r)
        else:
            copier = threading.Thread(target=_copy, args=(r, sys.stdout.buffer), daemon=True)
            copier.start()
        self._record(port, server.pid, args, log_path)
        code = self._wait(server)
        try:
            if filt is not None:
                filt.wait(FILTER_WAIT)
            elif copier is not None:
                copier.join(FILTER_WAIT)
        except subprocess.TimeoutExpired:
            pass
        job.close()
        return code

    def start(self, argv: Sequence[str], env: Mapping[str, str], log_path: str, port: int, console: str) -> int:
        sys.stdout.flush()
        sys.stderr.flush()
        args, server_env = self._prepare(argv, env)
        with open(console, "wb") as out:
            if not log_path:
                server = popen(args, server_env, subprocess.DEVNULL, out, subprocess.STDOUT, note=self.note)
            else:
                r, w = os.pipe()
                try:
                    server = popen(args, server_env, subprocess.DEVNULL, w, subprocess.STDOUT, note=self.note)
                except OSError:
                    os.close(r)
                    raise
                finally:
                    os.close(w)
                try:
                    self.filters.append(popen(self.filter_argv(log_path, port), dict(env), r, out,
                                              subprocess.STDOUT))
                finally:
                    os.close(r)
        self.started[server.pid] = server
        self.processes.children[server.pid] = server
        self._record(port, server.pid, args, log_path)
        return server.pid

    def follow(self, pid: int, console: str, sink: Optional[BinaryIO] = None) -> int:
        """The console file in this terminal while the server runs; Ctrl-C stops the server (the console stop). 0
        once it stopped, else the server's exit code."""
        sink = sink or sys.stdout.buffer
        self.out(FOLLOWING.format(pid=pid))
        at = 0
        try:
            while self.processes.alive(pid):
                at = self._show(console, at, sink)
                time.sleep(POLL)
        except KeyboardInterrupt:
            self.out(STOPPING)
            self._stop(pid)
            self._show(console, at, sink)
            self.out(STOPPED)
            return 0
        self._show(console, at, sink)
        child = self.started.get(pid)
        code = child.poll() if child is not None else None
        return code if code is not None else 0

    # ------------------------------------------------------------ the parts
    def _prepare(self, argv: Sequence[str], env: Mapping[str, str]) -> Tuple[List[str], Dict[str, str]]:
        """The argv and environment in ASCII paths; the firewall's note for a LAN bind."""
        host = lan_host(argv)
        if host:
            self.note(FIREWALL.format(host=host))
        return self.paths(argv, env)

    def _wait(self, server: "subprocess.Popen[bytes]") -> int:
        """The server's exit code; Ctrl-C (here: CARL's console only) stops it first. The wait has a timeout, because
        Python sees Ctrl-C on Windows only when a wait returns (one without a timeout never does: the Windows test PC, 2026-10-26)."""
        while True:
            try:
                return server.wait(POLL)
            except subprocess.TimeoutExpired:
                continue
            except KeyboardInterrupt:
                self.out(STOPPING)
                self._stop(server.pid)

    def _stop(self, pid: int) -> None:
        """The console stop, then TerminateProcess after its timeout; a second Ctrl-C ends it at once."""
        try:
            self.processes.stop(pid, STOP_TIMEOUT)
        except KeyboardInterrupt:
            terminate(pid)

    @staticmethod
    def _show(console: str, at: int, sink: BinaryIO) -> int:
        """What the console file has after offset at, to sink; the new offset."""
        try:
            with open(console, "rb") as f:
                f.seek(at)
                data = f.read()
        except OSError:
            return at
        if data:
            try:
                sink.write(data)
                sink.flush()
            except (OSError, ValueError):
                pass
        return at + len(data)

    def _record(self, port: int, pid: int, argv: Sequence[str], log_path: str) -> None:
        try:
            recorded(self.conf_dir, port, pid, creation_time(pid), argv, log_path)
        except (OSError, ValueError):
            pass                                  # the clients' file; never in the way of a start
