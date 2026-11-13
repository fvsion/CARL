"""llama.cpp's no-alloc dry run of a start (implements MemoryDryRun, contract C-059): `llama-fit-params --fit-print on` with
the flags of the start (domain/memory/dry_run.py), its answer kept in the settings folder by the build, the model file
(size and time) and the flags, so the same start asks llama.cpp once. A run that cannot happen (no program, a failed
or a timed-out run, an unreadable model) gives None and the start says nothing: the dry run is only a cross-check.
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Callable, Dict, Optional, Sequence, Tuple

from ...domain.memory import dry_run
from ...ports import LlamaCpp
from ..common.json_files import write_private

TIMEOUT = 120.0                    # a model of 20 GiB reads its header in a second; a first CUDA run loads the driver
KEPT = 200                         # answers kept in the file; the oldest go first
FILE = "dry-runs.json"


def run_program(argv: Sequence[str], timeout: float = TIMEOUT) -> Optional[str]:
    """The standard output of a program, None when it cannot run or exits with a failure."""
    try:
        r = subprocess.run(list(argv), capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


class LlamaDryRun:
    """The MemoryDryRun of llama.cpp. llama: where its programs are; conf_dir: the settings folder (the answers file);
    run: the program runner (the tests give theirs); stat: (size, time) of a file, None when it is not there."""

    def __init__(self, llama: LlamaCpp, conf_dir: str, run: Callable[[Sequence[str]], Optional[str]] = run_program,
                 stat: Optional[Callable[[str], Optional[Tuple[int, float]]]] = None) -> None:
        self.llama, self.conf_dir, self.run_program, self.stat = llama, conf_dir, run, stat or _stat

    def run(self, server_argv: Sequence[str]) -> Optional[str]:
        program = self.llama.fit_params()
        args = dry_run.argv_of(server_argv)
        model = _value(args, "-m", "--model")
        info = self.stat(model) if model else None
        if not program or info is None:
            return None
        key = dry_run.cache_key(self.llama.version_text().strip(), info[0], info[1], args)
        kept = self._read()
        if key in kept:
            return kept[key]
        out = self.run_program([program] + args)
        if out is not None and dry_run.parse(out) is not None:
            kept = {k: v for k, v in list(kept.items())[-(KEPT - 1):]}
            kept[key] = out
            self._write(kept)
        return out

    def _path(self) -> str:
        return os.path.join(self.conf_dir, FILE)

    def _read(self) -> Dict[str, str]:
        try:
            with open(self._path(), encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            return {}
        return {k: v for k, v in doc.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(doc, dict) else {}

    def _write(self, kept: Dict[str, str]) -> None:
        try:
            write_private(self._path(), json.dumps(kept, indent=0))
        except OSError:
            pass                                     # the answer is only kept for next time


def _stat(path: str) -> Optional[Tuple[int, float]]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_size, st.st_mtime)


def _value(args: Sequence[str], *names: str) -> str:
    """The value after the first of these flags ("" when there is none)."""
    for i, a in enumerate(args[:-1]):
        if a in names:
            return args[i + 1]
    return ""
