"""The CARL dashboard behind tools/llama-monitor.py.

Dependencies point inward, from the edge to the pure core:

  pure (no I/O)   fmt (text, cards), model (data types), logbook (log parsing), keys (input
                  parsing), settings (Settings rows, config.json mapping, fit maths), cards,
                  clients (client config snippets), views and settings_view (tab bodies), state
  port            store.ModelStore: models, config.json and GGUF shapes
  adapters        system (ps, netstat, sysctl, pmset, ...), api (HTTP to the server),
                  fsio (small files), store.CarlStore (tools/carl.py), gguf (tools/gguf_shape.py),
                  collector (one snapshot of the server), jobs (restarts, downloads,
                  Auto-tune), terminal (tty, mouse, logo)
  wiring          app (composition root, frames, main loop), controller (keys and clicks ->
                  actions), cli (arguments and environment)

Run it as tools/llama-monitor.py; tests: python3 -m unittest discover -s tests/monitor -t tests/monitor
"""
