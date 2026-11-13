"""The second-model guard rule: a process that probably holds a model. Two models do not fit in the GPU memory: on
a Mac the second one can stop the Mac (host/common.sh guard_other_models; it restarted on 2026-10-02), on a
discrete GPU it takes the VRAM the first one plans with. ALLOW_SECOND_MODEL=1 skips the guard (the callers read it).

Two forms, both pure (the process lists come from the adapters: macos/ps.py, windows/processes.py):
  - by memory and name (a Mac, Auto-tune): over BIG_GB resident, or a known model server. Name checks miss servers
    started some other way (an app, a renamed binary), so the memory counts too.
  - by name only (a discrete GPU): a second model there shows as less free VRAM, which the memory check counts; the
    resident size would also catch other large programs (a training job), so it is not used.
The names match with either separator and with .exe (C:\\llama\\llama-server.exe).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Sequence, Tuple

from ..shared.paths import basename_any

# mtplx stays although CARL no longer starts it: a leftover MTPLX server still holds a model.
_MODEL_SERVER = re.compile(r"(^|[/\\])(llama-server|mtplx|ollama|LM Studio)")
# The program names of the known model servers on a discrete GPU (tasklist's image names).
_SERVER_NAME = re.compile(r"^(llama-server|ollama|ollama_llama_server|lm studio|lms)(\.exe)?$", re.I)


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    rss_kib: int
    command: str


def is_model_server(command: str) -> bool:
    """A known model server's command (a path with / or \\, or a bare name)."""
    return bool(_MODEL_SERVER.search(command))


def blocking_processes(procs: Sequence[ProcessInfo], big_kib: int, own_pid: int) -> List[str]:
    """Processes that probably hold a model (over big_kib resident, or a known model
    server): a second model can crash the Mac. Like host/common.sh guard_other_models."""
    return [f"pid {p.pid} ({basename_any(p.command)}, {p.rss_kib / 2 ** 20:.1f} GiB)"
            for p in procs if p.pid != own_pid and (p.rss_kib > big_kib or is_model_server(p.command))]


def model_servers(procs: Sequence[Tuple[str, int]], own_pid: int) -> List[Tuple[str, int]]:
    """The (program name, pid) of the known model servers, by name only (a discrete GPU)."""
    return [(n, p) for n, p in procs if _SERVER_NAME.match(basename_any(n)) and p != own_pid]
