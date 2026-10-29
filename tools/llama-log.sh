#!/usr/bin/env bash
# Show a llama-server log as plain text (ANSI colour codes stripped).
# The server keeps colours on so the terminal stays readable; the --log-file
# copy therefore contains the colour codes too.
#
#   tools/llama-log.sh            # whole latest log, plain text
#   tools/llama-log.sh -f         # follow the latest log (like tail -f)
#   tools/llama-log.sh [-f] FILE  # a specific log
#   tools/llama-log.sh | grep -E " W | E "   # warnings/errors only
set -euo pipefail
follow=0
[[ "${1:-}" == "-f" ]] && { follow=1; shift; }
f="${1:-$HOME/models/logs/llama-server-latest.log}"
[[ -e "$f" ]] || { echo "no log at $f" >&2; exit 1; }
strip() {
  if command -v ansifilter >/dev/null; then ansifilter
  else sed -E $'s/\x1b\\[[0-9;]*[A-Za-z]//g'; fi   # fallback if ansifilter is missing
}
if (( follow )); then tail -n 50 -F "$f" | strip; else strip < "$f"; fi
