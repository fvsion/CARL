"""carl_client: the client package (OpenCode and Pi on any computer that uses a CARL server). Standard library only:
it ships alone in the client zip and never imports the server's code (rule R8).

domain/    the shared kernel and the merge rules: the switch vocabulary, the model entries, the thinking rules (the
           server imports these), the OpenCode and Pi merges, the legacy names, the server's place. Pure.
data/      the values the domain takes (thinking.json, legacy.json, server.json): no values in code (decision 12)
ports.py   what the app needs from the computer
adapters/  files, the package's data, the dashboard API, the lock, launchd, systemd, the shell profile
app/       configure (the configs) and sync (carl-sync.py)

The entries are client/carl_setup.py and client/carl-sync.py next to this folder. Importing a module runs nothing.
"""
