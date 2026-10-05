"""Command-line arguments and the environment variables the dashboard reads."""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from carl_core.adapters.api_key import key_file


@dataclass
class Options:
    """The command line and environment, read once at start."""
    host: Optional[str]         # --host: the server address (default: wherever it listens)
    port: int
    lines: int                  # log lines on the Live tab (full detail)
    interval: float             # seconds between refreshes
    log: Optional[str]
    server_pid: Optional[int]   # the launcher's server
    console: Optional[str]      # its console output file
    once: bool
    tab: int                    # --once: which tab (1-5; 0 = Live)
    expand: bool
    # from the environment
    home: str = ""
    demo: bool = False          # CARL_DEMO=1: README screenshots hide the key and the home path
    key_file: str = ""          # API_KEY_FILE, else ~/.config/carl/api-key (carl_core.domain.apikey)
    env_host: Optional[str] = None      # HOST
    vm_addr: str = "192.168.42.1"       # VM_HOST


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    v = env.get(name, "")
    return int(v) if v.strip().isdigit() else default


def parse(argv: Optional[Sequence[str]], description: str, env: Mapping[str, str]) -> Options:
    """Options from argv (None: sys.argv) and env; --help prints description."""
    ap = argparse.ArgumentParser(description=description, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=None, help="server address (default: wherever the server on --port listens)")
    ap.add_argument("--port", type=int, default=_env_int(env, "PORT", 8080), help="server port (default 8080)")
    ap.add_argument("--lines", type=int, default=6, help="log lines on the Live tab, full detail (default 6)")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between refreshes (default 2)")
    ap.add_argument("--log", default=None, help="log file (default: the running server's --log-file)")
    ap.add_argument("--server-pid", type=int, default=None, help="PID of the server to watch (set by the launcher)")
    ap.add_argument("--owner", action="store_true", help=argparse.SUPPRESS)     # set by the launcher; nothing reads it
    ap.add_argument("--console", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--once", action="store_true", help="print one snapshot and exit")
    ap.add_argument("--tab", type=int, default=0, choices=range(0, 6), metavar="N", help=argparse.SUPPRESS)
    ap.add_argument("--expand", action="store_true", help="start with every card fully detailed")
    a = ap.parse_args(argv)
    home = os.path.expanduser("~")
    return Options(host=a.host, port=a.port, lines=a.lines, interval=a.interval, log=a.log, server_pid=a.server_pid,
                   console=a.console, once=a.once, tab=a.tab, expand=a.expand,
                   home=home, demo=env.get("CARL_DEMO") == "1",
                   key_file=key_file(env, home),
                   env_host=env.get("HOST") or None, vm_addr=env.get("VM_HOST", "192.168.42.1"))
