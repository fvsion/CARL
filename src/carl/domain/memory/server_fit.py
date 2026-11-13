"""The GPU need of a start from llama-server's own fit (E; decision A67: "We can't be under"). Pure.

llama-server with `--fit on` and `-lv 4` prints, before it loads any weight, the memory breakdown of the start's own
parameters: for each device the model, the context (KV and recurrent state) and the compute buffer it will allocate, in
MiB, and a Host row (the RAM):

    common_memory_breakdown_print: |   - CUDA0 (RTX 2070 SUPER) |  8191 = 7131 + (3381 =  2851 +     293 +     237) +  -2321 |
    common_memory_breakdown_print: |   - Host                   |                 2267 =  2192 +       0 +      75          |

It uses the server's own graph and output limits, so its compute buffer is the server's: WESTc measured it exact at 232 of
232 starts (CUDA b11738, Metal 0.6.0; 1 and 2 slots, -ub 512 to 4096, whole GPU and experts in the RAM), never more than the
print's integer floor under. CARL starts the server with the start's real argv on its own local port (argv_of), stops it by
its pid when the log says "fitting params to free memory took" (FIT_DONE; about 1 s), and reads the breakdown (parse).

  need       each number of a device row, plus 1 MiB for the print's floor of each (device_bytes); an MTP head inside the
             model file is the "extra model" block after the main one (its weights are the main model's: only its context
             and compute count); on Metal the Host row's model too (the token embedding: Metal maps the whole file
             into its model buffer, so a real start's MTL0_Mapped buffer is the device's model and the Host's)
  a drafter  in its own file (-md) cannot be measured: llama.cpp needs the target's live context to build its context
             ("failed to measure the memory of the extra model": extra_failed). Its weights are the drafter file, it has no
             KV (it reads the target's last full and window layers), and its compute buffer follows the target's KV cells:
             draft_bound, from the header, with a margin proven on held-out starts. After a first real start CARL reads the
             draft context's buffers from the server log (draft_record) and uses them for the same start again. The
             start does not trust the log's words: with -md, the drafter's record or bound is added unless the extra
             block has a device row (extra_measured: a build that can measure it), and then that block's model counts
             too (device_bytes(drafter)), the drafter's weights.
  before a download  E runs on a sparse stub: the header and tensor table, then a hole up to the real file size (stub_size).
             llama.cpp reads the header and tensor table only and checks each tensor lies inside the file: the breakdown is
             the file's to the MiB (WESTc, 10 of 10).

The source a need came from is named on the start's line and in carl fit (SOURCES): E, E with the drafter bound or
record, stub E (with the drafter bound), or B (CARL's upper bound from the header, domain/memory/calibration.bound, used
where llama-server's fit cannot run: no llama-server with --fit, or no header to make a stub of). CARL's estimate
(calibration.py) still makes the plan and carl fit's table; the start then measures the plan, and a need over the
estimate makes the plan again with that much less room. Auto fit's pick is measured the same way (PickNeed): on its file
when it is downloaded, else on a stub of its header, for carl fit and the catalogue pick (application/fit.py,
FitService.measured_fit).

The dry run (D, domain/memory/dry_run.py) is only a check: on a start whole on the GPU its compute buffer equals E's, so a
gap over 1 MiB means the build changed how it sizes a start (check_line).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import List, Mapping, Optional, Sequence, Tuple

from .fit import window_label

MIB = 2 ** 20
GIB = 2 ** 30
HOST = "Host"
METAL_DEVICE = "MTL"                                        # Metal's devices: MTL0, MTL1
FIT_DONE = "fitting params to free memory took"            # the fit's last line: the breakdown is printed
# Lines of the load that follows the fit: with one of them and no FIT_DONE, the build has no fit for this start (a CPU
# build, no device to fit) and waiting longer gives nothing.
FIT_PAST = ("llama_model_loader: loaded meta data", "load_tensors:")
EXTRA_START = "getting device memory data for the extra model"
EXTRA_FAILED = "failed to measure the memory of the extra model"
LOCAL = "127.0.0.1"
FLOOR = 1                                                   # MiB the print's integer floor can take from each number
VERBOSE = "4"                                               # -lv 4: the breakdown is an INFO line
KEPT_PREFIX = "common_memory_breakdown_print: "             # the rows' lines as a kept answer writes them

E, E_BOUND, E_RECORD, STUB_E, STUB_E_BOUND, B = ("E", "E with the drafter bound", "E with the drafter record", "stub E",
                                                 "stub E with the drafter bound", "B")
SOURCES = (E, E_BOUND, E_RECORD, STUB_E, STUB_E_BOUND, B)

_DEV = re.compile(r"\|\s+- (\S+) \(.*?\)\s*\|\s*-?\d+\s*=\s*-?\d+\s*\+\s*\(\s*-?\d+\s*=\s*(-?\d+)\s*\+\s*(-?\d+)\s*\+"
                  r"\s*(-?\d+)\)")
_HOST = re.compile(r"\|\s+- Host\s*\|\s*\d+\s*=\s*(\d+)\s*\+\s*(\d+)\s*\+\s*(\d+)")
Row = Tuple[str, int, int, int]                             # (device, model, context, compute), MiB


@dataclass(frozen=True)
class Breakdown:
    """What the fit printed: the main block's rows, the extra model's (an MTP head in the file; () none) and whether a
    drafter in its own file could not be measured."""
    rows: Tuple[Row, ...]
    extra: Tuple[Row, ...] = ()
    extra_failed: bool = False

    def devices(self) -> List[Row]:
        return [r for r in self.rows if r[0] != HOST]

    def extra_measured(self) -> bool:
        """The extra model's block has a device row: the fit measured the MTP head in the file, or a drafter in its own
        file (a build that can)."""
        return any(r[0] != HOST for r in self.extra)

    def device_bytes(self, drafter: bool = False) -> int:
        """The need on the devices, bytes: every number of a device row and 1 MiB for the floor of each; the extra
        model's context and compute, and its model too when it is a drafter in its own file (drafter: the start has
        -md; an MTP head's weights are the main model's). On Metal also the Host rows' model: Metal maps the whole file
        into its model buffer (MTL0_Mapped is the device's model and the Host's: the WESTd fix round's real starts), so
        the weights the CPU reads count on the GPU too."""
        metal = any(r[0].startswith(METAL_DEVICE) for r in self.devices())
        mib = sum(m + c + k + 3 * FLOOR for _, m, c, k in self.devices())
        mib += sum((m + FLOOR if drafter else 0) + c + k + 2 * FLOOR for d, m, c, k in self.extra if d != HOST)
        if metal:
            mib += sum(m + FLOOR for d, m, _, _ in self.rows if d == HOST)
            mib += sum(m + FLOOR for d, m, _, _ in self.extra if d == HOST and drafter)
        return mib * MIB

    def text(self) -> str:
        """The breakdown as the lines parse reads (what server-fits.json keeps of a fit's log: its rows, not the
        whole -lv 4 log)."""
        def lines(rows: Sequence[Row]) -> List[str]:
            out = [f"{KEPT_PREFIX}| memory breakdown [MiB] |"]
            for d, m, c, k in rows:
                out.append(f"{KEPT_PREFIX}|   - Host | {m + c + k} = {m} + {c} + {k} |" if d == HOST else
                           f"{KEPT_PREFIX}|   - {d} () | 0 = 0 + ({m + c + k} = {m} + {c} + {k}) + 0 |")
            return out
        out = lines(self.rows)
        if self.extra or self.extra_failed:
            out.append(EXTRA_START)
        out += lines(self.extra) if self.extra else []
        out += [EXTRA_FAILED] if self.extra_failed else []
        return "\n".join(out + [FIT_DONE]) + "\n"

    def compute(self) -> int:
        """The main context's compute buffers on the devices, MiB (D's check compares it)."""
        return sum(r[3] for r in self.devices())


def parse(log: str) -> Optional[Breakdown]:
    """The breakdown from the fit's log; None when it printed no device row (the fit did not run, or no device)."""
    blocks: List[List[Row]] = []
    extra_at, failed, cur = -1, False, None
    for line in log.splitlines():
        if "memory breakdown [MiB]" in line:
            cur = []
            blocks.append(cur)
        elif EXTRA_START in line:
            extra_at, cur = len(blocks), None
        elif EXTRA_FAILED in line:
            failed = True
        elif cur is not None:
            m = _DEV.search(line)
            if m:
                cur.append((m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))))
                continue
            m = _HOST.search(line)
            if m:
                cur.append((HOST, int(m.group(1)), int(m.group(2)), int(m.group(3))))
    if not blocks or not [r for r in blocks[0] if r[0] != HOST]:
        return None
    extra = tuple(blocks[extra_at]) if 0 < extra_at < len(blocks) and not failed else ()
    return Breakdown(tuple(blocks[0]), extra, failed)


