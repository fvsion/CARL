"""What a llama.cpp program says about its version: `llama-server --version` (implements the version_text of
LlamaCpp, contract C-022). Its output is read by domain/llama/version.py."""
from __future__ import annotations

import subprocess
from typing import Optional

TIMEOUT = 30.0                     # a first start of a CUDA build loads the driver: it can take a while


def version_text(program: Optional[str], timeout: float = TIMEOUT) -> str:
    """What `program --version` prints (the standard output, then the error output); "" when there is no program or
    it cannot run."""
    if not program:
        return ""
    try:
        r = subprocess.run([program, "--version"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout + r.stderr
