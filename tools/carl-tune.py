#!/usr/bin/env python3
"""Auto-tune one model for this Mac: speculative decoding and context window.

  ./carl.sh tune NAME [--quick | --long] [--port 8093]
  ./carl.sh tune all [...]      every downloaded model, one after another (a failure doesn't stop the rest)

Steps (the model loads once per speculation mode, ~5-10 min in all):
  1. memory: the largest window that fits with 1 and 2 slots (tools/llama-fit.py's model)
  2. speculation: none, n-gram, and (when the file has an MTP head) MTP and MTP + n-gram
     at n = 1 and 2. Each runs prose, fresh code and a code re-emit, twice. Score =
     weighted geometric mean (prose 0.4, code 0.4, re-emit 0.2); a mode with drafting
     must beat a simpler one by 3% to win.
  3. prompt reading: a cold read at 8K, 32K and 64K tokens (--quick: 8K and 32K; --long: also
     128K and 192K, and the decode speed at each depth). The
     read time for a full window gives this Mac's context zones: a cold re-read of the
     whole window within 3 min = fast, 10 min = slow, beyond = very slow.
  4. result: kv q4_0, the best speculation, the context (96K when it fits; above that the
     catalogue's window while a cold read of it is no worse than "slow" here and it fits,
     else the largest standard window in the fast zone), 2 slots when two windows fit.
     Saved to ~/.config/carl/models.json (models.NAME.tune); every start of NAME
     uses it unless config.json sets a value (the monitor's Settings tab).

Needs the GPU to itself: refuses to run while another model is loaded
(stop the server first; the monitor's Auto-tune panel does that for you).
Progress lines start with "STEP i/N" (the monitor shows them); the last line is
"DONE {settings}" or "error: ...". --dry-run measures but doesn't save.
"""
import os
import sys

sys.dont_write_bytecode = True                    # keep the shared folder free of __pycache__
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse  # noqa: E402
import json  # noqa: E402
from typing import List  # noqa: E402

import carl  # noqa: E402
from carl_core.adapters import llama_server  # noqa: E402
from carl_core.adapters.api_key import key_file, read_key  # noqa: E402
from carl_core.adapters.console import StepPrinter  # noqa: E402
from carl_core.adapters.system import (listening_ports, llama_server_version, processes, sysctl_int,  # noqa: E402
                                       sysctl_text)
from carl_core.domain.errors import ConfigError  # noqa: E402
from carl_core.domain.settings import Config  # noqa: E402
from carl_core.domain.tuning import AutoTuner, TunePlan, blocking_processes  # noqa: E402
from carl_core.domain.types import ModelInfo, TuneRecord  # noqa: E402

GUARDED_PORTS = (8080,)                        # the llama.cpp server CARL starts
DEFAULT_BIG_GB = 8


