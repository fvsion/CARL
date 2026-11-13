#!/usr/bin/env python3
"""CARL client config sync: applies the client config that the dashboard on the server sends to OpenCode and Pi on
this computer (carl-sync.py help: the commands). The sync is carl_client.app.sync; this file stays at its path,
because installed services (launchd dev.carl.sync, systemd carl-sync.service, the Task Scheduler task) start it.
The bootstrap is _carl_boot.py, shared with carl_setup.py; this file is written for old Pythons too, so that they
can print its version sentence."""
import os
import sys

sys.dont_write_bytecode = True                    # no __pycache__ in the client bundle
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                          # _carl_boot and carl_client, next to this file

import _carl_boot  # noqa: E402


def main(argv):
    code = _carl_boot.start()
    if code:
        return code
    from carl_client.app import sync
    return sync.main(argv, sync.build(HERE))


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(130)
