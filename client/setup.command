#!/bin/bash
# CARL client setup for a double click in the Finder (macOS opens a .command file in Terminal).
# It runs ./setup in this folder, then waits, so that you can read what it did.
cd "$(dirname "$0")" || exit 1
bash ./setup "$@"
status=$?
echo
read -r -p "Press Enter to close this window. " _ || true
exit "$status"