# The flags a fit run drops, with the number of values each has: the log goes to the pipe, the verbosity is the fit's.
DROPPED = {"-lv": 1, "--verbosity": 1, "--log-verbosity": 1, "--log-file": 1, "--log-disable": 0}


def argv_of(server_argv: Sequence[str], port: int) -> List[str]:
    """The fit run's arguments (after the program) for a llama-server start with these arguments: the same flags, on
    127.0.0.1 and its own port, --fit on, and -lv 4 so the breakdown is printed."""
    out: List[str] = []
    i, args = 0, list(server_argv)
    while i < len(args):
        a = args[i]
        if a in DROPPED:
            i += 1 + DROPPED[a]
            continue
        if a in ("--host", "--port", "--fit") and i + 1 < len(args):
            out += [a, LOCAL if a == "--host" else str(port) if a == "--port" else "on"]
            i += 2
            continue
        out.append(a)
        i += 1
    if "--host" not in out:
        out += ["--host", LOCAL]
    if "--port" not in out:
        out += ["--port", str(port)]
    if "--fit" not in out:
        out += ["--fit", "on"]
    return out + ["-lv", VERBOSE]


# The flags that do not change a start's memory, with their values: left out of the key (they name files of the start's
# own folders, which move with the home). The model files are named by their size and header (cache_key), not their path.
KEY_DROPPED = {"--api-key-file": 1, "--slot-save-path": 1, "--chat-template-file": 1, "--alias": 1, "-a": 1}
FILE_FLAGS = ("-m", "--model", "-md", "--model-draft")


