#!/usr/bin/env python3
"""CARL dashboard: the live monitor for the llama.cpp server.

Starting a server from a terminal (./carl.sh, ./carl.sh llama) shows
this monitor there, with the server running in the background. Quitting asks
whether to stop the server or leave it running; `./carl.sh monitor`
re-attaches later.

Tabs (click, or keys 1-5 / Tab):
  1 Overview  cards: CONNECT, CONTEXT (fill, KV quant, KV RAM), MEMORY, ACTIVITY
              (live speed, prompt ETA), MODEL, HEALTH, SYSTEM, recent requests, log
  2 Connect   Setup: URL, API key, install on this Mac, a VM, push the config,
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
              Auto fit: the best stock model for this Mac, why, the ranking
              Auto-tune: measure a model (or all) on this Mac (tools/carl-tune.py)
              Router: who switches the model (the dashboard, or OpenCode / Pi)
              Caching: the disk cache of prompt states (EXPERIMENTAL)
            Each panel: the controls on the left; a Quick tip and the explanations
            beside them on a wide terminal, below them on a narrow one.

Mouse: click a card title for more detail (once more to collapse it); click
buttons; the wheel scrolls. The footer shows the keys of the panel you see; ? lists them
all. Keys: q or Ctrl-C quit (asks) | k show/hide key |
o / p / t copy OpenCode / Pi / curl | e / c expand / collapse all | w wrap |
f errors only | arrows, PgUp/PgDn scroll | space refresh | ? all keys

  ./carl.sh monitor                      # attach to a running server
  ./carl.sh monitor --port 8081          # a server on another port
  ./carl.sh monitor --once --expand      # print one snapshot and exit

Read-only towards the server (/health, /slots, /metrics, /props, /v1/models
and its log file); the one thing it changes is stopping the server, when you
choose that. Context memory is computed from the GGUF metadata and the
server's flags (tools/gguf_shape.py).
"""
import os
import sys

sys.dont_write_bytecode = True                    # keep the shared folder free of __pycache__
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor.app import main  # noqa: E402  (after the path set-up above)

if __name__ == "__main__":
    main(None, __doc__ or "")
