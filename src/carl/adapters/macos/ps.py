"""Processes from macOS ps: the one parser (the second-model guard, Auto-tune and the dashboard read it). The parse_*
functions are pure and take the command's text output; PsTable runs ps for the start."""
from __future__ import annotations

import os
import re
import subprocess
from typing import List, NamedTuple, Optional

from ...domain.hardware.processes import ProcessInfo

LIST_ARGS = ["ps", "-axo", "pid=,rss=,comm="]          # every process: parse_processes
ROW_FIELDS = "rss=,%cpu=,etime=,command="              # one process (ps -o ROW_FIELDS -p PID): parse_ps_row
TIMEOUT = 20


class PsRow(NamedTuple):
    """One process as `ps -o rss=,%cpu=,etime=,command=` shows it."""
    rss_kib: int
    cpu: float          # percent
    etime: str          # [[dd-]hh:]mm:ss
    command: str


_PS = re.compile(r"\s*(\d+)\s+([\d.]+)\s+(\S+)\s+(.*)")


def parse_processes(ps_output: str) -> List[ProcessInfo]:
    """`ps -axo pid=,rss=,comm=` lines (rss in KiB)."""
    out: List[ProcessInfo] = []
    for line in ps_output.splitlines():
        f = line.split(None, 2)
        if len(f) == 3 and f[0].isdigit() and f[1].isdigit():
            out.append(ProcessInfo(int(f[0]), int(f[1]), f[2]))
    return out


def parse_ps_row(text: str) -> Optional[PsRow]:
    """The first line of `ps -o rss=,%cpu=,etime=,command=` output, or None."""
    m = _PS.match(text)
    if not m:
        return None
    return PsRow(int(m.group(1)), float(m.group(2)), m.group(3), m.group(4).strip())


class PsTable:
    """This Mac's processes from `ps -axo pid=,rss=,comm=` (implements ProcessTable, contract C-018): the
    second-model guard and the port check read them. ps by its name, as the launcher called it."""

    def processes(self) -> List[ProcessInfo]:
        try:
            out = subprocess.run(LIST_ARGS, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                 timeout=TIMEOUT).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        return parse_processes(out)

    def alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True
