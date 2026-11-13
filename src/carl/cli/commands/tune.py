"""`carl tune NAME|all [--quick | --long] [--port 8093] [--dry-run]`: Auto-tune one model for this computer, or every
downloaded model one after another (a failure does not stop the rest).

Steps (the model loads once per speculation mode, about 10-20 min in all; application/tune.py runs them):
  1. memory: the largest window that fits with 1 and 2 slots (the fit's model)
  2. speculation: none, n-gram, and (when the file has an MTP head) MTP and MTP + n-gram at n = 1 and 2, or (with a
     separate MTP drafter, Gemma 4: passed with -md) at n = 1 to 4 (--quick: 1 and 2). Each runs prose, fresh code
     and a code re-emit, twice. Score = weighted geometric mean (prose 0.4, code 0.4, re-emit 0.2); a mode with
     drafting must beat a simpler one by 3% to win.
  3. prompt reading: a cold read at 8K, 32K and 64K tokens (--quick: 8K and 32K; --long: also 128K and 192K, and the
     decode speed at each depth). The read time for a full window gives this computer's context zones: a cold
     re-read of the whole window within 3 min = fast, 10 min = slow, beyond = very slow. On the CPU alone the reads
     stop at 8K tokens (2K, 4K and 8K), and the longer ones are an estimate from their slope, said as one (A71).
  1b. context memory type (the CPU alone, A71): llama.cpp reads prompts 2 to 3 times faster on the CPU with an f16
     context memory than with q8_0 or q4_0, and the RAM usually covers it. Each type that fits the RAM (f16, q8_0,
     q4_0; the free RAM less the load reserve, as a start judges it) starts the model at its -ub and reads 8K tokens
     cold, then writes; the type that reads or writes 3% faster without losing on the other is saved, f16 first, and
     every later step runs with it. A start of the model uses the saved type.
  2b. batch size (every OS and depth, A45): a cold read of 8K tokens at -ub 512, 1024, 2048 and 4096, each a start of
     the model, for the sizes that keep the setup -ub 512 gets (2 x 96K first, 2 x 128K on NVIDIA; nothing below
     48K); the fastest wins by 5% over the default (2048 on a PC, 512 on a Mac) and is saved in the model's record (a
     -ub typed in the server options wins; a saved choice of the model's or llama.ub is asked about once, A51)
  2c. MTP with experts in the RAM (a model with an MTP head whose chosen setup keeps any expert layer in the RAM,
     A51): the best MTP mode and n-gram are measured at that setup and -ub; MTP stays in the record only when it beats
     n-gram by 3%, else the record says n-gram (a start with experts in the RAM runs n-gram by default)
  2d. threads with experts in the RAM (a CPU whose cores can be read and a chosen setup that keeps any expert layer in
     the RAM, A54): the chosen speculation and -ub run twice with the threads pinned one per physical core and twice
     unpinned, alternating; pinned goes in the record only when it writes 3% faster, and the start follows the record
     (a start with no record runs unpinned, A57)
  4. result: kv q4_0 (on the CPU alone the type step 1b chose), the best speculation, the context, 2 slots when two
     windows fit, the -ub and the load mode the reads ran with (each test start takes load mode none when the free RAM
     covers it and the load reserve, as carl start does). Saved to ~/.config/carl/models.json (models.NAME.tune) with
     the llama.cpp build and the backend it ran on (A72: a start on another build or backend keeps the settings and
     calls the result an estimate); every start of NAME uses it unless config.json sets a value.

It needs the GPU to itself: it refuses to run while another model is loaded (stop the server first; the dashboard's
Auto-tune panel does that). Progress lines start with "STEP i/N" (the dashboard shows them); the last line is
"DONE {settings}" or "error: ...". --dry-run measures but does not save. The composition gives it its server, the
ports, the processes and the host's words (TuneTools). Contract C-409.
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Set, Tuple

from ...application.tune import AutoTuner
from ...domain.catalog.drafts import KIND_OF_SPEC, KIND_WORDS
from ...domain.catalog.inventory import mtp_source, offered_drafts
from ...domain.llama.spec_support import unused_note
from ...domain.llama.version import parse_version
from ...domain.config.settings import Config
from ...domain.hardware.processes import ProcessInfo, blocking_processes
from ...domain.shared.constants import DEFAULT_PORT, UBATCH
from ...domain.shared.errors import ConfigError
from ...domain.shared.types import ModelInfo, TuneRecord
from ...domain.tune.progress import done_line, model_line
from ...domain.tune.scoring import TunePlan
from ...ports import HostInfo, Progress, TuneServer
from .base import Cli
from .fit import help_action

GUARDED_PORTS = (DEFAULT_PORT, 8080)           # the llama.cpp server CARL starts (8080 before 23.5)
DEFAULT_BIG_GB = 8


@dataclass
class TuneTools:
    """What Auto-tune runs with, from the composition:
      listening       the ports something listens on (the PortProbe's: a server in the way)
      processes       the processes (the ProcessTable's: another model in memory)
      host            this computer's processor and RAM, and on a PC its GPUs (the HostInfo port: the record's
                      machine, W6.9)
      cpu             the processor's name alone (the first line)
      llama_version   the "version:" line of llama-server
      server          the test server of a model file on a port (stopped at the end)
      progress        the STEP and note lines
      allow_second    ALLOW_SECOND_MODEL=1; big_gb: BIG_GB as set (None: not set)
      pid             this process (not a model in the way)
      edit_workload   the frozen copy of the re-emit workload (src/carl/data/tune-edit-workload.txt)
      today           the date of the record
      ubs             the batch sizes (-ub) to measure the prompt read at (every OS, A45)
      ub_default      this OS's -ub before a tune (2048 on a PC, 512 on a Mac)"""
    listening: Callable[[], Set[int]]
    processes: Callable[[], List[ProcessInfo]]
    host: HostInfo
    cpu: Callable[[], str]
    llama_version: Callable[[], str]
    server: Callable[[str, int], TuneServer]
    progress: Progress
    allow_second: bool
    big_gb: Optional[str]
    pid: int
    edit_workload: str
    today: Callable[[], str]
    ubs: Tuple[int, ...] = ()
    ub_default: int = UBATCH


def parse_args(cli: Cli, argv: Sequence[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog=f"{cli.words.command} tune", add_help=False)
    ap.add_argument("-h", "--help", action=help_action(cli, "tune"))
    ap.add_argument("model", help="a downloaded model, or all (every downloaded model in turn)")
    ap.add_argument("--port", type=int, default=8093)
    depth = ap.add_mutually_exclusive_group()
    depth.add_argument("--quick", action="store_true",
                       help="skip the MTP modes at n=2 (n=3 and 4 with an MTP drafter) and the 64K read (~4 min)")
    depth.add_argument("--long", action="store_true", help="also read 128K and 192K cold and measure the decode speed "
                                                            "at each depth (+10-40 min)")
    ap.add_argument("--dry-run", action="store_true", help="measure and print the result, don't save it")
    return ap.parse_args(list(argv))


def downloaded_model(cli: Cli, name: str) -> ModelInfo:
    m = cli.api().find(name)
    cmd = cli.words.command
    if not m:
        raise ConfigError(f"unknown model '{name}'. {cmd} models lists the models.")
    if m.get("status") != "downloaded":
        raise ConfigError(f"{m.get('name')} is not downloaded. To download it: {cmd} download {m.get('name')}")
    return m


def guard_gpu(t: TuneTools, port: int) -> None:
    """Refuse while a server or another large process holds memory: two models don't fit."""
    busy = t.listening()
    for p in GUARDED_PORTS + (port,):
        if p in busy:
            raise ConfigError(f"a server runs on port {p}. Stop it first: two models do not fit in the memory.")
    if t.allow_second:
        return
    try:
        big_gb = int(t.big_gb if t.big_gb is not None else DEFAULT_BIG_GB)
    except ValueError:
        raise ConfigError("BIG_GB must be a whole number of GB.") from None
    big = blocking_processes(t.processes(), big_gb * 2 ** 20, t.pid)
    if big:
        raise ConfigError("another large process (possibly a model) is in memory: " + "; ".join(big)
                          + ". Stop it first. If it is not a model, set ALLOW_SECOND_MODEL=1.")


def edit_workload(t: TuneTools) -> str:
    with open(t.edit_workload, encoding="utf-8") as f:
        return f.read()


def save(cli: Cli, m: ModelInfo, record: TuneRecord) -> None:
    """models.NAME.tune in models.json (a custom model keeps its path and source there)."""
    api = cli.api()
    db = api.load_local()
    entry = db["models"].setdefault(m.get("name", ""), {})
    if m.get("custom"):
        entry.setdefault("path", m.get("path", ""))
        entry.setdefault("source", m.get("source", "file"))
    entry["tune"] = record
    api.save_local(db)


def run_one(cli: Cli, t: TuneTools, args: argparse.Namespace, server: TuneServer, m: ModelInfo) -> None:
    api = cli.api()
    path = m.get("path", "")
    shape = api.shape_of(path)
    machine = t.cpu()
    ram_gb = t.host.ram_bytes() // 2 ** 30
    source = mtp_source(m, shape)
    mtp = {"head": "an MTP head in the file", "drafter": f"the MTP drafter {os.path.basename(m.get('draft_path', ''))}",
           "none": "no MTP (the MTP drafter is not downloaded)" if m.get("draft") else "no MTP"}[source]
    print(f"Auto-tune measures {m.get('name')} on {cli.words.machine} ({machine}, {ram_gb} GiB of RAM). The model: "
          f"{os.path.basename(path)}, {shape['ftype']}, {'MoE' if shape['experts'] else 'dense'}, {mtp}.", flush=True)
    guard_gpu(t, args.port)
    edit_source = edit_workload(t)
    base_ctx = api.carl.effective_tune(m, Config())[0]["ctx"]         # catalogue / header window, no config.json
    if not isinstance(base_ctx, int):
        raise ConfigError(f"{m.get('name')}: the context of the model is not a number of tokens")
    drafter = source == "drafter"           # the launcher passes it with -md for the MTP modes
    weights = os.path.getsize(path) + (os.path.getsize(m.get("draft_path", "")) if drafter else 0)
    kv_room = api.carl.fit.tune_kv(shape, weights)       # the CPU alone (A71): the RAM is the limit, and the types are measured
    limit = int(api.budget().allowed) if kv_room is not None else api.gpu_limit()[0]
    engine = parse_version(t.llama_version())
    offered, left = offered_drafts(m, engine)       # the drafter files of other kinds: tried, or said not used (A74)
    unused = tuple(unused_note(str(m.get("name")), KIND_WORDS[KIND_OF_SPEC[str(d.get("spec"))]], engine) for d in left)
    plan = TunePlan(shape=shape, weights=weights, limit=limit, base_ctx=base_ctx,
                    kinds=tuple(str(d.get("spec")) for d in offered), unused=unused,
                    kind_bytes=tuple((str(d.get("spec")), int(d.get("bytes", 0))) for d in offered),
                    depth="quick" if args.quick else "long" if args.long else "default",
                    date=t.today(), machine=t.host.machine(), llama_cpp=t.llama_version(),
                    edit_source=edit_source, drafter=drafter, ubs=t.ubs, ub_default=t.ub_default,
                    room=api.carl.fit.tune_room(shape, weights), ram_layers=api.carl.fit.tune_ram(shape, weights),
                    pin=api.carl.fit.thread_pin(), tiers=api.budget().tiers, kv_room=kv_room,
                    backend=api.carl.fit.start_backend())
    record = AutoTuner(server, t.progress, cli.words).run(plan)
    if not args.dry_run:
        save(cli, m, record)
        t.progress.note(f"Saved in {cli.tilde(cli.local_file)}. The next start of {m.get('name')} uses it.")
    print(done_line(json.dumps(record.get("settings", {}))), flush=True)


def tune_one(cli: Cli, t: TuneTools, args: argparse.Namespace, m: ModelInfo) -> None:
    """Tune one model with its own test server (stopped at the end, also on an error)."""
    server = t.server(m.get("path", ""), args.port)
    try:
        run_one(cli, t, args, server, m)
    finally:
        server.stop()


def tune_all(cli: Cli, t: TuneTools, args: argparse.Namespace) -> int:
    """Every downloaded model in turn ("MODEL k/N NAME" before each); a model that fails is reported
    and the rest still run. 0 when every one was tuned."""
    models = [m for m in cli.api().all_models() if m.get("status") == "downloaded"]
    if not models:
        raise ConfigError(f"no model is downloaded. To download one: {cli.words.command} download NAME")
    failed = []
    for k, m in enumerate(models, 1):
        print(model_line(k, len(models), str(m.get("name"))), flush=True)
        try:
            tune_one(cli, t, args, m)
        except KeyboardInterrupt:
            raise
        except Exception as e:                # the next model still runs
            print(f"error: {m.get('name')}: {e}", flush=True)
            failed.append(str(m.get("name")))
    tuned = len(models) - len(failed)
    print(done_line(f"ALL {tuned} of {len(models)} tuned" + (f"; failed: {', '.join(failed)}" if failed else "")),
          flush=True)
    return 0 if not failed else 1


def run(cli: Cli, argv: Sequence[str]) -> int:
    args = parse_args(cli, argv)
    try:
        t = cli.tools.tune()
        if args.model == "all":
            return tune_all(cli, t, args)
        tune_one(cli, t, args, downloaded_model(cli, args.model))
        return 0
    except KeyboardInterrupt:
        print("cancelled", flush=True)
        return 130
    except Exception as e:                    # the dashboard shows the last "error: ..." line
        print(f"error: {e}", flush=True)
        return 1
