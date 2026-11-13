"""`carl router-preset --out FILE --templates DIR [--port PORT]` (hidden): router mode's presets INI, and with --port
the server file of each model's cache; it prints "model NAME SETUP", "skip NAME: WHY" and "start NAME" lines.
Contract C-414. It reads no environment (R6), so THREAD_PIN does not reach it: a model pins by its tune
record alone."""
from __future__ import annotations

from typing import List

from ...domain.server.router_presets import preset_ini
from .base import Cli, arg

USAGE = "router-preset --out FILE --templates DIR"


def run(cli: Cli, a: List[str]) -> int:
    out = arg(a, a.index("--out") + 1, USAGE) if "--out" in a else arg([], 0, USAGE)
    tdir = arg(a, a.index("--templates") + 1, USAGE) if "--templates" in a else arg([], 0, USAGE)
    api = cli.api()
    preset, common = api.carl.router_preset(api.carl.load_config(), tdir)
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(preset_ini(preset, common, cli.words))
    if "--port" in a:                       # the server file for this computer's clients (Phase 23.5)
        port = int(arg(a, a.index("--port") + 1, USAGE + " [--port PORT]"))
        cli.tools.server_file().write_router(port, preset)
    for p in preset.models:
        print(f"model {p.name} {p.describe()}")
    for n, why in preset.skipped:
        print(f"skip {n}: {why}")
    if preset.start:
        print(f"start {preset.start}")
    return 0