def parse_args(argv: List[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model", help="a downloaded model, or all (every downloaded model in turn)")
    ap.add_argument("--port", type=int, default=8093)
    depth = ap.add_mutually_exclusive_group()
    depth.add_argument("--quick", action="store_true", help="skip the MTP modes at n=2 and the 64K read (~4 min)")
    depth.add_argument("--long", action="store_true", help="also read 128K and 192K cold and measure the decode speed "
                                                            "at each depth (+10-40 min)")
    ap.add_argument("--dry-run", action="store_true", help="measure and print the result, don't save it")
    return ap.parse_args(argv)


def downloaded_model(name: str) -> ModelInfo:
    m = carl.find(name)
    if not m:
        raise ConfigError(f"unknown model '{name}' (./carl.sh models)")
    if m.get("status") != "downloaded":
        raise ConfigError(f"{m.get('name')} is not downloaded (./carl.sh download {m.get('name')})")
    return m


def guard_gpu(port: int) -> None:
    """Refuse while a server or another large process holds memory: two models don't fit."""
    busy = listening_ports()
    for p in GUARDED_PORTS + (port,):
        if p in busy:
            raise ConfigError(f"a server is running on port {p}: stop it first (two models don't fit in memory)")
    if os.environ.get("ALLOW_SECOND_MODEL") == "1":
        return
    try:
        big_gb = int(os.environ.get("BIG_GB", DEFAULT_BIG_GB))
    except ValueError:
        raise ConfigError("BIG_GB must be a whole number of GB") from None
    big = blocking_processes(processes(), big_gb * 2 ** 20, os.getpid())
    if big:
        raise ConfigError("another large process (probably a model) is in memory: " + "; ".join(big)
                          + ". Stop it first, or set ALLOW_SECOND_MODEL=1 if it is not a model.")


def save(m: ModelInfo, record: TuneRecord) -> None:
    """models.NAME.tune in models.json (a custom model keeps its path and source there)."""
    db = carl.load_local()
    entry = db["models"].setdefault(m.get("name", ""), {})
    if m.get("custom"):
        entry.setdefault("path", m.get("path", ""))
        entry.setdefault("source", m.get("source", "file"))
    entry["tune"] = record
    carl.save_local(db)


def run(args: argparse.Namespace, server: llama_server.LlamaServerControl, m: ModelInfo) -> None:
    app = carl.app()
    path = m.get("path", "")
    shape = app.shapes.local(path)
    machine = sysctl_text("machdep.cpu.brand_string")
    ram_gb = sysctl_int("hw.memsize") // 2 ** 30
    has_mtp = bool(shape["nextn"])
    print(f"Auto-tune {m.get('name')} on {machine} {ram_gb} GB ({os.path.basename(path)}, {shape['ftype']}, "
          f"{'MoE' if shape['experts'] else 'dense'}, MTP head: {'yes' if has_mtp else 'no'})", flush=True)
    guard_gpu(args.port)
    # the re-emit workload copies this code back (it has a `call` method the prompt renames)
    with open(llama_server.__file__, encoding="utf-8") as f:
        edit_source = f.read()
    base_ctx = app.effective_tune(m, Config())[0]["ctx"]       # catalogue / header window, no config.json
    if not isinstance(base_ctx, int):
        raise ConfigError(f"{m.get('name')}: the tuned ctx is not a token count")
    plan = TunePlan(shape=shape, weights=os.path.getsize(path), limit=app.gpu.limit()[0], base_ctx=base_ctx,
                    depth="quick" if args.quick else "long" if args.long else "default",
                    date=app.clock.today(), machine=f"{machine} {ram_gb} GB", llama_cpp=llama_server_version(),
                    edit_source=edit_source)
    progress = StepPrinter()
    record = AutoTuner(server, progress).run(plan)
    if not args.dry_run:
        save(m, record)
        progress.note(f"saved to {carl.LOCAL_FILE.replace(os.path.expanduser('~'), '~')} "
                      f"(used by the next start of {m.get('name')})")
    print("DONE " + json.dumps(record.get("settings", {})), flush=True)


def tune_one(args: argparse.Namespace, m: ModelInfo) -> None:
    """Tune one model with its own test server (stopped at the end, also on an error)."""
    server = llama_server.LlamaServerControl(
        launcher=os.path.join(carl.REPO, "host", "serve-llama.sh"), model_path=m.get("path", ""), port=args.port,
        api_key=read_key(key_file()), log_path=os.path.expanduser(f"~/models/logs/.tune-{args.port}.out"),
        listening=listening_ports)
    try:
        run(args, server, m)
    finally:
        server.stop()


def tune_all(args: argparse.Namespace) -> int:
    """Every downloaded model in turn ("MODEL k/N NAME" before each); a model that fails is reported
    and the rest still run. 0 when every one was tuned."""
    models = [m for m in carl.all_models() if m.get("status") == "downloaded"]
    if not models:
        raise ConfigError("no model is downloaded (./carl.sh download NAME)")
    failed = []
    for k, m in enumerate(models, 1):
        print(f"MODEL {k}/{len(models)} {m.get('name')}", flush=True)
        try:
            tune_one(args, m)
        except KeyboardInterrupt:
            raise
        except Exception as e:                # the next model still runs
            print(f"error: {m.get('name')}: {e}", flush=True)
            failed.append(str(m.get("name")))
    tuned = len(models) - len(failed)
    print(f"DONE ALL {tuned} of {len(models)} tuned" + (f"; failed: {', '.join(failed)}" if failed else ""), flush=True)
    return 0 if not failed else 1


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    try:
        if args.model == "all":
            return tune_all(args)
        tune_one(args, downloaded_model(args.model))
        return 0
    except KeyboardInterrupt:
        print("cancelled", flush=True)
        return 130
    except Exception as e:                    # the monitor shows the last "error: ..." line
        print(f"error: {e}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