def key_args(server_argv: Sequence[str]) -> List[str]:
    """The arguments that decide a start's breakdown (the key of an answer): all but the address, the log, the flags
    that do not change the memory, and the model files' paths."""
    out: List[str] = []
    i, args = 0, argv_of(server_argv, 0)
    while i < len(args):
        a = args[i]
        if a in KEY_DROPPED:
            i += 1 + KEY_DROPPED[a]
            continue
        out += [a, "<file>"] if a in FILE_FLAGS and i + 1 < len(args) else [a]
        i += 2 if a in FILE_FLAGS else 1
    return out


def value(args: Sequence[str], *names: str) -> str:
    """The value after the first of these flags ("" when there is none)."""
    for i, a in enumerate(args[:-1]):
        if a in names:
            return args[i + 1]
    return ""


def cache_key(build: str, gpus: str, files: Sequence[Tuple[int, str]], args: Sequence[str]) -> str:
    """The key of an answer: the build, the GPUs (their names and sizes: a settings folder moved to another computer
    asks again), the model files (the model, the drafter: size and a hash of the header) and the flags (key_args)."""
    text = "\n".join([build, gpus] + [f"{s} {h}" for s, h in files] + list(args))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


# ---------------------------------------------------------------- a drafter in its own file
# The draft context's compute buffer over T (the f16 K and V of the target's two shared layers), fitted by linear
# programming (the least total over, none under, every term at least 0, by a development tool) on every
# draft context measured before the fix round's last held-out round (results/WESTd/draft-fit.txt): WESTc's (e-tests.md,
# test 2c), WESTd's first held-out starts and the fix round's rounds 2 and 3 (the 26B-A4B on Metal, -ub 4096 on CUDA, the
# Gemma 4 12B and 31B), 36 on CUDA and 37 on Metal. The terms: bytes per full cell and -ub token, per window cell and
# -ub token, per -ub token and unit of the target's width (embd), MiB per 1024 -ub tokens, MiB fixed, bytes per byte of
# the drafter file, and bytes per -ub token and MiB of the drafter file (the drafters' widths: the 26B-A4B's is four times
# the E4B's; a fit on the E4B alone was 14% under the 26B-A4B on Metal and 30% under the 31B at -ub 4096). CUDA's numbers
# stand for every PC backend (Vulkan and ROCm are not measured).
DRAFT_FIT = {"cuda": (1.77, 0.0, 0.0, 25.73, 0.0, 0.0, 368.6), "metal": (1.82, 0.48, 27.93, 0.0, 0.0, 0.19, 71.13)}
# The margin over the fit: what each backend needs when each model is left out of its fit in turn (CUDA 49.9%, the
# 26B-A4B left out; Metal 30.3%, the 31B), rounded up, set before the fourth held-out round (results/WESTd/held-check.txt).
DRAFT_MARGIN = {"cuda": 0.50, "metal": 0.35}


