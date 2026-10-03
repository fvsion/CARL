#!/usr/bin/env bash
# A/B llama-server KV cache types and -ub batch size, then restore the default
# server. Restarting wipes the server's cached session prompt, so run it only
# when no session is active:
#
#   tools/llama-ab.sh [kv|ub|all]                     (default: all)
#   tools/llama-wait-idle.sh 1200 && tools/llama-ab.sh  (wait for 20 min idle)
#
# kv  For each KV_CONFIGS entry ("K:V"): restart, record idle RSS (the full-ctx
#     KV is allocated up front, so this is the exact memory cost), then
#     tools/llama-kv-longctx.py at LONG_TOKENS context:
#       t1 cold prefill + recall of 8 needles at 2..98% depth (quality proxy:
#          q4 K is the risky one for long-range recall)
#       t2 append + 400-token decode at long context (the overnight workload
#          was 76% decode time, mostly at 60-118K ctx)
#       t3 re-emit a function from deep in the context (n-gram path)
#     plus tools/llama-spec-bench.py (short-context decode: prose/code/edit).
#     ~25-30 min per config at 64K (cold prefill ~50 tok/s dominates).
# ub  For each UB_CONFIGS entry: restart with UB_KV (default q4_0) KV and measure a cold ~12K
#     prefill (tools/llama-ab-measure.py). ~5 min per config.
#
# Env: KV_CONFIGS="q8_0:q8_0 q8_0:q4_0 q4_0:q4_0"  UB_CONFIGS="512 1024 2048"
#      LONG_TOKENS=65536  LOGDIR=~/models/logs
# Results are also written to $LOGDIR/ab-<timestamp>.txt.
set -uo pipefail
cd "$(dirname "$0")/.."
source host/common.sh   # port_pid (netstat-based)
MODE="${1:-all}"
PORT=8080
LOGDIR="${LOGDIR:-$HOME/models/logs}"; mkdir -p "$LOGDIR"
KV_CONFIGS="${KV_CONFIGS:-q8_0:q8_0 q8_0:q4_0 q4_0:q4_0}"
UB_CONFIGS="${UB_CONFIGS:-512 1024 2048}"
LONG_TOKENS="${LONG_TOKENS:-65536}"
UB_KV="${UB_KV:-q4_0}"
RESULTS="$LOGDIR/ab-$(date +%Y%m%d-%H%M%S).txt"
exec > >(tee -a "$RESULTS") 2>&1

stop() { local p; p=$(port_pid "$PORT"); [[ -n "$p" ]] && kill -TERM $p
         while [[ -n "$(port_pid "$PORT")" ]]; do sleep 1; done; }
start() { local name=$1; shift
          env LOG_FILE="$LOGDIR/ab-$name.log" "$@" nohup ./host/serve-llama.sh > "$LOGDIR/ab-$name.out" 2>&1 &
          for _ in $(seq 1 150); do grep -q -E "listening on|error|failed to" "$LOGDIR/ab-$name.out" && break; sleep 2; done
          grep -q "listening on" "$LOGDIR/ab-$name.out"; }
idle_rss() { ps -o rss= -p "$(port_pid "$PORT")" | awk '{printf "%.2fG", $1/1048576}'; }

echo "== llama-ab $MODE $(date '+%F %T')  kv=[$KV_CONFIGS] ub=[$UB_CONFIGS] long=$LONG_TOKENS"

if [[ "$MODE" == kv || "$MODE" == all ]]; then
  for cfg in $KV_CONFIGS; do
    kk="${cfg%%:*}"; kv="${cfg##*:}"; label="K$kk/V$kv"; name="kv-$kk-$kv"
    stop
    if ! start "$name" KV_K="$kk" KV_V="$kv"; then echo "$label FAILED: $(grep -m1 -E 'error|failed' "$LOGDIR/ab-$name.out")"; continue; fi
    sleep 5; echo "$label idle_rss $(idle_rss) (full ${LONG_TOKENS}+ ctx KV allocated at 131072)"
    python3 tools/llama-kv-longctx.py "$label" "$LONG_TOKENS" || echo "$label longctx error"
    python3 tools/llama-spec-bench.py "$label" || echo "$label bench error"
  done
fi

if [[ "$MODE" == ub || "$MODE" == all ]]; then
  for ub in $UB_CONFIGS; do
    label="ub$ub K$UB_KV/V$UB_KV"; name="ub-$ub"
    stop
    if ! start "$name" UB="$ub" KV_K="$UB_KV" KV_V="$UB_KV"; then echo "$label FAILED: $(grep -m1 -E 'error|failed' "$LOGDIR/ab-$name.out")"; continue; fi
    sleep 5; echo "$label idle_rss $(idle_rss)"
    python3 tools/llama-ab-measure.py "$label" || echo "$label measure error"
  done
fi

stop
nohup ./host/serve.sh llama > "$LOGDIR/llama-server-nohup.out" 2>&1 &
for _ in $(seq 1 150); do grep -q "listening on" "$LOGDIR/llama-server-nohup.out" && break; sleep 2; done
echo "== restored default server: $(grep -m1 '^model=' "$LOGDIR/llama-server-nohup.out")"
echo "== results: $RESULTS"
