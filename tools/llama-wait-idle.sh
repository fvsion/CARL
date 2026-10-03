#!/usr/bin/env bash
# Block until llama-server has been completely idle for IDLE_S seconds
# (slot not processing AND prompt/predicted token counters unchanged).
#   tools/llama-wait-idle.sh [IDLE_S=1200] [BASE=http://192.168.42.1:8080]
# The API key comes from API_KEY_FILE (default ~/.config/llm-deploy/api-key; the
# pre-1.2.0 ~/.mtplx/api-key while the new file does not exist yet).
set -uo pipefail        # no -e: a failed poll counts as "busy" and the wait goes on
IDLE_S="${1:-1200}"; BASE="${2:-http://192.168.42.1:8080}"
[[ "$IDLE_S" =~ ^[0-9]{1,7}$ ]] || { echo "error: IDLE_S must be seconds, got '$IDLE_S'" >&2; exit 2; }
[[ "$BASE" =~ ^https?://[A-Za-z0-9.:-]+(/[A-Za-z0-9._/-]*)?$ ]] || { echo "error: BASE must be http(s)://HOST:PORT, got '$BASE'" >&2; exit 2; }
KEY_FILE="${API_KEY_FILE:-$HOME/.config/llm-deploy/api-key}"
[[ -z "${API_KEY_FILE:-}" && ! -s "$KEY_FILE" && -s "$HOME/.mtplx/api-key" ]] && KEY_FILE="$HOME/.mtplx/api-key"
KEY="$(tr -d '[:space:]' 2>/dev/null < "$KEY_FILE")" || { echo "error: cannot read the API key file $KEY_FILE" >&2; exit 1; }
# The header comes from a file descriptor: the key stays out of curl's command line (ps).
api_get() { curl -fsS -m 5 -H @<(printf 'Authorization: Bearer %s\n' "$KEY") "$BASE$1"; }
last_sig=""; idle_since=$(date +%s)
while true; do
  busy=$(api_get /slots | python3 -c "import sys,json;print(any(s.get('is_processing') for s in json.load(sys.stdin)))" 2>/dev/null || echo err)
  sig=$(api_get /metrics | grep -E '^llamacpp:(prompt_tokens_total|tokens_predicted_total|prompt_tokens_cached_total) ' | tr '\n' ' ')
  now=$(date +%s)
  if [[ "$busy" != "False" || "$sig" != "$last_sig" ]]; then idle_since=$now; last_sig="$sig"; fi
  if (( now - idle_since >= 10#$IDLE_S )); then echo "$(date +%H:%M:%S) idle for $IDLE_S s"; exit 0; fi
  sleep 30
done
