"""The backend of a llama.cpp build that nobody named (decision A72): a build of the user's own (llama.path or
CARL_LLAMA_DIR, a folder with no engine record and no name like bNNNNN-BACKEND) is classed from what it offers, and the
engine record is written beside it, so the next start reads it instead of asking again.

  1. the backend libraries beside llama-server (ggml-cuda, ggml-vulkan, ggml-hip, ggml-metal): the build carries that
     backend (CUDA with its major version when the runtime's file names it: cuda-13);
  2. else `llama-server --list-devices`: a build that links its backend in lists the GPU it runs on; a build that
     runs (exit 0, the "Available devices:" header, no backend failure) and lists none is a CPU build.

A build that cannot run, or whose backend failed to start, is not classed (""). That answer is kept for the process like
any other, so a build that cannot be classed runs once, not at every plan, render and start; a rebuilt or repaired
llama-server (another size or time) is asked again. The record is the install marker's file (release.MARKER) with
"detected": true and the program's size and time, so a rebuilt llama-server is classed again; a folder CARL cannot
write to (or a llama-server found on PATH, whose folder is not CARL's to mark) keeps the answer for this process only.
Contract C-022 (LlamaCpp), C-024.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Callable, Dict, Iterable, Optional, Sequence, Tuple

from ...domain.llama.release import MARKER
from .locator import SERVER, build_backend, find, signature

TIMEOUT = 60.0
_LIB = re.compile(r"^(?:lib)?ggml-(cuda|vulkan|hip|metal)\b", re.IGNORECASE)
_CUDART = re.compile(r"(?:cudart|cublas)(?:64)?_(\d+)\.dll$|libcudart\.so\.(\d+)|libcublas\.so\.(\d+)", re.IGNORECASE)
_DEVICE = re.compile(r"^\s*([A-Za-z_]+?)\d+\s*:\s*.+\(\d+\s*MiB", re.MULTILINE)     # "  CUDA0: NVIDIA ... (8191 MiB, ..."
_HEADER = re.compile(r"^\s*Available devices:", re.MULTILINE | re.IGNORECASE)
_FAILED = re.compile(r"failed to initiali[sz]e|initiali[sz]ation failed|error while loading shared|cannot open shared object"
                     r"|no cuda-capable device|driver version is insufficient|failed to load", re.IGNORECASE)
_NAMES = {"cuda": "cuda", "vulkan": "vulkan", "hip": "rocm", "metal": "metal"}
_DEVICE_BACKENDS = {"cuda": "cuda", "vulkan": "vulkan", "rocm": "rocm", "hip": "rocm", "metal": "metal", "mtl": "metal"}
_ORDER = ("cuda", "rocm", "vulkan", "metal")        # which backend a folder that carries several is classed as
_KNOWN: Dict[Tuple[str, int, int], str] = {}        # (program, size, mtime) -> the answer, for this process


def run_text(args: Sequence[str], timeout: float) -> str:
    """stdout and stderr of a program ("" when it cannot run, times out or exits with an error)."""
    try:
        done = subprocess.run(list(args), capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    if done.returncode != 0:
        return ""
    return done.stdout + done.stderr


def from_files(names: Iterable[str]) -> str:
    """The backend that the files beside llama-server carry ("" when none of them is a GPU backend's). CUDA says its
    major version when a runtime file names it: cuda-13."""
    found, cuda = set(), ""
    for n in sorted(names):                                  # the folder's order is the file system's: not the answer's
        m = _LIB.match(n)
        if m:
            found.add(_NAMES[m.group(1).lower()])
        c = _CUDART.search(n)
        if c:
            cuda = next(g for g in c.groups() if g)
    backend = next((b for b in _ORDER if b in found), "")    # a folder with several backends: the first of _ORDER
    return f"cuda-{cuda}" if backend == "cuda" and cuda else backend


def from_devices(text: str) -> str:
    """The backend of the first GPU in `llama-server --list-devices` output; cpu when the program ran (it printed the
    "Available devices:" header) and lists none; "" when it printed nothing, no header (an error text) or a backend
    failure (a GPU build whose driver cannot start is not a CPU build)."""
    found = _DEVICE.search(text)
    if not found:
        return "" if not _HEADER.search(text) or _FAILED.search(text) else "cpu"
    name = found.group(1).lower()
    return _DEVICE_BACKENDS.get(name, name)


def _record(folder: str, backend: str, sig: Tuple[int, int]) -> None:
    doc = {"backend": backend, "detected": True, "server": {"size": sig[0], "mtime": sig[1]}}
    try:
        with open(os.path.join(folder, MARKER), "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, indent=2)
            f.write("\n")
    except OSError:
        pass                                              # not ours to write to: the answer is kept in memory


def classify(folder: Optional[str], suffix: str = "", run: Callable[[Sequence[str], float], str] = run_text,
             which: Callable[[str], Optional[str]] = lambda n: None, write: bool = True) -> str:
    """The backend of the llama.cpp build at folder (or llama-server on PATH when folder is None, via which): the
    engine record or the folder's name (locator.build_backend), else what the build offers, recorded. "" when the
    build cannot be classed."""
    named = build_backend(folder, suffix)
    if named:
        return named
    program = find(SERVER, folder, suffix, which)
    sig = signature(program) if program else None
    if not program or sig is None:
        return ""
    where = os.path.dirname(os.path.abspath(os.path.expanduser(program)))
    key = (os.path.abspath(program), sig[0], sig[1])
    if key in _KNOWN:
        return _KNOWN[key]
    try:
        got = from_files(os.listdir(where))
    except OSError:
        got = ""
    if not got:
        got = from_devices(run([program, "--list-devices"], TIMEOUT))
    if got and folder and write:
        _record(where, got, sig)
    _KNOWN[key] = got                       # "" too: a build that cannot be classed is not run again, until it changes
    return got
