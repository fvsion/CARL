"""The TCP sockets of this Mac from `netstat -anv -p tcp`: the one parser (the launcher's port checks, Auto-tune and
the dashboard read it), and the start's PortProbe (NetstatPorts). parse_netstat, parse_listening_ports and parse_lsof
are pure and take the command's text output.

netstat can run and show nothing at all: on a Mac whose netstat is blind to a process with a Homebrew python above it
(macOS 27, measured 2026-11-01) it exits 0 with no output, not even its header. That is "cannot read", not "no
sockets": tcp_rows then reads the sockets from lsof (by its flags, one that does not stat the filesystems). lsof lists
only the sockets of the user's own processes; a port it does not show is still caught by the bind of NetstatPorts.free.

Every lsof call has a time limit (LSOF_TIMEOUT, decision A60), and run_limited does not wait for a child that ignores
the kill. An lsof that does not answer in time is read as no output: the sockets are unreadable, never "the port is
free" (NetstatPorts.free still binds)."""
from __future__ import annotations

import os
import re
import socket
import subprocess
import threading
from typing import Callable, List, NamedTuple, Optional, Sequence, Set

NETSTAT = "/usr/sbin/netstat"
ARGS = [NETSTAT, "-anv", "-p", "tcp"]               # by its absolute path (a golden test checks it)
NETSTAT_HEADER = "Active Internet connections"      # first line of every netstat that could read the sockets
LSOF = "/usr/sbin/lsof"
LSOF_ARGS = [LSOF, "-nP", "-b", "-w", "-iTCP", "-F", "pnT"]   # -b: no stat of a mounted share; -F: one field a line
TIMEOUT = 10
LSOF_TIMEOUT = 5                                    # an lsof that has not answered by then is given up on, not waited for


class TcpRow(NamedTuple):
    """One TCP socket from netstat."""
    local_addr: str
    local_port: int
    remote: str         # address.port
    state: str
    pid: Optional[int]


_NETSTAT_PID = re.compile(r":(\d+)\s+\d{5}\s")     # "process:pid  00100": process names may hold spaces


def parse_netstat(text: str) -> List[TcpRow]:
    """TCP sockets from `netstat -anv -p tcp` output."""
    rows = []
    for line in text.splitlines():
        f = line.split()
        if len(f) < 6 or not f[0].startswith("tcp"):
            continue
        m = _NETSTAT_PID.search(line)
        la, _, lp = f[3].rpartition(".")
        if lp.isdigit():
            rows.append(TcpRow(la, int(lp), f[4], f[5], int(m.group(1)) if m else None))
    return rows


def parse_listening_ports(text: str) -> Set[int]:
    """TCP ports in LISTEN state (local address like *.8080)."""
    return {r.local_port for r in parse_netstat(text) if r.state == "LISTEN"}


def parse_lsof(text: str) -> List[TcpRow]:
    """TCP sockets from `lsof -F pnT` output (p: pid; n: local[->remote]; TST=: state), as netstat's rows."""
    rows: List[TcpRow] = []
    pid: Optional[int] = None
    name = state = ""

    def flush() -> None:
        nonlocal name, state
        local, _, remote = name.partition("->")
        la, _, lp = local.rpartition(":")
        if state and lp.isdigit():
            rh, _, rp = remote.rpartition(":")
            row = TcpRow(la.strip("[]"), int(lp), f"{rh.strip('[]')}.{rp}" if remote else "*.*", state, pid)
            if row not in rows:                 # one socket held by several descriptors is one row
                rows.append(row)
        name = state = ""

    for line in text.splitlines():
        tag, val = line[:1], line[1:]
        if tag in ("p", "f"):
            flush()
            if tag == "p":
                pid = int(val) if val.isdigit() else None
        elif tag == "n":
            name = val
        elif tag == "T" and val.startswith("ST="):
            state = val[3:]
    flush()
    return rows


def tcp_rows(run: Callable[[Sequence[str]], str]) -> List[TcpRow]:
    """This Mac's TCP sockets; run(argv) is the text a command printed ("" when it cannot run). netstat's output
    without its header is unreadable, not an empty list: the sockets are then read from lsof."""
    text = run(ARGS)
    if NETSTAT_HEADER in text:
        return parse_netstat(text)
    return parse_lsof(run(LSOF_ARGS))


def run_limited(argv: Sequence[str], timeout: float = TIMEOUT) -> str:
    """A command's output ("" when it cannot run or does not finish in time; lsof gets at most LSOF_TIMEOUT). All of
    it is read: a pipe that ends early cannot make a busy port look free (a353e86's `netstat | awk` under pipefail did,
    decision A2). A command that ignores the kill (stuck in the kernel) is not waited for: subprocess.run would
    block, and Ctrl-C could not stop it."""
    if argv and os.path.basename(argv[0]) == "lsof":
        timeout = min(timeout, LSOF_TIMEOUT)
    try:
        p = subprocess.Popen(list(argv), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                             text=True, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return ""
    try:
        out: str = p.communicate(timeout=timeout)[0]
        return out
    except subprocess.TimeoutExpired:
        p.kill()
        if p.stdout:
            p.stdout.close()                # our end of the pipe: closing it does not wait for the child
        threading.Thread(target=p.wait, daemon=True).start()      # reaps it whenever it does die
        return ""


_run = run_limited


class NetstatPorts:
    """This Mac's TCP listeners (implements PortProbe, contract C-017). The owner of a port is the first LISTEN
    socket on it (netstat lists the newest first), on any address, as host/common.sh port_pid read it."""

    def listening(self) -> Set[int]:
        return {r.local_port for r in tcp_rows(_run) if r.state == "LISTEN"}

    def owner(self, port: int) -> Optional[int]:
        if not 1 <= port <= 65535:
            return None
        for r in tcp_rows(_run):
            if r.state == "LISTEN" and r.local_port == port:
                return r.pid
        return None

    def free(self, host: str, port: int) -> bool:
        with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET) as s:
            try:
                s.bind((host, port))
                return True
            except OSError:
                return False
