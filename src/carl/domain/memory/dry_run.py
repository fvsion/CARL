"""llama.cpp's own no-alloc dry run of a start (D), a check of llama-server's own fit (A57, A64, A67). Pure.

`llama-fit-params --fit-print on` takes a model file and the flags of a start and prints, for each device, the model, the
context (KV and recurrent state) and the compute buffer it would allocate, in MiB, without allocating them:

    CUDA0 1603 242 501
    Host 11491 0 48

A57 tried it as the source of the GPU need: its compute buffer counts the logits of every -ub token, so it is 1.7 to 3.5
times the server's (results/WEST/dryrun.md). A64's D runs it with the output weight off the GPU and no op offload
(-ot <output>=CPU --no-op-offload, launch_plan.dry_args): on a start whole on the GPU its compute buffer is the server's
to the MiB, but with experts in the RAM it is under at 119 of 144 starts (results/WESTc/d-heldout.md): it does not build
the server's staged copy of the experts. A67 makes llama-server's own fit (E, domain/memory/server_fit.py) the source;
D runs beside it on a start whole on the GPU, and a gap over 1 MiB is a line on the start (a changed build), never the
need. A58: the dry run has no --kv-unified, so for a start with it the dry run gets --parallel 1 and the same -c, one
stream of the whole pool, which is the server's mask (argv_of).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

MIB = 2 ** 20
HOST = "Host"
# The flags of a llama-server start that the dry run takes, with the number of values each has. The server-only flags
# (the host, the port, --kv-unified, the checkpoints, the cache, the metrics, the log) are left out.
TAKEN = {"-m": 1, "--model": 1, "-ngl": 1, "--n-gpu-layers": 1, "--gpu-layers": 1, "-ncmoe": 1, "--n-cpu-moe": 1,
         "-dev": 1, "--device": 1, "-ts": 1, "--tensor-split": 1, "-sm": 1, "--split-mode": 1, "-mg": 1, "--main-gpu": 1,
         "-ctk": 1, "--cache-type-k": 1, "-ctv": 1, "--cache-type-v": 1, "-c": 1, "--ctx-size": 1, "-b": 1,
         "--batch-size": 1, "-ub": 1, "--ubatch-size": 1, "-np": 1, "--parallel": 1, "-ot": 1, "--override-tensor": 1,
         "-fa": 1, "--flash-attn": 1, "--swa-full": 0, "-nkvo": 0, "--no-kv-offload": 0, "--no-op-offload": 0}


@dataclass(frozen=True)
class DryRun:
    """What the dry run printed: (device, model, context, compute) in MiB for each device; the Host row is the RAM."""
    rows: Tuple[Tuple[str, int, int, int], ...]

    def devices(self) -> List[Tuple[str, int, int, int]]:
        return [r for r in self.rows if r[0] != HOST]

    def compute(self) -> int:
        """The compute buffers on the devices, MiB (0: no device printed)."""
        return sum(r[3] for r in self.devices())


def parse(text: str) -> Optional[DryRun]:
    """The dry run's rows from its stdout (the lines `NAME model context compute`; the log goes to stderr); None when
    it printed none."""
    rows = []
    for line in text.splitlines():
        words = line.split()
        if len(words) == 4 and all(w.isdigit() for w in words[1:]):
            rows.append((words[0], int(words[1]), int(words[2]), int(words[3])))
    return DryRun(tuple(rows)) if rows else None


def argv_of(server_argv: Sequence[str]) -> List[str]:
    """The dry run's arguments (after the program) for a llama-server start with these arguments: the flags it takes, in
    their order, then --fit-print on. With --kv-unified (one pool of -c tokens for all slots) the dry run, which has no such
    flag, gets --parallel 1: one stream of the whole pool has the pool's mask, where one stream per slot is smaller."""
    out: List[str] = []
    i, args = 0, list(server_argv)
    unified = "--kv-unified" in args
    while i < len(args):
        n = TAKEN.get(args[i])
        if n is None:
            i += 1
            continue
        if unified and args[i] in ("-np", "--parallel"):
            out += [args[i], "1"]
        else:
            out += args[i:i + 1 + n]
        i += 1 + n
    return out + ["--fit-print", "on"]


def cache_key(build: str, size: int, mtime: float, args: Sequence[str]) -> str:
    """The key of a dry run's answer: the build, the model file (its size and time) and the flags that matter (-ub, the
    pool, the placement are all in them)."""
    text = "\n".join([build, str(size), f"{mtime:.0f}"] + list(args))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
