"""Running a GPU tool (nvidia-smi, llama-server --list-devices): from an argument list, its text output. A tool that is
missing, fails to start or times out gives "", never an error.
"""
from __future__ import annotations

import subprocess
from typing import Mapping, Optional, Sequence

TIMEOUT = 15.0


def run_text(args: Sequence[str], timeout: float = TIMEOUT) -> str:
    """stdout of a command, "" when it is missing, fails to start or times out."""
    try:
        return subprocess.run(list(args), capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def run_logged(args: Sequence[str], timeout: float = TIMEOUT, env: Optional[Mapping[str, str]] = None) -> str:
    """stdout and stderr of a command, in that order, "" when it is missing, fails to start or times out (llama-server
    writes its Vulkan device lines, with the device type, on stderr). env: the command's environment (None: ours)."""
    try:
        done = subprocess.run(list(args), capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, env=dict(env) if env is not None else None)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout + done.stderr

