"""The CARL dashboard behind tools/llama-monitor.py.

Dependencies point inward, from the edge to the pure core:

  pure (no I/O)   fmt (text, cards), model (data types), logbook (log parsing), keys (input
                  parsing), settings (Settings rows, config.json mapping, the fit line over
                  carl_core.domain.fit), cards, clients (client config snippets), views (tab
                  bodies), settings_view with settings_panels/ (the Settings panels, one module
                  each), card_form and card_view (the card edit form), arrange (sort and filter),
                  diskcache and clientsync (their rules), state
  port            store.ModelStore: models, config.json and GGUF shapes
  adapters        system (ps, netstat, sysctl, pmset, ...), api (HTTP to the server),
                  fsio (small files), store.CarlStore (tools/carl.py), gguf (carl_core's header
                  reader and GPU limit), collector (one snapshot of the server), jobs (restarts,
                  downloads, Auto-tune), cacheapi (the API for clients on other computers),
                  slotpack (zstd patches), terminal (tty, mouse, logo)
  wiring          app (composition root, frames, main loop), controller (keys and clicks ->
                  actions; settings_actions, card_actions and connect_actions act on the
                  Settings and Connect tabs), cli (arguments and environment)

Run it as tools/llama-monitor.py; tests: python3 -m unittest discover -s tests/monitor -t tests/monitor
"""
