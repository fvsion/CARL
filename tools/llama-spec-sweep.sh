#!/usr/bin/env bash
# Restart llama-server once per speculative-decoding config and run
# tools/llama-spec-bench.py against each. Logs go to $LOGDIR (default /tmp).
#   tools/llama-spec-sweep.sh "draft-mtp:1" "draft-mtp:2" "ngram-mod:2" ...
set -uo pipefail
cd "$(dirname "$0")/.."
LOGDIR="${LOGDIR:-/tmp}"
PORT="${PORT:-8080}"

stop_server() {
  local pid; pid=$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null)
  [[ -n "$pid" ]] && kill -TERM $pid
  while lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; do sleep 1; done
}

for cfg in "$@"; do
  spec="${cfg%%:*}"; n="${cfg##*:}"
  stop_server
  log="$LOGDIR/llama-${spec//,/+}-$n.log"
  SPEC="$spec" SPEC_N="$n" nohup ./host/serve-llama.sh > "$log" 2>&1 &
  for _ in $(seq 1 150); do
    grep -q -E "listening on|error|failed to" "$log" && break; sleep 2
  done
  if ! grep -q "listening on" "$log"; then
    echo "$spec n=$n FAILED TO START: $(grep -m1 -E 'error|failed' "$log")"; continue
  fi
  python3 tools/llama-spec-bench.py "$spec n=$n" 2>&1 | grep -v "^ "  || echo "$spec n=$n bench error"
done
