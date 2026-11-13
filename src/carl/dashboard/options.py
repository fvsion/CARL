"""Command-line arguments and the environment variables the dashboard reads."""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from ..composition.dashboard import host as dashboard_host
from ..domain.config.settings import LLAMA_KEYS
from ..domain.server.runtime_doc import DEFAULT_PORT
from ..domain.shared.constants import VM_ADDR
from ..ui.words import computer


HELP = """CARL dashboard: the live monitor for the llama.cpp server.

Starting a server from a terminal (carl alone, or its start command) shows
this monitor there, with the server running in the background. Quitting asks
whether to stop the server or leave it running; `carl monitor`
re-attaches later.

Tabs (click, or keys 1-5 / Tab):
  1 Overview  cards: CONNECT, CONTEXT (fill, KV quant, KV RAM), MEMORY, ACTIVITY
              (live speed, prompt ETA), MODEL, HEALTH, SYSTEM, recent requests, log
  2 Connect   Setup: URL, API key, install on {this}, a VM, push the config,
              the OpenCode / Pi config and curl test copied to the clipboard;
              Clients: every computer that syncs ([ and ] switch)
  3 Requests  every finished request with its speeds
  4 Log       full server log: scroll, wrap, errors only
  5 Settings  six panels ([ and ] switch):
              Server: model, KV cache, context, slots, speculation, RAM cache,
                network, sampling; the fit line; Apply restarts the server (the
                old one starts again if the new one fails). The MODEL card says
                what the model is for and why each value is tuned that way
              Models: catalogue + every .gguf in the models folder; download,
                verify, delete, add any GGUF from Hugging Face
              Auto fit: the best stock model for {this}, why, the ranking
              Auto-tune: measure a model (or all) on {this}
              Router: who switches the model (the dashboard, or OpenCode / Pi)
              Caching: the disk cache of prompt states (EXPERIMENTAL)
            Each panel: the controls on the left; a Quick tip and the explanations
            beside them on a wide terminal, below them on a narrow one.

Mouse: click a card title for more detail (once more to collapse it); click
buttons; the wheel scrolls. The footer shows the keys of the panel you see; ? lists them
all. Keys: q or Ctrl-C quit (asks) | k show/hide key |
o / p / t copy OpenCode / Pi / curl | e / c expand / collapse all | w wrap |
f errors only | arrows, PgUp/PgDn scroll | space refresh | ? all keys

  carl monitor                      # attach to a running server
  carl monitor --port 8081          # a server on another port
  carl monitor --once --expand      # print one snapshot and exit

The dashboard reads the server's API (/health, /slots, /metrics, /props,
/v1/models) and its log file. It changes the server only when you tell it to:
it stops the server, or it starts the server again with new settings (Apply,
Auto-tune). Before a stop, it saves the sessions in the slots
(POST /slots/N?action=save). In router mode, it loads and unloads models
(POST /models/load and /models/unload). It also serves its own API on the
server's port + 1 for OpenCode and Pi on other computers (the disk cache and
the client config). This API uses the same API key as the server. The
dashboard calculates the context memory from the GGUF metadata and the
server's flags.
"""


def help_text() -> str:
    """The text of --help (the old dashboard script's docstring); the computer is the platform's word."""
    return HELP.replace("{this}", computer())


@dataclass
class Options:
    """The command line and environment, read once at start."""
    host: Optional[str]         # --host: the server address (default: wherever it listens)
    port: int
    lines: int                  # log lines on the Live tab (full detail)
    interval: float             # seconds between refreshes
    log: Optional[str]
    server_pid: Optional[int]   # the launcher's server
    console: Optional[str]      # its console output file
    once: bool
    tab: int                    # --once: which tab (1-5; 0 = Live)
    expand: bool
    # from the environment
    home: str = ""
    demo: bool = False          # CARL_DEMO=1: README screenshots hide the key and the home path
    key_file: str = ""          # API_KEY_FILE, else ~/.config/carl/api-key (carl.domain.config.key_paths)
    env_host: Optional[str] = None      # HOST
    vm_addr: str = VM_ADDR              # VM_HOST


HOST_VAR = str(LLAMA_KEYS["host"].env)       # the variable of llama.host (the settings schema names it)


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    v = env.get(name, "")
    return int(v) if v.strip().isdigit() else default


def parse(argv: Optional[Sequence[str]], description: str, env: Mapping[str, str]) -> Options:
    """Options from argv (None: sys.argv) and env; --help prints description."""
    ap = argparse.ArgumentParser(description=description, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=None, help="server address (default: wherever the server on --port listens)")
    ap.add_argument("--port", type=int, default=_env_int(env, "PORT", DEFAULT_PORT), help=f"server port (default {DEFAULT_PORT})")
    ap.add_argument("--lines", type=int, default=0, help="log lines on the Live tab (default: by the screen's height)")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between refreshes (default 2)")
    ap.add_argument("--log", default=None, help="log file (default: the running server's --log-file)")
    ap.add_argument("--server-pid", type=int, default=None, help="PID of the server to watch (set by the launcher)")
    ap.add_argument("--owner", action="store_true", help=argparse.SUPPRESS)     # set by the launcher; nothing reads it
    ap.add_argument("--console", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--once", action="store_true", help="print one snapshot and exit")
    ap.add_argument("--tab", type=int, default=0, choices=range(0, 6), metavar="N", help=argparse.SUPPRESS)
    ap.add_argument("--expand", action="store_true", help="every section at its full level for this run")
    a = ap.parse_args(argv)
    home = os.path.expanduser("~")
    return Options(host=a.host, port=a.port, lines=a.lines, interval=a.interval, log=a.log, server_pid=a.server_pid,
                   console=a.console, once=a.once, tab=a.tab, expand=a.expand,
                   home=home, demo=env.get("CARL_DEMO") == "1",
                   key_file=dashboard_host(env).key_file(home),
                   env_host=env.get(HOST_VAR) or None, vm_addr=env.get("VM_HOST", VM_ADDR))
