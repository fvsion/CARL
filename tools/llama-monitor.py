#!/usr/bin/env python3
"""CARL dashboard: the live monitor for the llama.cpp server.

Starting a server from a terminal (./carl.sh, ./carl.sh llama) shows
this monitor there, with the server running in the background. Quitting asks
whether to stop the server or leave it running; `./carl.sh monitor`
re-attaches later.

Tabs (click, or keys 1-5 / Tab):
  1 Overview  cards: CONNECT, CONTEXT (fill, KV quant, KV RAM), MEMORY, ACTIVITY
              (live speed, prompt ETA), MODEL, HEALTH, SYSTEM, recent requests, log
  2 Connect   URL, API key, setup steps, OpenCode / Pi config and curl test,
              shown and copied to the clipboard
  3 Requests  every finished request with its speeds
  4 Log       full server log: scroll, wrap, errors only
  5 Settings  three panels ([ and ] switch):
              Server: model (press Enter for a drop-down of every model; auto =
                auto fit's pick, ★), auto goal (everyday / hard-code), auto from
                (catalogue / downloaded), KV cache, context, slots, speculation, RAM
                cache, network, sampling. Press A for Auto fit: model, context, slots
                and KV for this Mac in one step (it offers the download of a pick that
                isn't here). The MODEL card explains the model, why auto fit picked
                it, and why each value is tuned that way; values
                are coloured (green tuned / fast, yellow changed / slower, red very
                slow). Saved to ~/.config/carl/config.json, applied by a
                restart (the old server starts again if the new one fails)
              Models: catalogue + every .gguf in the models folder; download,
                verify, delete, add any GGUF from Hugging Face
              Auto-tune: measure a model on this Mac (tools/carl-tune.py)

Mouse: click a card title for more detail (once more to collapse it); click
buttons; the wheel scrolls. Keys: q or Ctrl-C quit (asks) | k show/hide key |
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
