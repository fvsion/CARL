"""A program run to its end: its exit code and its output (the fetch of a llama.cpp program for a plugin,
composition/computer.py)."""
from __future__ import annotations

import subprocess
from typing import Mapping, Sequence, Tuple


def run_program(argv: Sequence[str], env: Mapping[str, str]) -> Tuple[int, str]:
    """The program's exit code and its output (both streams, as text); (127, why) when it cannot start."""
    try:
        p = subprocess.run(list(argv), capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=dict(env))
    except OSError as e:
        return 127, str(e)
    return p.returncode, (p.stdout or "") + (p.stderr or "")
