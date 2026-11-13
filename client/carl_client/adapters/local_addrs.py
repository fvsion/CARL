"""This computer's own addresses, as the config step reads them (the bash setup's checks, unchanged):

- is_ours: an address in remote.json that is one of this computer's (the server's own computer: local mode, no sync
  service). macOS lists them with ifconfig, Linux with ip; 127.0.0.1, localhost and ::1 always are.
- listen_addr: the address a server on this computer listens on for a port (--local). macOS asks netstat by its
  absolute path (/usr/sbin/netstat: lsof hangs on a stale network share), Linux asks ss. A netstat that prints nothing,
  not even its header (some Macs, to a process under Homebrew python), is not read as "no listener": lsof is asked
  then, with -b, which does not touch the file systems, and with a time limit (LSOF_TIMEOUT; an lsof that does not
  answer is "no listener found", never a free port: the start's own bind still catches a busy one).

Windows has neither yet (W7.5): no address is ours but the loopback names, and no listener is found.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from typing import Callable, List, Optional

NETSTAT = "/usr/sbin/netstat"
NETSTAT_HEADER = "Active Internet connections"
LSOF = "/usr/sbin/lsof"
LSOF_TIMEOUT = 5
LOOPBACK = re.compile(r"^(127\.0\.0\.1|localhost|::1)$")


def _text(argv: List[str], timeout: float = 30) -> Optional[str]:
    """A tool's stdout (None when it fails to start or does not finish in time; lsof gets at most LSOF_TIMEOUT). A tool
    that ignores the kill (an lsof stuck in the kernel) is not waited for: subprocess.run would block, and Ctrl-C
    could not stop it."""
    if argv and os.path.basename(argv[0]) == "lsof":
        timeout = min(timeout, LSOF_TIMEOUT)
    try:
        p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        return p.communicate(timeout=timeout)[0]
    except subprocess.TimeoutExpired:
        p.kill()
        if p.stdout:
            p.stdout.close()
        threading.Thread(target=p.wait, daemon=True).start()
        return None


class LocalAddrs:
    """platform: sys.platform; which and read: shutil.which and a tool's output (tests pass fakes)."""

    def __init__(self, platform: Optional[str] = None, which: Callable[[str], Optional[str]] = shutil.which,
                 read: Callable[[List[str]], Optional[str]] = _text) -> None:
        self.platform = platform if platform is not None else sys.platform
        self.which = which
        self.read = read

    def is_ours(self, host: str) -> bool:
        if LOOPBACK.match(host):
            return True
        if self.which("ifconfig"):
            out = self.read(["ifconfig"]) or ""
            if f"inet {host} " in out:
                return True
        if self.which("ip"):
            out = self.read(["ip", "-o", "addr"]) or ""
            if f"inet {host}/" in out:
                return True
        return False

    def listen_addr(self, port: str) -> str:
        """The address of a listener on port ("" when none): netstat's first LISTEN line on macOS (* is 127.0.0.1),
        ss's first one on Linux."""
        if self.platform == "darwin":
            out = self.read([NETSTAT, "-anv", "-p", "tcp"]) or ""
            if NETSTAT_HEADER not in out:
                names = (self.read([LSOF, "-nP", "-b", "-w", "-iTCP", "-sTCP:LISTEN", "-F", "n"]) or "").splitlines()
                for name in names:      # "n*:9276", "n127.0.0.1:9275", "n[::1]:9275"
                    a, _, p = name[1:].rpartition(":")
                    if name.startswith("n") and p == port:
                        return "127.0.0.1" if a == "*" else a.strip("[]")
                return ""
            end = re.compile(r"[.]" + port + "$")
            for line in out.splitlines():
                f = line.split()
                if len(f) >= 6 and f[5] == "LISTEN" and end.search(f[3]):
                    a = end.sub("", f[3], count=1)
                    return "127.0.0.1" if a == "*" else a
            return ""
        if self.platform != "win32" and self.which("ss"):
            lines = (self.read(["ss", "-Hltn", f"sport = :{port}"]) or "").splitlines()
            f = lines[0].split() if lines else []
            return f[3].split(":")[0] if len(f) >= 4 else ""
        return ""