def draft_bound(kv_full: int, kv_swa: int, window: int, ctx: int, slots: int, ub: int, swa_full: bool,
                backend: str, drafter: int, embd: int) -> float:
    """The compute buffer of a drafter's own context, bytes, an upper bound from the target's header: T = 4 bytes x
    (cells_full x kv_full + cells_swa x kv_swa), kv = the K heads x the key length of the target's full and window layers,
    cells_full = ctx x slots, cells_swa = cells_full with the full cache (or a model without window layers), else
    window x slots + ub; then the terms of DRAFT_FIT (the cells of each cache by -ub, the target's width embd by -ub, -ub
    alone, a fixed part, the drafter file, drafter its bytes, alone and by -ub) and the margin."""
    cells_full = ctx * slots
    cells_swa = cells_full if swa_full or not window else window * slots + ub
    t = 4.0 * (cells_full * kv_full + cells_swa * kv_swa)
    kind = "metal" if backend == "metal" else "cuda"
    a, s, e, b, c, d, g = DRAFT_FIT[kind]
    terms = (a * cells_full * ub + s * cells_swa * ub + e * ub * embd + (b * ub / 1024 + c) * MIB + d * drafter
             + g * ub * drafter / MIB)
    return max(t + terms, 0.0) * (1 + DRAFT_MARGIN[kind])


WIDE = 16384                    # the target's width when its header gave none (no tensor table): wider than the catalogue's


def draft_bound_of(shape: Mapping[str, object], ctx: int, slots: int, ub: int, swa_full: bool, backend: str,
                   drafter: int) -> float:
    """draft_bound with the target's widths from its header shape (the K widths of its full and window layers, its
    window, its width embd)."""
    def num(key: str) -> int:
        v = shape.get(key, 0)
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0
    return draft_bound(num("attn_elems") // 2, num("attn_elems_swa") // 2, num("swa_window"), ctx, slots, ub, swa_full,
                       backend, drafter, num("embd") or WIDE)


_LOAD_DRAFT = "loading draft model"
_SPEC_ADDED = "adding speculative implementation"
_BUF = re.compile(r"(\S+) (model|KV|compute) buffer size\s*=\s*([\d.]+) MiB")


def draft_record(log: str) -> Optional[int]:
    """The draft context's buffers on the devices from a real start's server log, bytes: its model buffers, any KV and
    the largest compute buffer, from "loading draft model" to the reserve after "adding speculative implementation";
    1 MiB more for the print's two decimals. None when the log has no draft context."""
    model = kv = compute = 0.0
    seen = done = False
    after_spec = False
    for line in log.splitlines():
        if _LOAD_DRAFT in line:
            seen = True
            continue
        if not seen or done:
            continue
        if _SPEC_ADDED in line:
            after_spec = True
        m = _BUF.search(line)
        if m and not m.group(1).startswith(("CPU", "CUDA_Host", "Metal_Host", "BLAS")) and HOST not in m.group(1):
            mib = float(m.group(3))
            if m.group(2) == "model":
                model += mib
            elif m.group(2).lower() == "kv":
                kv += mib
            else:
                compute = max(compute, mib)
                if after_spec:
                    done = True
    if not seen or not compute:
        return None
    return int((model + kv + compute + 1) * MIB)


# ---------------------------------------------------------------- before a download: a stub
def stub_size(head_size: int, file_size: int) -> int:
    """The size of a stub: the real file's (a header-only file fails llama.cpp's check that each tensor lies inside the
    file). Raises ValueError when the header is not shorter than the file."""
    if not 0 < head_size < file_size:
        raise ValueError(f"a header of {head_size} bytes cannot stand for a file of {file_size}")
    return file_size


