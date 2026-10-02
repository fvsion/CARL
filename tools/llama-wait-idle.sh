#!/usr/bin/env bash
# Block until llama-server has been completely idle for IDLE_S seconds
# (slot not processing AND prompt/predicted token counters unchanged).
#   tools/llama-wait-idle.sh [IDLE_S=1200] [BASE=http://192.168.42.1:8080]
set -uo pipefail
IDLE_S="${1:-1200}"; BASE="${2:-http://192.168.42.1:8080}"
KEY=$(cat ~/.mtplx/api-key); H="Authorization: Bearer $KEY"
last_sig=""; idle_since=$(date +%s)
while true; do
  busy=$(curl -fsS -m 5 -H "$H" "$BASE/slots" | python3 -c "import sys,json;print(any(s.get('is_processing') for s in json.load(sys.stdin)))" 2>/dev/null || echo err)
  sig=$(curl -fsS -m 5 -H "$H" "$BASE/metrics" | grep -E '^llamacpp:(prompt_tokens_total|tokens_predicted_total|prompt_tokens_cached_total) ' | tr '\n' ' ')
  now=$(date +%s)
  if [[ "$busy" != "False" || "$sig" != "$last_sig" ]]; then idle_since=$now; last_sig="$sig"; fi
  if (( now - idle_since >= IDLE_S )); then echo "$(date +%H:%M:%S) idle for $IDLE_S s"; exit 0; fi
  sleep 30
done
