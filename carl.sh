#!/usr/bin/env bash
# CARL: start the local model server and its dashboard from the project root.
# The same commands, flags and help as host/serve.sh:
#   ./carl.sh                  the dashboard (attach, or start the llama.cpp server)
#   ./carl.sh llama [options]
#   ./carl.sh -h               all commands
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CARL_CMD=./carl.sh exec "$here/host/serve.sh" "$@"