# ---------------------------------------------------------------- the lines
def need_line(source: str, need: float, devices: Sequence[str], gap: float) -> str:
    """The start's line about its GPU need: the source, the need and, when CARL's estimate was under it, by how much
    (the plan was made again with that much less room). B: llama-server's fit could not run, so the need is B's bound."""
    where = f" on {', '.join(devices)}" if devices else ""
    text = f"Memory: the GPU need is {need / MIB:.0f} MiB{where}, from {SOURCE_WORDS[source]} ({source})"
    text += ": llama-server's own fit could not run." if source == B else "."
    if gap > 0:
        text += (f" CARL's estimate was {gap / MIB:.0f} MiB under it, so the plan was made with that much less room.")
    return text


SOURCE_WORDS = {E: "llama-server's own fit", E_BOUND: "llama-server's own fit and the drafter's bound from the header",
                E_RECORD: "llama-server's own fit and the drafter's buffers from an earlier start",
                STUB_E: "llama-server's own fit on the model's header",
                STUB_E_BOUND: "llama-server's own fit on the model's header and the drafter's bound from the header",
                B: "CARL's upper bound from the header"}


def stub_name(repo: str, revision: str, file: str) -> str:
    """The name a stub's answer is kept by: the file on Hugging Face at its revision (a pinned revision never changes)."""
    return f"{repo}@{revision or 'main'}/{file}"


@dataclass(frozen=True)
class PickNeed:
    """Auto fit's pick measured (A67 (2)): the need on its device (bytes), its source (SOURCES), CARL's estimate of the
    same plan (bytes), the room a model can use there (bytes) and how much less room the pick was made with (bytes; 0:
    the first pick held)."""
    need: float
    source: str
    estimate: float
    room: float
    less: float = 0.0

    @property
    def fits(self) -> bool:
        return self.need <= self.room

    def sentence(self) -> str:
        """carl fit's words on the pick's need: the need and its source, and what Auto fit did when the estimate was
        under it."""
        text = f"It needs {self.need / GIB:.1f} GiB, from {SOURCE_WORDS[self.source]} ({self.source})"
        text += ": llama-server's own fit could not run on it." if self.source == B else "."
        if self.less > 0:
            text += (f" CARL's estimate was under it, so Auto fit chose again with {self.less / GIB:.1f} GiB less "
                     f"room.")
        if not self.fits:
            text += (f" That is more than the {self.room / GIB:.1f} GiB a model can use: a start makes its plan again "
                     f"with less room.")
        return text


@dataclass(frozen=True)
class RowFit:
    """A row of carl fit's table measured (A72): the largest window per slot that fits by llama-server's own fit, the
    layers whose experts stay in the RAM at that window (0: the model is whole on the GPU), and the need there (the fit's
    need, its source, CARL's estimate of the same setup, the room)."""
    ctx: int
    cpu_moe: int
    need: PickNeed

    def source(self) -> str:
        """The source a table's note names: E, stub E, either with the drafter's bound."""
        return self.need.source


def row_line(name: str, fits: Sequence[Tuple[str, Optional["RowFit"], int]], reason: str = "") -> str:
    """carl fit's sentence on one row asked for: the window of each context memory type by the fit (kv, its RowFit, CARL's
    estimate of the window; label), else why the row stays an estimate."""
    got = [(kv, f, est) for kv, f, est in fits if f is not None]
    if not got:
        return f"{name}: not measured, so the table's figures stay CARL's estimate. {reason}".rstrip()
    parts = [f"{window_label(f.ctx)} at {kv} (CARL's estimate: {window_label(est)})" for kv, f, est in got]
    text = f"{name}: llama-server's own fit says {' and '.join(parts)}, from {SOURCE_WORDS[got[0][1].need.source]} " \
           f"({got[0][1].need.source})."
    if len(got) < len(fits):
        text += f" The other context memory type stays CARL's estimate. {reason}"
    return text.rstrip()


def check_line(dry_compute: int, fit_compute: int) -> str:
    """D's check against E on a start whole on the GPU ("" when they agree within the print's floor)."""
    gap = dry_compute - fit_compute
    if abs(gap) <= FLOOR:
        return ""
    return (f"Memory: llama.cpp's dry run says {dry_compute} MiB of compute buffer and llama-server's own fit {fit_compute} "
            f"MiB ({abs(gap)} MiB {'over' if gap > 0 else 'under'}): this build sizes a start differently from the one "
            f"CARL was measured with. The need is the fit's.")
